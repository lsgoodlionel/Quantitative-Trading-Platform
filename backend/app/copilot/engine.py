"""Copilot 对话引擎（V3 Wave B-c / I1）

一次 `/copilot/chat` 的完整编排：调模型 → 分发工具 → 结果回灌 → 收尾成人话。

三个必须处理的现实（契约 §2.1）都在这里：

1. **本地小模型会吐非法 JSON 的 tool arguments**。网关把原文留在 `{"__raw__": ...}`
   而不是静默丢弃；这里识别它，回一句人话的错误 —— 既不抛 500，也不假装调用成功。
2. **工具调用可能连环**（查行情 → 再回测）。`MAX_TOOL_ROUNDS` 是硬上限，
   超了就停下并如实说明，不无限循环烧 token。
3. **草稿产出后不再让模型继续调工具**：否则同一个意图可能被重复草拟成两张卡片。

本模块不碰 HTTP，也不解析 provider 配置 —— 那是端点的事。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.copilot.context import CopilotContext, ToolExecutionError
from app.copilot.drafts import CopilotDraft
from app.copilot.prompt import SYSTEM_PROMPT, TOOL_CALL_PREFIX, TOOL_RESULT_PREFIX
from app.copilot.tools import COPILOT_TOOLS, CopilotTool, tool_specs
from app.core.llm import ChatMessage, LLMProvider, ToolCall

#: 工具连环调用的轮次上限。超过就停下如实说明。
MAX_TOOL_ROUNDS = 5

#: 回灌给模型的单条工具结果字符数上限 —— 再长也只是挤占上下文
MAX_RESULT_CHARS = 2000

ERROR_INVALID_ARGUMENTS = "invalid_tool_arguments"

_INVALID_ARGS_TEXT = (
    "模型返回的工具参数无法解析（不是合法 JSON），这次没有执行任何操作。"
    "请换个说法再问一次，或在「模型管理」里换一个更擅长工具调用的模型。"
)
_TRUNCATED_NOTE = (
    f"（已达到工具调用轮次上限 {MAX_TOOL_ROUNDS} 轮，我先停下来了。"
    "如果还需要更多信息，请把问题拆小一点再问。）"
)


@dataclass(frozen=True)
class CopilotCard:
    """结构化卡片：只读工具的完整结果，直接交给前端渲染。"""

    kind: str
    title: str
    data: dict[str, Any]


@dataclass(frozen=True)
class CopilotOutcome:
    """一次对话的全部产出。"""

    text: str
    cards: tuple[CopilotCard, ...] = ()
    drafts: tuple[CopilotDraft, ...] = ()
    rounds: int = 0
    truncated: bool = False
    error_kind: str | None = None
    model: str = ""


@dataclass
class _Accumulator:
    """一次对话过程中攒下的东西（内部可变，最终冻结成 CopilotOutcome）。"""

    convo: list[ChatMessage]
    cards: list[CopilotCard] = field(default_factory=list)
    drafts: list[CopilotDraft] = field(default_factory=list)
    rounds: int = 0
    truncated: bool = False


async def run_copilot_chat(
    provider: LLMProvider,
    messages: list[ChatMessage],
    *,
    ctx: CopilotContext,
    tools: tuple[CopilotTool, ...] = COPILOT_TOOLS,
    max_rounds: int = MAX_TOOL_ROUNDS,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> CopilotOutcome:
    """跑完一次问答（含工具往返），返回结构化结果。"""
    acc = _Accumulator(convo=[ChatMessage(role="system", content=SYSTEM_PROMPT), *messages])
    specs = tool_specs(tools)
    model_name = provider.model

    while acc.rounds < max_rounds:
        response = await provider.chat(
            acc.convo, tools=specs, temperature=temperature, max_tokens=max_tokens
        )
        model_name = response.model or model_name
        if not response.tool_calls:
            return _freeze(acc, response.content, model_name)
        if _has_unparsable_arguments(response.tool_calls):
            return _freeze(acc, _INVALID_ARGS_TEXT, model_name, ERROR_INVALID_ARGUMENTS)

        acc.rounds += 1
        await _run_round(response.tool_calls, acc, ctx, tools)
        if acc.drafts:
            break
    else:
        acc.truncated = True

    return await _compose_final(provider, acc, model_name, temperature, max_tokens)


# ── 单轮工具分发 ──────────────────────────────────────────────────────────────

async def _run_round(
    calls: tuple[ToolCall, ...],
    acc: _Accumulator,
    ctx: CopilotContext,
    tools: tuple[CopilotTool, ...],
) -> None:
    """执行一轮里的全部工具调用，并把往返写回对话。"""
    # 只在**本次传入**的工具集里查找：注册表是默认值，不是唯一来源。
    # 拿全局注册表查会让「限定工具集」的调用方形同虚设。
    allowed = {tool.name: tool for tool in tools}
    for call in calls:
        acc.convo.append(
            ChatMessage(
                role="assistant",
                content=f"{TOOL_CALL_PREFIX} {call.name}({json.dumps(call.arguments, ensure_ascii=False)})",
            )
        )
        result = await _dispatch(call, acc, ctx, allowed)
        acc.convo.append(
            ChatMessage(
                role="user", content=f"{TOOL_RESULT_PREFIX} {call.name} → {_clip(result)}"
            )
        )


async def _dispatch(
    call: ToolCall, acc: _Accumulator, ctx: CopilotContext, allowed: dict[str, CopilotTool]
) -> str:
    """分发一次工具调用，返回要回灌给模型的文本。"""
    tool = allowed.get(call.name)
    if tool is None:
        # 模型幻觉出一个不存在的工具名 —— 如实告诉它，不要假装调用成功
        return f"错误：不存在名为 {call.name} 的工具。"

    try:
        args = tool.args_model.model_validate(call.arguments)
    except Exception as exc:  # noqa: BLE001 —— Pydantic 的 ValidationError 也在内
        return f"错误：参数不合法 —— {exc}"

    if tool.requires_confirmation:
        return await _make_draft(tool, args, acc, ctx)
    return await _execute(tool, args, acc, ctx)


async def _make_draft(
    tool: CopilotTool, args: Any, acc: _Accumulator, ctx: CopilotContext
) -> str:
    """写动作：只生成草稿。`tool.handler` 在这条路径上永远不会被调用。"""
    if tool.draft_builder is None:  # CopilotTool 构造期已保证，这里只是兜底
        return f"错误：工具 {tool.name} 缺少草稿构造器，无法生成待确认草稿。"
    try:
        draft = await tool.draft_builder(ctx, args)
    except ToolExecutionError as exc:
        return f"错误：{exc}"
    acc.drafts.append(draft)
    return f"已生成待确认草稿「{draft.title}」，等待用户在界面上确认，尚未执行。"


async def _execute(
    tool: CopilotTool, args: Any, acc: _Accumulator, ctx: CopilotContext
) -> str:
    """只读工具：直接执行，完整结果进卡片，摘要回灌模型。"""
    try:
        payload = await tool.handler(ctx, args)
    except ToolExecutionError as exc:
        return f"错误：{exc}"

    card_data = payload.pop("_card", None)
    if tool.card_kind:
        acc.cards.append(
            CopilotCard(
                kind=tool.card_kind,
                title=tool.name,
                data=card_data if isinstance(card_data, dict) else payload,
            )
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


# ── 收尾 ─────────────────────────────────────────────────────────────────────

async def _compose_final(
    provider: LLMProvider,
    acc: _Accumulator,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> CopilotOutcome:
    """禁用工具再问一次，让模型把结果说成人话（这一步不可能再触发工具调用）。"""
    response = await provider.chat(
        acc.convo, tools=None, temperature=temperature, max_tokens=max_tokens
    )
    text = response.content.strip()
    if acc.truncated:
        text = f"{text}\n\n{_TRUNCATED_NOTE}".strip()
    return _freeze(acc, text, response.model or model_name)


def _freeze(
    acc: _Accumulator, text: str, model_name: str, error_kind: str | None = None
) -> CopilotOutcome:
    return CopilotOutcome(
        text=text,
        cards=tuple(acc.cards),
        drafts=tuple(acc.drafts),
        rounds=acc.rounds,
        truncated=acc.truncated,
        error_kind=error_kind,
        model=model_name,
    )


def _has_unparsable_arguments(calls: tuple[ToolCall, ...]) -> bool:
    """网关把无法解析的参数原文留在 `__raw__` 里 —— 见 openai_compat._decode_arguments。"""
    return any("__raw__" in call.arguments for call in calls)


def _clip(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return f"{text[:MAX_RESULT_CHARS]}…（结果过长已截断）"
