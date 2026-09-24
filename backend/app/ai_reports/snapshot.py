"""个股数据快照（V3 Wave C-a / I4）

把研报要用的四类输入收成一个不可变结构：行情与技术指标 · 近期新闻 · 财报日历 ·
期权隐含波动率。全部来自既有服务，**不新增任何数据源**。

两条设计线：

1. **行情是硬依赖，其余是尽力而为**。K 线拿不到 / 太短 → 抛 `SnapshotError`
   （没有价格的「个股研报」是空转）。新闻、财报、期权任一失败只记一条
   `data_notes`，报告照出，缺的那节如实写「无可用数据」。
2. **缺失必须留痕**。`data_notes` 会同时进提示词与响应体 —— 用户看到的
   「本期无可用新闻」和模型看到的是同一句话，不存在前端粉饰后端缺数据的情况。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.data.models import Bar, Frequency, Market
from app.data.providers.news_calendar_models import CompanyNewsItem, EarningsEvent
from app.data.providers.news_service import NewsCalendarService
from app.data.providers.options_service import OptionsService
from app.data.service import DataService
from app.quant import indicators

logger = get_logger(__name__)

#: 少于这个数量的 K 线不足以谈「技术面」，直接拒绝生成而不是硬写
MIN_BARS = 5

#: 喂给模型的新闻条数上限 —— 再多只是挤占上下文，且 sources 会变得没法核对
MAX_NEWS_ITEMS = 12

#: 喂给模型的财报事件条数上限
MAX_EARNINGS_EVENTS = 6

#: 计入 ATM 隐含波动率的行权价范围（相对现价的偏离）
ATM_MONEYNESS_BAND = 0.10

#: 年化波动率的交易日基数
TRADING_DAYS_PER_YEAR = 252

#: 无新闻时写进报告与提示词的固定说法（契约 §1.1 第 3 点）
NO_NEWS_TEXT = "本期无可用新闻。"

_NO_NEWS_NOTE = "本期无可用新闻（数据源未返回任何条目）。"
_NO_EARNINGS_NOTE = "本期无可用财报日历数据。"
_NO_OPTIONS_NOTE = "本期无可用期权隐含波动率数据。"
_OPTIONS_US_ONLY_NOTE = "期权数据仅覆盖美股，本标的所在市场无期权链数据源。"


class SnapshotError(Exception):
    """行情这条硬依赖没拿到 —— 报告不该在没有价格的情况下生成。"""


@dataclass(frozen=True)
class NewsSource:
    """一条实际喂给模型的新闻。契约 §1.1 第 1 点：标题与时间必须可回溯。"""

    title: str
    published_at: str | None = None
    publisher: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "published_at": self.published_at,
            "publisher": self.publisher,
            "url": self.url,
        }


@dataclass(frozen=True)
class StockSnapshot:
    """一次研报所依据的全部事实。"""

    symbol: str
    market: str
    lookback_days: int
    start_date: str
    end_date: str
    bar_count: int
    technicals: dict[str, Any] = field(default_factory=dict)
    sources: tuple[NewsSource, ...] = ()
    news_summaries: tuple[str, ...] = ()
    earnings: tuple[dict[str, Any], ...] = ()
    implied_volatility: dict[str, Any] | None = None
    data_notes: tuple[str, ...] = ()

    @property
    def has_news(self) -> bool:
        return len(self.sources) > 0


async def build_stock_snapshot(
    session: Any,
    *,
    symbol: str,
    market: Market,
    lookback_days: int,
    news_service: NewsCalendarService | None = None,
    options_service: OptionsService | None = None,
) -> StockSnapshot:
    """收集研报所需的全部输入。行情失败即抛错，其余失败降级为 `data_notes`。"""
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=lookback_days)
    bars = await _fetch_bars(session, symbol=symbol, market=market, start=start, end=end)

    notes: list[str] = []
    sources, summaries = await _collect_news(
        news_service or NewsCalendarService(), symbol, market, notes
    )
    earnings = await _collect_earnings(
        news_service or NewsCalendarService(), symbol, market, notes
    )
    iv = await _collect_iv(options_service or OptionsService(), symbol, market, notes)

    return StockSnapshot(
        symbol=symbol.upper(),
        market=market.value,
        lookback_days=lookback_days,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        bar_count=len(bars),
        technicals=compute_technicals(bars),
        sources=tuple(sources),
        news_summaries=tuple(summaries),
        earnings=tuple(earnings),
        implied_volatility=iv,
        data_notes=tuple(notes),
    )


def compute_technicals(bars: list[Bar]) -> dict[str, Any]:
    """从 K 线算出研报要引用的技术指标。

    指标一律走 `app.quant.indicators` —— 研报里的 RSI 必须和行情页副图里的 RSI
    是同一个数，否则用户对着两个界面会得到两套「技术面」。窗口不够的指标留 None，
    不做任何外推。
    """
    frame = pd.DataFrame(
        {
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [float(b.volume) for b in bars],
        }
    )
    closes = frame["close"]

    return {
        "last_close": round(float(closes.iloc[-1]), 4),
        "last_bar_time": bars[-1].time.isoformat(),
        "change_pct_1d": _pct(closes.iloc[-1], closes.iloc[-2]) if len(closes) > 1 else None,
        "change_pct_period": _pct(closes.iloc[-1], closes.iloc[0]),
        "period_high": round(float(frame["high"].max()), 4),
        "period_low": round(float(frame["low"].min()), 4),
        "sma_20": _last(indicators.sma(frame, 20)),
        "sma_60": _last(indicators.sma(frame, 60)),
        "rsi_14": _last(indicators.rsi(frame, 14)),
        "atr_14": _last(indicators.atr(frame, 14)),
        "annualized_volatility_pct": _annualized_vol_pct(closes),
        "avg_volume_20": _last(frame["volume"].rolling(20).mean(), digits=0),
    }


# ── 行情 ─────────────────────────────────────────────────────────────────────

async def _fetch_bars(
    session: Any, *, symbol: str, market: Market, start: Any, end: Any
) -> list[Bar]:
    if session is None:
        raise SnapshotError("数据库会话不可用，无法获取行情。")
    try:
        bars = await DataService(session).get_bars(
            symbol=symbol, market=market, frequency=Frequency.DAY_1, start=start, end=end
        )
    except Exception as exc:  # noqa: BLE001 —— 数据源整体不可用
        raise SnapshotError(f"获取 {symbol} 行情失败：{exc}") from exc
    if len(bars) < MIN_BARS:
        raise SnapshotError(
            f"{symbol} 在 {start}~{end} 仅有 {len(bars)} 根日线，"
            f"少于生成研报所需的 {MIN_BARS} 根，请拉长回溯区间。"
        )
    return list(bars)


# ── 尽力而为的三类补充数据 ────────────────────────────────────────────────────

async def _collect_news(
    service: NewsCalendarService, symbol: str, market: Market, notes: list[str]
) -> tuple[list[NewsSource], list[str]]:
    try:
        response = await service.get_news(
            symbol=symbol, market=market.value, limit=MAX_NEWS_ITEMS
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai report news failed", symbol=symbol, error=str(exc))
        notes.append(f"新闻获取失败：{exc}")
        return [], []

    notes.extend(response.warnings)
    items = list(response.items)[:MAX_NEWS_ITEMS]
    if not items:
        notes.append(_NO_NEWS_NOTE)
        return [], []
    return [_to_source(item) for item in items], [_news_line(item) for item in items]


async def _collect_earnings(
    service: NewsCalendarService, symbol: str, market: Market, notes: list[str]
) -> list[dict[str, Any]]:
    try:
        response = await service.get_earnings(
            symbol=symbol, market=market.value, limit=MAX_EARNINGS_EVENTS
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai report earnings failed", symbol=symbol, error=str(exc))
        notes.append(f"财报日历获取失败：{exc}")
        return []

    notes.extend(response.warnings)
    events = list(response.events)[:MAX_EARNINGS_EVENTS]
    if not events:
        notes.append(_NO_EARNINGS_NOTE)
    return [_earnings_row(event) for event in events]


async def _collect_iv(
    service: OptionsService, symbol: str, market: Market, notes: list[str]
) -> dict[str, Any] | None:
    """近月平值隐含波动率。期权链只覆盖美股 —— 其余市场直接标注而不去空跑。"""
    if market is not Market.US:
        notes.append(_OPTIONS_US_ONLY_NOTE)
        return None
    try:
        chain = await service.get_chain(symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai report options failed", symbol=symbol, error=str(exc))
        notes.append(f"期权隐含波动率获取失败：{exc}")
        return None

    summary = summarize_atm_iv(chain)
    if summary is None:
        notes.append(_NO_OPTIONS_NOTE)
    return summary


def summarize_atm_iv(chain: Any) -> dict[str, Any] | None:
    """把一条期权链压成「近月平值 IV」一个数。取不到返回 None。"""
    underlying = getattr(chain, "underlying_price", None)
    if not underlying or underlying <= 0:
        return None

    contracts = [*getattr(chain, "calls", []), *getattr(chain, "puts", [])]
    ivs = [
        c.implied_volatility
        for c in contracts
        if c.implied_volatility is not None
        and c.strike
        and abs(c.strike - underlying) / underlying <= ATM_MONEYNESS_BAND
    ]
    if not ivs:
        return None
    return {
        "expiration": getattr(chain, "expiration", None),
        "underlying_price": round(float(underlying), 4),
        "atm_implied_volatility_pct": round(sum(ivs) / len(ivs) * 100, 2),
        "contracts_used": len(ivs),
    }


# ── 指标计算 ──────────────────────────────────────────────────────────────────

def _last(series: pd.Series, digits: int = 4) -> float | None:
    """取序列最后一个有效值。窗口不足时 rolling 给的是 NaN —— 返回 None 而不是 0。"""
    if len(series) == 0:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else round(float(value), digits)


def _annualized_vol_pct(closes: pd.Series) -> float | None:
    returns = closes.pct_change().dropna()
    if len(returns) < 2:
        return None
    std = returns.std(ddof=1)
    if pd.isna(std):
        return None
    return round(float(std) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 4)


def _pct(current: float, base: float) -> float | None:
    if not base:
        return None
    return round((float(current) / float(base) - 1) * 100, 4)


# ── 归一化 ───────────────────────────────────────────────────────────────────

def _to_source(item: CompanyNewsItem) -> NewsSource:
    return NewsSource(
        title=item.title,
        published_at=item.published_at.isoformat() if item.published_at else None,
        publisher=item.publisher,
        url=item.url,
    )


def _news_line(item: CompanyNewsItem) -> str:
    """一条喂给模型的新闻：时间 · 来源 · 标题（· 摘要）。"""
    when = item.published_at.isoformat() if item.published_at else "时间未知"
    head = f"[{when}] {item.publisher or '来源未知'} — {item.title}"
    return f"{head}\n  摘要：{item.summary.strip()}" if item.summary else head


def _earnings_row(event: EarningsEvent) -> dict[str, Any]:
    return {
        "report_date": event.report_date.isoformat() if event.report_date else None,
        "period": event.period,
        "eps_estimate": event.eps_estimate,
        "eps_actual": event.eps_actual,
        "surprise_percent": event.surprise_percent,
        "is_upcoming": event.is_upcoming,
    }
