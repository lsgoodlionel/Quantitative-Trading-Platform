"""AI 报告的共用件（V3 Wave C-a / I4+I5）

两个报告端点共享的东西都在这里：免责声明、调用参数、以及「让模型吐出结构化
JSON」这件不太可靠的事的统一处理。

**为什么坚持要 JSON 而不是自由文本**：报告是分节展示的，分节必须是数据结构而不是
靠正则去切标题。本地小模型经常在 JSON 外面裹一层 ```json 代码块或加一句寒暄，
`extract_json_object` 负责把它剥出来。

**为什么有重试而不是一次定死**：第一次吐不出合法 JSON 时，把「你上次的输出不是
合法 JSON」作为一条新消息喂回去，多数模型第二次就对了。上限是 `MAX_ATTEMPTS`，
超了就抛 `AIReportError` —— 契约要求「失败时返回结构化错误而非半截报告」，
所以这里绝不做「解析不了就把原文塞进第一节」这种降级。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.llm import ChatMessage, ChatResponse, LLMProvider

#: 两个报告共用的免责声明。契约 §1.1 第 2 点：它是结构化字段，不是提示词里的一句话。
DISCLAIMER = (
    "本内容由大模型基于平台既有数据自动汇总生成，可能存在错误或遗漏，"
    "不构成任何投资建议或操作指引。请以 sources 中列出的原始信息自行核对。"
)

#: 单次生成允许的模型往返上限（首次 + 一次纠正）。契约 §三：要有轮次上限。
MAX_ATTEMPTS = 2

#: 单次调用超时。研报要写五节，比一句问答长得多，60s 偏紧。
LLM_TIMEOUT_SECONDS = 120.0

#: 生成长度上限。五节中文报告 ~1200 字，留足余量。
LLM_MAX_TOKENS = 2048

#: 报告要贴着数据写，温度压低。
LLM_TEMPERATURE = 0.2

_REPAIR_HINT = (
    "你上一次的输出不是合法 JSON，无法被解析。请**只**输出一个 JSON 对象，"
    "不要包裹代码块，不要任何解释性文字。"
)


class AIReportError(Exception):
    """报告生成失败 —— 模型连着几次都吐不出可用结构。"""


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里剥出一个 JSON 对象；剥不出返回 None。

    依次尝试：整段直接解析 → 去掉 ```json 围栏 → 取第一个 `{` 到最后一个 `}`。
    """
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def request_json(
    provider: LLMProvider,
    messages: list[ChatMessage],
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[dict[str, Any], str]:
    """要一份 JSON 结果，返回 (解析后的对象, 实际模型名)。

    Raises:
        AIReportError: 用尽 `max_attempts` 仍拿不到合法 JSON。
        LLMUnavailableError: provider 连不上 —— 由调用方翻成 503，不在这里吞掉。
    """
    convo = list(messages)
    model_name = provider.model
    last_text = ""

    for attempt in range(max_attempts):
        response: ChatResponse = await provider.chat(
            convo,
            tools=None,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        model_name = response.model or model_name
        last_text = response.content
        payload = extract_json_object(last_text)
        if payload is not None:
            return payload, model_name
        if attempt + 1 < max_attempts:
            convo = [
                *convo,
                ChatMessage(role="assistant", content=last_text),
                ChatMessage(role="user", content=_REPAIR_HINT),
            ]

    raise AIReportError(
        f"模型连续 {max_attempts} 次未能返回可解析的结构化结果，本次不生成报告。"
        f"可在「模型管理」里换一个更擅长结构化输出的模型。"
    )


def clean_text(value: Any, *, fallback: str = "") -> str:
    """模型给的某一节可能是 None / 数字 / 列表 —— 一律压成可直接渲染的字符串。"""
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, list):
        joined = "\n".join(clean_text(item) for item in value if item is not None)
        return joined.strip() or fallback
    if value is None:
        return fallback
    return str(value).strip() or fallback


def clean_lines(value: Any, *, limit: int) -> list[str]:
    """把模型给的「列表字段」压成最多 `limit` 条非空字符串。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = list(value)
    else:
        items = []
    cleaned = [text for text in (clean_text(item) for item in items) if text]
    return cleaned[:limit]


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _json_candidates(text: str) -> list[str]:
    """按「最可能正确」的顺序给出待解析的片段。"""
    stripped = (text or "").strip()
    if not stripped:
        return []
    candidates = [stripped]

    fenced = _strip_code_fence(stripped)
    if fenced != stripped:
        candidates.append(fenced)

    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    return candidates


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()
