"""LLM 网关 API（V3 Wave B-a / I0）—— 模型管理页的后端。

状态码约定（契约 §1.3，别混）：

- **501** 一个 provider 都没配 → 功能未启用
- **503** 配了但连不上 / key 无效 → 依赖挂了

`/test` 会**真实发一次最小请求**，不是 ping base_url —— key 错、模型名错、
额度耗尽都只有真调一次才知道，而这三样正是用户最容易配错的。

密钥安全现状见 `app/core/llm/config_store.py` 的模块 docstring。
本模块的硬约束：**任何响应体都只出现掩码，完整 key 绝不外泄。**
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.auth import UserInfo
from app.core.audit import AuditAction, audit_log
from app.core.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    ChatMessage,
    LLMNotConfiguredError,
    LLMUnavailableError,
    ToolSpec,
)
from app.core.llm.config_store import (
    ProviderConfig,
    clear_active,
    delete_config,
    load_active,
    load_all_configs,
    save_active,
    save_config,
)
from app.core.llm.registry import PRESET_IDS, PROVIDER_PRESETS, ProviderPreset, get_preset
from app.core.llm.service import is_ready, resolve_active, resolve_named
from app.core.rbac import Role, require_role
from app.core.redis import get_redis

router = APIRouter()

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
# 写入端点要 Trader 及以上（与券商配置一致：会用真实密钥调外部 API / 花钱）
TraderDep = Annotated[UserInfo, Depends(require_role(Role.TRADER))]
# 对话入口只要求「已登录」——Copilot 面向所有角色，但不对匿名开放
AuthedDep = Annotated[UserInfo, Depends(require_role(Role.VIEWER))]

_MAX_TEMPERATURE = 2.0
_MAX_TOKENS_LIMIT = 32_000
_MAX_TIMEOUT_SECONDS = 300.0


# ── Schemas ──────────────────────────────────────────────────────────────────

class ProviderStatus(BaseModel):
    """一张 provider 卡片的全部信息。key 永远只以掩码出现。"""

    id: str
    label: str
    kind: Literal["openai_compat", "anthropic"]
    requires_key: bool
    doc_url: str
    suggested_models: list[str]
    supports_model_listing: bool
    default_base_url: str
    # ── 运行期状态 ──
    configured: bool           # 保存过配置
    ready: bool                # 配置完整到可以直接发请求
    base_url: str | None = None
    default_model: str | None = None
    key_hint: str | None = None   # 掩码，如 "sk••••••••1234"


class ActiveModel(BaseModel):
    configured: bool
    provider_id: str | None = None
    provider_label: str | None = None
    model: str | None = None
    explicit: bool = False    # False = 系统按预设顺序自动挑的


class ProvidersResponse(BaseModel):
    providers: list[ProviderStatus]
    active: ActiveModel


class SaveProviderRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500, description="API 根地址")
    default_model: str = Field(
        min_length=1, max_length=200,
        description="默认模型名。不做白名单校验 —— 用户填什么就是什么。",
    )
    api_key: str | None = Field(
        default=None,
        max_length=500,
        description="留空 = 保持原值（不是清空）。清空请用 DELETE。",
    )


class SetActiveRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)


class TestResult(BaseModel):
    ok: bool
    provider_id: str
    model: str | None = None
    latency_ms: float | None = None
    error: str | None = None


class ModelListResponse(BaseModel):
    provider_id: str
    models: list[str]


class ChatMessageIn(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


class ToolSpecIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1)
    tools: list[ToolSpecIn] | None = None
    temperature: float = Field(default=DEFAULT_TEMPERATURE, ge=0.0, le=_MAX_TEMPERATURE)
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=1, le=_MAX_TOKENS_LIMIT)
    timeout: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=_MAX_TIMEOUT_SECONDS)
    # 单次覆盖模型，不改全局 active
    model: str | None = Field(default=None, max_length=200)


class ToolCallOut(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatResponseOut(BaseModel):
    content: str
    tool_calls: list[ToolCallOut]
    provider_id: str
    model: str
    usage: dict[str, int]


# ── 端点：配置 ────────────────────────────────────────────────────────────────

@router.get("/providers", response_model=ProvidersResponse)
async def list_providers(redis: RedisDep) -> ProvidersResponse:
    """预设清单 + 各自配置状态。**只回掩码**，完整 key 不出现在响应里。"""
    configs = await load_all_configs(redis, PRESET_IDS)
    providers = [_to_status(preset, configs.get(preset.id)) for preset in PROVIDER_PRESETS]
    return ProvidersResponse(providers=providers, active=await _describe_active(redis))


@router.put("/providers/{provider_id}", response_model=ProviderStatus)
async def save_provider(
    provider_id: str,
    body: SaveProviderRequest,
    redis: RedisDep,
    user: TraderDep,
) -> ProviderStatus:
    """保存 provider 配置。需 Trader 及以上。api_key 留空 = 保持原值。"""
    preset = _require_preset(provider_id)
    config = await save_config(
        redis,
        provider_id,
        base_url=body.base_url.strip(),
        default_model=body.default_model.strip(),
        api_key=(body.api_key or "").strip() or None,
    )
    await audit_log(
        AuditAction.LLM_CONFIG_SAVE,
        actor=user.email or user.id,
        detail={
            "provider": provider_id,
            "base_url": config.base_url,
            "model": config.default_model,
            "key_hint": config.masked_key,   # 审计也只记掩码
        },
        redis=redis,
    )
    return _to_status(preset, config)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_provider(provider_id: str, redis: RedisDep, user: TraderDep) -> None:
    """删除 provider 配置（含密钥）。需 Trader 及以上。"""
    _require_preset(provider_id)
    await delete_config(redis, provider_id)

    # 生效选择指向被删的 provider 时一并清掉，避免残留一个指向空气的 active
    active = await load_active(redis)
    if active is not None and active.provider_id == provider_id:
        await clear_active(redis)

    await audit_log(
        AuditAction.LLM_CONFIG_DELETE,
        actor=user.email or user.id,
        detail={"provider": provider_id},
        redis=redis,
    )


@router.post("/providers/{provider_id}/test", response_model=TestResult)
async def test_provider(
    provider_id: str,
    redis: RedisDep,
    _user: TraderDep,
    model: str | None = None,
) -> TestResult:
    """
    连通性测试：**真实发一次最小对话请求**。需 Trader 及以上（会用真实密钥）。

    返回 200 + ok=false 而不是 503 —— 「测试失败」本身就是这个端点的正常结果，
    前端要拿到具体错误文案展示给用户。
    """
    try:
        resolved = await resolve_named(redis, provider_id, model_override=model)
    except LLMNotConfiguredError as exc:
        return TestResult(ok=False, provider_id=provider_id, model=model, error=str(exc))

    health = await resolved.provider.health()
    return TestResult(
        ok=health.ok,
        provider_id=provider_id,
        model=health.model,
        latency_ms=health.latency_ms,
        error=health.error,
    )


@router.get("/providers/{provider_id}/models", response_model=ModelListResponse)
async def list_provider_models(provider_id: str, redis: RedisDep) -> ModelListResponse:
    """拉取该端点当前可用的模型名（Ollama 本地拉了什么只有它自己知道）。"""
    preset = _require_preset(provider_id)
    if not preset.supports_model_listing:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{preset.label} 不支持列举模型。",
        )
    try:
        resolved = await resolve_named(redis, provider_id)
        models = await resolved.provider.list_models()
    except LLMNotConfiguredError as exc:
        raise _not_configured(exc) from exc
    except LLMUnavailableError as exc:
        raise _unavailable(exc) from exc
    return ModelListResponse(provider_id=provider_id, models=list(models))


# ── 端点：生效选择 ────────────────────────────────────────────────────────────

@router.get("/active", response_model=ActiveModel)
async def get_active(redis: RedisDep) -> ActiveModel:
    """当前生效的 provider 与模型。未配置时回 configured=false（不是 501 —— 这是状态查询）。"""
    return await _describe_active(redis)


@router.put("/active", response_model=ActiveModel)
async def set_active(body: SetActiveRequest, redis: RedisDep, user: TraderDep) -> ActiveModel:
    """切换生效的 provider/model。需 Trader 及以上。模型名不做白名单校验。"""
    preset = _require_preset(body.provider_id)
    await save_active(redis, body.provider_id, body.model.strip())
    await audit_log(
        AuditAction.LLM_ACTIVE_SWITCH,
        actor=user.email or user.id,
        detail={"provider": body.provider_id, "model": body.model},
        redis=redis,
    )
    return ActiveModel(
        configured=True,
        provider_id=preset.id,
        provider_label=preset.label,
        model=body.model.strip(),
        explicit=True,
    )


# ── 端点：统一对话入口 ────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponseOut)
async def chat(body: ChatRequest, redis: RedisDep, _user: AuthedDep) -> ChatResponseOut:
    """统一对话入口（供 I1 Copilot 与 I4/I5 复用）。非流式。"""
    try:
        resolved = await resolve_active(redis, model_override=body.model)
        response = await resolved.provider.chat(
            [ChatMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id)
             for m in body.messages],
            tools=[ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
                   for t in body.tools] if body.tools else None,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            timeout=body.timeout,
        )
    except LLMNotConfiguredError as exc:
        raise _not_configured(exc) from exc
    except LLMUnavailableError as exc:
        raise _unavailable(exc) from exc

    return ChatResponseOut(
        content=response.content,
        tool_calls=[ToolCallOut(id=c.id, name=c.name, arguments=c.arguments)
                    for c in response.tool_calls],
        provider_id=resolved.preset.id,
        model=response.model or resolved.model,
        usage=response.usage,
    )


# ── 内部工具 ─────────────────────────────────────────────────────────────────

def _require_preset(provider_id: str) -> ProviderPreset:
    preset = get_preset(provider_id)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未知的模型服务商：{provider_id}",
        )
    return preset


def _not_configured(exc: Exception) -> HTTPException:
    """501：功能未启用 —— 一个 provider 都没配。"""
    return HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))


def _unavailable(exc: Exception) -> HTTPException:
    """503：功能启用了但依赖挂了 —— 连不上 / key 无效 / 模型不存在。"""
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


def _to_status(preset: ProviderPreset, config: ProviderConfig | None) -> ProviderStatus:
    base = ProviderStatus(
        id=preset.id,
        label=preset.label,
        kind=preset.kind,
        requires_key=preset.requires_key,
        doc_url=preset.doc_url,
        suggested_models=list(preset.suggested_models),
        supports_model_listing=preset.supports_model_listing,
        default_base_url=preset.default_base_url,
        configured=config is not None,
        ready=config is not None and is_ready(config, preset),
    )
    if config is None:
        return base
    return base.model_copy(
        update={
            "base_url": config.base_url,
            "default_model": config.default_model,
            "key_hint": config.masked_key,
        }
    )


async def _describe_active(redis: aioredis.Redis) -> ActiveModel:
    """描述当前生效选择。无可用 provider 时返回 configured=false 而不是抛错。"""
    try:
        resolved = await resolve_active(redis)
    except LLMNotConfiguredError:
        return ActiveModel(configured=False)
    return ActiveModel(
        configured=True,
        provider_id=resolved.preset.id,
        provider_label=resolved.preset.label,
        model=resolved.model,
        explicit=resolved.explicit,
    )
