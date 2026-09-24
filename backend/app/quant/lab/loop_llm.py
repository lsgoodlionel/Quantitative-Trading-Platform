"""
自动因子循环的 LLM 角色：**复盘 + 提种子**（V3 · I2 契约 §3）

模型只做两件事：

  - 复盘：用自然语言说清楚存活因子在捕捉什么模式、彼此是否高度相关
  - 提种子：为下一轮提出若干候选表达式

⚠️ **模型不参与适应度打分。** 打分必须是确定性、可复现的
（`compute_factor_fitness` + 固定 seed）。让模型给因子打分，等于让一个
不知道自己在猜的东西决定资金去向。

⚠️ **模型提出的表达式一律过 `expression_tree.parse_expr`**（手写递归下降 +
算子白名单），非法的丢弃并计数。本模块里没有 `eval`，也不会有。

⚠️ **模型不可用时循环照跑。** 这个功能的核心是搜索，不是模型 ——
调用失败、没配 provider、返回一团乱码，一律降级成 `review=None` 继续走。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.core.llm.base import ChatMessage, LLMError, LLMProvider
from app.quant.lab.auto_loop import LoopRound, parse_seed_expressions

logger = logging.getLogger(__name__)

#: 最多采纳几条下一轮种子 —— 再多也只是把下一轮的初代种群塞满
MAX_NEXT_SEEDS = 6

#: 复盘文本长度上限。模型偶尔会写一篇小作文，前端展示不下，也没人看
MAX_REVIEW_CHARS = 1500

#: 送进模型的存活因子条数上限
MAX_SURVIVORS_IN_PROMPT = 10

SYSTEM_PROMPT = """你是量化因子研究的复盘助手。

你会收到一轮自动因子挖掘的结果：若干条存活的因子表达式，每条都带样本内与样本外两套指标。

你的任务只有两件：
1. 复盘：这些因子在捕捉什么市场模式？彼此是否高度相关（结构相似 = 冗余）？
   样本外相对样本内衰减明显的，直接指出来。
2. 为下一轮提出候选种子表达式。

严格约束：
- **不要给因子打分、排名或推荐上线**。打分由确定性的回测流程负责，不是你的工作。
- 表达式只能用给定的算子与特征名，写成 `算子(参数, ...)` 的形式，如 `DIV(MOM20, ATR_RATIO)`。
- 不认识的名字一律不要用；不确定就少提几条。

只输出一个 JSON 对象，不要写其他任何内容：
{"review": "复盘文字", "seeds": ["表达式1", "表达式2"]}
"""


@dataclass(frozen=True)
class LlmOutcome:
    """一次复盘的产出。`review is None` 表示这一轮没有可用的模型复盘。"""

    review: str | None
    next_seeds: tuple[str, ...] = ()
    rejected_seed_count: int = 0
    error: str | None = None


async def review_round(
    provider: LLMProvider,
    round_: LoopRound,
    *,
    include_cross_section: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> LlmOutcome:
    """让模型复盘一轮结果并提出下一轮种子。任何失败都降级为 `review=None`。"""
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_prompt(round_)),
    ]
    try:
        response = await provider.chat(
            messages, tools=None, temperature=temperature, max_tokens=max_tokens
        )
    except LLMError as exc:
        logger.warning("因子循环复盘调用失败，本轮跳过复盘：%s", exc)
        return LlmOutcome(review=None, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — 复盘失败绝不能带垮整轮搜索
        logger.exception("因子循环复盘出现未预期异常，本轮跳过复盘")
        return LlmOutcome(review=None, error=str(exc))

    return interpret_reply(response.content, include_cross_section=include_cross_section)


def interpret_reply(content: str, *, include_cross_section: bool = False) -> LlmOutcome:
    """把模型回复解析成复盘文本 + 合法种子。

    本地小模型经常不听话（吐 markdown 围栏、在 JSON 前后加解释、干脆写散文），
    所以走两级：先按 JSON 解析，失败则把整段当复盘文本、逐行捞可解析的表达式。
    两条路径的种子**都要**过 `parse_seed_expressions` 的白名单校验。
    """
    text = (content or "").strip()
    if not text:
        return LlmOutcome(review=None, error="模型返回空内容")

    payload = _extract_json_object(text)
    if payload is not None:
        review = _clip(str(payload.get("review", "")).strip()) or None
        raw_seeds = payload.get("seeds", [])
        candidates = [str(s) for s in raw_seeds] if isinstance(raw_seeds, list) else []
    else:
        review = _clip(text)
        candidates = _scan_expression_lines(text)

    _, accepted, rejected = parse_seed_expressions(
        candidates, include_cross_section=include_cross_section
    )
    return LlmOutcome(
        review=review,
        next_seeds=accepted[:MAX_NEXT_SEEDS],
        rejected_seed_count=rejected,
    )


def build_prompt(round_: LoopRound) -> str:
    """把一轮结果摊成给模型看的文本。分母（`hypotheses_tested`）也一并给它。"""
    lines = [
        f"universe: {', '.join(round_.universe) or '(未记录)'}",
        f"样本内截止日: {round_.is_end}（其后 {round_.embargo_bars} 根 bar 为禁运期）",
        f"本轮共评估了 {round_.hypotheses_tested} 个不同表达式，以下是挑出来的最好的几条。",
        "",
        "存活因子（IS = 样本内，OOS = 样本外）：",
    ]
    for i, c in enumerate(round_.survivors[:MAX_SURVIVORS_IN_PROMPT], start=1):
        oos = c.out_of_sample
        oos_text = (
            f"OOS IC={_fmt(oos.ic_mean)} RankIC={_fmt(oos.rank_ic_mean)}"
            if oos is not None
            else "OOS 无数据"
        )
        suspect = "，样本外衰减明显" if c.overfit_suspect else ""
        lines.append(
            f"{i}. {c.expr} | IS IC={_fmt(c.in_sample.ic_mean)} "
            f"RankIC={_fmt(c.in_sample.rank_ic_mean)} | {oos_text}{suspect}"
        )
    if not round_.survivors:
        lines.append("（本轮没有任何因子通过样本内评估）")
    return "\n".join(lines)


# ── 内部 ──────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
#: 一行里的 `NAME(...)` 或裸 `NAME` —— 只用来**挑出候选**，合法性仍由解析器裁决
_EXPR_LINE_RE = re.compile(r"^[\s\-*\d.)]*([A-Za-z_][A-Za-z0-9_]*(?:\s*\(.*\))?)\s*$")


def _extract_json_object(text: str) -> dict | None:
    """从可能带 markdown 围栏 / 前后废话的回复里抠出 JSON 对象。"""
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    return candidates


def _scan_expression_lines(text: str) -> list[str]:
    """逐行捞出「看起来像表达式」的行。真假由 `parse_expr` 说了算，这里只做粗筛。"""
    found: list[str] = []
    for line in text.splitlines():
        match = _EXPR_LINE_RE.match(line.strip())
        if match:
            found.append(match.group(1))
    return found[: MAX_NEXT_SEEDS * 3]


def _clip(text: str) -> str:
    if len(text) <= MAX_REVIEW_CHARS:
        return text
    return f"{text[:MAX_REVIEW_CHARS]}…（复盘过长已截断）"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"
