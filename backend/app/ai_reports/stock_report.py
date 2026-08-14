"""个股研报编排（V3 Wave C-a / I4）

快照 → 提示词 → 模型 → 分节报告。本模块不碰 HTTP，也不解析 provider 配置。

**无新闻时「消息面」由本模块直接写死**，不采用模型对该节的输出：既然快照里
一条新闻都没有，那一节唯一诚实的内容就是「本期无可用新闻」。提示词里的禁令是
第一道防线，这里是第二道 —— 本地小模型经常无视禁令自行发挥，而这一节恰恰是
最容易被编造的地方。

**不做缓存**（契约 §1.1 第 4 点）：研报依赖实时行情与新闻，缓存的过期语义
比省下的一次调用更麻烦。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ai_reports.common import DISCLAIMER, clean_text, request_json
from app.ai_reports.snapshot import NO_NEWS_TEXT, NewsSource, StockSnapshot
from app.ai_reports.stock_prompt import SECTION_KEYS, SECTION_TITLES, build_stock_messages
from app.core.llm import LLMProvider

#: 模型漏写某一节时的占位文案 —— 空字符串会让前端渲染出一张空卡片
_MISSING_SECTION = "模型未就该章节输出内容。"


@dataclass(frozen=True)
class StockReport:
    """一份可以直接序列化给前端的研报。"""

    symbol: str
    market: str
    sections: dict[str, str]
    section_titles: list[dict[str, str]]
    sources: list[dict[str, Any]]
    data_notes: list[str]
    technicals: dict[str, Any]
    generated_at: str
    model: str
    disclaimer: str = DISCLAIMER


async def generate_stock_report(
    provider: LLMProvider, snapshot: StockSnapshot
) -> StockReport:
    """生成一份研报。模型连不上抛 `LLMUnavailableError`，吐不出结构抛 `AIReportError`。"""
    payload, model_name = await request_json(provider, build_stock_messages(snapshot))
    sections = _extract_sections(payload, snapshot)

    return StockReport(
        symbol=snapshot.symbol,
        market=snapshot.market,
        sections=sections,
        section_titles=[{"key": key, "title": title} for key, title in SECTION_TITLES],
        sources=[source.to_dict() for source in snapshot.sources],
        data_notes=list(snapshot.data_notes),
        technicals=dict(snapshot.technicals),
        generated_at=datetime.now(tz=UTC).isoformat(),
        model=model_name,
    )


def _extract_sections(payload: dict[str, Any], snapshot: StockSnapshot) -> dict[str, str]:
    """取出五节内容；无新闻时「消息面」不采信模型输出。"""
    sections = {
        key: clean_text(payload.get(key), fallback=_MISSING_SECTION) for key in SECTION_KEYS
    }
    if not snapshot.has_news:
        sections["news"] = NO_NEWS_TEXT
    return sections


__all__ = ["NewsSource", "StockReport", "generate_stock_report"]
