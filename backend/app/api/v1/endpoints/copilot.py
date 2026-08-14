"""平台 Copilot API（V3 Wave B-c / I1）

    POST /api/v1/copilot/chat            对话（工具调用在服务端完成）
    POST /api/v1/copilot/drafts/execute  确认执行一张草稿
    GET  /api/v1/copilot/tools           工具清单（含各自是否需要确认，供前端与审计查看）

**未配置模型时不甩 501 给用户**：网关抛 `LLMNotConfiguredError` 时这里回 200 +
`needs_setup=true` + 指向 `/settings/models` 的链接。对用户来说「还没配模型」是一句
引导语，不是一个错误码。provider 配了但连不上（503）同理，回一句人话 + `error_kind`。

**草稿执行需要 Trader 及以上**：直接调用既有端点函数会绕过它自己的
`Depends(require_role(...))`，所以这里必须自己声明同等权限，不能只靠下游。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import UserInfo
from app.copilot import (
    COPILOT_TOOLS,
    CopilotContext,
    CopilotDraft,
    CopilotOutcome,
    DraftExecutionError,
    execute_draft,
    run_copilot_chat,
)
from app.core.database import get_db
from app.core.llm import ChatMessage, LLMNotConfiguredError, LLMUnavailableError, resolve_active
from app.core.rbac import Role, require_role
from app.core.redis import get_redis

router = APIRouter()

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
SessionDep = Annotated[AsyncSession, Depends(get_db)]
AuthedDep = Annotated[UserInfo, Depends(require_role(Role.VIEWER))]
TraderDep = Annotated[UserInfo, Depends(require_role(Role.TRADER))]

#: 未配置模型时引导用户去的页面
SETUP_URL = "/settings/models"

ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"

_MAX_HISTORY_MESSAGES = 40
_MAX_MESSAGE_CHARS = 8000


# ── Schemas ──────────────────────────────────────────────────────────────────

class CopilotMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)


class CopilotChatRequest(BaseModel):
    messages: list[CopilotMessageIn] = Field(min_length=1, max_length=_MAX_HISTORY_MESSAGES)
    model: str | None = Field(default=None, max_length=200, description="单次覆盖模型")


class CardOut(BaseModel):
    kind: str
    title: str
    data: dict[str, Any]


class DraftFieldOut(BaseModel):
    label: str
    value: str
    emphasis: bool = False


class DraftOut(BaseModel):
    draft_id: str
    tool: str
    action: str
    title: str
    summary: str
    fields: list[DraftFieldOut]
    params: dict[str, Any]
    endpoint: str
    method: str
    legs: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CopilotChatResponse(BaseModel):
    text: str
    cards: list[CardOut] = Field(default_factory=list)
    drafts: list[DraftOut] = Field(default_factory=list)
    rounds: int = 0
    truncated: bool = False
    needs_setup: bool = False
    setup_url: str | None = None
    provider_id: str | None = None
    model: str | None = None
    error_kind: str | None = None


class DraftExecuteRequest(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=32)
    params: dict[str, Any]


class DraftExecuteResponse(BaseModel):
    action: str
    result: dict[str, Any]


class ToolInfo(BaseModel):
    name: str
    description: str
    requires_confirmation: bool


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(_user: AuthedDep) -> list[ToolInfo]:
    """工具清单。`requires_confirmation` 是动作边界的可见形态。"""
    return [
        ToolInfo(
            name=tool.name,
            description=tool.description,
            requires_confirmation=tool.requires_confirmation,
        )
        for tool in COPILOT_TOOLS
    ]


@router.post("/chat", response_model=CopilotChatResponse)
async def chat(
    body: CopilotChatRequest,
    redis: RedisDep,
    session: SessionDep,
    _user: AuthedDep,
) -> CopilotChatResponse:
    """一次问答。只读工具直接执行，写动作只产出草稿。"""
    try:
        resolved = await resolve_active(redis, model_override=body.model)
    except LLMNotConfiguredError as exc:
        return _setup_guidance(str(exc))
    except LLMUnavailableError as exc:
        return _unavailable(str(exc), None, None)

    ctx = CopilotContext(session=session, redis=redis)
    messages = [ChatMessage(role=m.role, content=m.content) for m in body.messages]
    try:
        outcome = await run_copilot_chat(resolved.provider, messages, ctx=ctx)
    except LLMUnavailableError as exc:
        return _unavailable(str(exc), resolved.preset.id, resolved.model)
    return _to_response(outcome, resolved.preset.id, resolved.model)


@router.post("/drafts/execute", response_model=DraftExecuteResponse)
async def execute_draft_endpoint(
    body: DraftExecuteRequest, user: TraderDep
) -> DraftExecuteResponse:
    """确认执行一张草稿。执行走既有端点，权限与手动操作一致（Trader 及以上）。"""
    try:
        result = await execute_draft(
            draft_id=body.draft_id, action=body.action, params=body.params, user=user
        )
    except DraftExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return DraftExecuteResponse(**result)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _setup_guidance(hint: str) -> CopilotChatResponse:
    """把网关的 501 翻成一句引导语 + 一个能点的链接。"""
    return CopilotChatResponse(
        text=f"{hint}\n\n配置完成后回到这里再问一次即可。",
        needs_setup=True,
        setup_url=SETUP_URL,
    )


def _unavailable(
    detail: str, provider_id: str | None, model: str | None
) -> CopilotChatResponse:
    """把网关的 503 翻成一句人话 —— 「依赖挂了」和「功能没开」是两个排查方向。"""
    return CopilotChatResponse(
        text=f"模型服务当前不可用：{detail}\n\n请到「模型管理」检查地址、密钥与模型名。",
        provider_id=provider_id,
        model=model,
        error_kind=ERROR_PROVIDER_UNAVAILABLE,
        setup_url=SETUP_URL,
    )


def _to_response(
    outcome: CopilotOutcome, provider_id: str, model: str
) -> CopilotChatResponse:
    return CopilotChatResponse(
        text=outcome.text,
        cards=[CardOut(kind=c.kind, title=c.title, data=c.data) for c in outcome.cards],
        drafts=[_draft_out(d) for d in outcome.drafts],
        rounds=outcome.rounds,
        truncated=outcome.truncated,
        provider_id=provider_id,
        model=outcome.model or model,
        error_kind=outcome.error_kind,
    )


def _draft_out(draft: CopilotDraft) -> DraftOut:
    return DraftOut(
        draft_id=draft.draft_id,
        tool=draft.tool,
        action=draft.action,
        title=draft.title,
        summary=draft.summary,
        fields=[
            DraftFieldOut(label=f.label, value=f.value, emphasis=f.emphasis)
            for f in draft.fields
        ],
        params=draft.params,
        endpoint=draft.endpoint,
        method=draft.method,
        legs=list(draft.legs),
        warnings=list(draft.warnings),
    )
