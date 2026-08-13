"""
复权与公司行为（Wave K-b / K8）

问题：回测直接用原始价，跨除权日会出现虚假暴跌（AAPL 2020-08-31 一拆四，
原始价从 499 跳到 129），所有趋势策略被误触发。

复权口径：**以最新价为基准调整历史价**（因子在最后一个 session 上恒为 1.0，
越往前越小）。这样「最新价 = 真实价」，回测持仓市值可直接与实盘对账。

> 契约 waveKb §3.2 把这一口径写作「后复权」，但同段又要求「最新价 = 真实价」。
> 二者在中文术语上是矛盾的（该口径通称**前复权/qfq**，后复权 hfq 保留的是最早价）。
> 此处按契约**明确写出的语义要求**实现：最新价 = 真实价。

参考 zipline `zipline/data/adjustments.py` 的因子累乘思路（Apache License 2.0）：
    Copyright 2016 Quantopian, Inc.
本文件为独立实现。
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from dataclasses import dataclass, replace
from datetime import date as Date
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

from app.data.models import Bar, Market

logger = logging.getLogger(__name__)

ActionKind = Literal["split", "dividend", "merger"]

VALID_KINDS: frozenset[str] = frozenset({"split", "dividend", "merger"})
# 因子为 1 时无需重建 Bar，浮点比较留一点容差
_FACTOR_EPS = 1e-12
FACTOR_SERIES_NAME = "adj_factor"


@dataclass(frozen=True)
class CorporateAction:
    """
    一次公司行为。

    ratio  — 拆股比例：1 拆 N 则 ratio=N；N 合 1（缩股）则 ratio=1/N。
    amount — 每股现金分红（除权日发放）。
    """

    symbol: str
    ex_date: Date
    kind: ActionKind
    ratio: float | None = None
    amount: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"未知公司行为类型 {self.kind!r}，可选 {sorted(VALID_KINDS)}")
        if self.kind == "split" and (self.ratio is None or self.ratio <= 0):
            raise ValueError(f"拆股必须提供正的 ratio，收到 {self.ratio!r}")
        if self.kind == "dividend" and (self.amount is None or self.amount <= 0):
            raise ValueError(f"分红必须提供正的 amount，收到 {self.amount!r}")


# ── 因子构建 ──────────────────────────────────────────────────

def build_adjustment_factors(
    actions: list[CorporateAction],
    sessions: list[Date],
    prices: pd.Series | None = None,
) -> pd.Series:
    """
    逐日累乘的价格复权因子（最后一个 session 恒为 1.0）。

    某个 session d 的因子 = 所有 `ex_date > d` 的公司行为调整比例之积：
      - 拆股 1 拆 N → 比例 1/N（历史价按比例下调）
      - 分红 A 元   → 比例 (1 - A / 除权前收盘价)

    prices: session → 收盘价。缺省时分红**不做价格调整**（只保留现金流），
            因为没有前收价就无法把现金分红折算成价格比例 —— 记 warning 而非静默跳过。
    """
    ordered = _ordered_sessions(sessions)
    if not ordered:
        return pd.Series(dtype=float, name=FACTOR_SERIES_NAME)

    factors = np.ones(len(ordered), dtype=float)
    for action in sorted(actions, key=lambda a: a.ex_date):
        ratio = _action_price_ratio(action, ordered, prices)
        if ratio is None:
            continue
        # 除权日之前（严格小于）的全部 session 乘上该比例
        cut = bisect_left(ordered, action.ex_date)
        factors[:cut] *= ratio

    return pd.Series(factors, index=pd.DatetimeIndex(ordered), name=FACTOR_SERIES_NAME)


def build_volume_factors(
    actions: list[CorporateAction], sessions: list[Date]
) -> pd.Series:
    """
    成交量复权因子：只有拆股影响股数（1 拆 N → 历史成交量 ×N）。
    现金分红不改变股数，因此不参与成交量调整。
    """
    ordered = _ordered_sessions(sessions)
    if not ordered:
        return pd.Series(dtype=float, name="volume_factor")

    factors = np.ones(len(ordered), dtype=float)
    for action in sorted(actions, key=lambda a: a.ex_date):
        if action.kind != "split" or action.ratio is None:
            continue
        cut = bisect_left(ordered, action.ex_date)
        factors[:cut] *= action.ratio

    return pd.Series(factors, index=pd.DatetimeIndex(ordered), name="volume_factor")


def _action_price_ratio(
    action: CorporateAction, sessions: list[Date], prices: pd.Series | None
) -> float | None:
    """单次公司行为的价格调整比例；None 表示不做价格调整。"""
    if action.kind == "split":
        return 1.0 / float(action.ratio)   # __post_init__ 已保证 ratio > 0

    if action.kind == "merger":
        logger.warning(
            "%s %s: 合并/重组（merger）暂未实现价格调整，已跳过",
            action.symbol, action.ex_date,
        )
        return None

    prev_close = _close_before(prices, action.ex_date, sessions)
    if prev_close is None or prev_close <= 0:
        logger.warning(
            "%s %s: 缺少除权前收盘价，分红 %.4f 只计现金流、不调整价格",
            action.symbol, action.ex_date, action.amount or 0.0,
        )
        return None

    ratio = 1.0 - float(action.amount) / prev_close
    if ratio <= 0:
        logger.warning(
            "%s %s: 分红 %.4f 不小于前收价 %.4f，比例异常，跳过价格调整",
            action.symbol, action.ex_date, action.amount or 0.0, prev_close,
        )
        return None
    return ratio


def _close_before(
    prices: pd.Series | None, ex_date: Date, sessions: list[Date]
) -> float | None:
    """除权日前最后一个 session 的收盘价。"""
    if prices is None or prices.empty:
        return None
    cut = bisect_left(sessions, ex_date)
    if cut == 0:
        return None
    key = pd.Timestamp(sessions[cut - 1])
    if key not in prices.index:
        return None
    value = float(prices.loc[key])
    return None if not np.isfinite(value) else value


def _ordered_sessions(sessions: list[Date]) -> list[Date]:
    return sorted({_as_date(s) for s in sessions})


def _as_date(value: Date | datetime) -> Date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    raise TypeError(f"需要 date 或 datetime，收到 {type(value).__name__}")


# ── 因子应用 ──────────────────────────────────────────────────

def apply_adjustments(
    bars: list[Bar],
    factors: pd.Series,
    volume_factors: pd.Series | None = None,
) -> list[Bar]:
    """
    按因子重建 bar 序列（Bar 是 frozen dataclass，返回新对象，绝不就地修改）。

    OHLC 与 vwap 乘价格因子；volume 乘成交量因子（缺省不动）。
    因子中找不到的日期原样返回，不静默丢 bar。
    """
    if factors is None or factors.empty:
        return list(bars)

    price_map = _factor_map(factors)
    volume_map = _factor_map(volume_factors) if volume_factors is not None else {}

    return [_adjust_bar(bar, price_map, volume_map) for bar in bars]


def _factor_map(factors: pd.Series) -> dict[Date, float]:
    return {_as_date(idx): float(val) for idx, val in factors.items()}


def _adjust_bar(
    bar: Bar, price_map: dict[Date, float], volume_map: dict[Date, float]
) -> Bar:
    day = bar.time.date()
    price_factor = price_map.get(day, 1.0)
    volume_factor = volume_map.get(day, 1.0)

    if (
        abs(price_factor - 1.0) <= _FACTOR_EPS
        and abs(volume_factor - 1.0) <= _FACTOR_EPS
    ):
        return bar

    return replace(
        bar,
        open=bar.open * price_factor,
        high=bar.high * price_factor,
        low=bar.low * price_factor,
        close=bar.close * price_factor,
        vwap=bar.vwap * price_factor if bar.vwap is not None else None,
        volume=max(int(round(bar.volume * volume_factor)), 0),
        # 成交额 = 价 × 量，两个因子都要吃进去才自洽：
        # 纯拆股时 price_factor × volume_factor == 1（真实成交额本就不变），
        # 分红时 volume_factor == 1，成交额随价格进入复权空间。
        turnover=(
            bar.turnover * price_factor * volume_factor
            if bar.turnover is not None
            else None
        ),
    )


# ── 分红现金流 ────────────────────────────────────────────────

def dividend_cash_per_share(
    actions: list[CorporateAction], factors: pd.Series | None = None
) -> dict[Date, float]:
    """
    除权日 → 每股现金分红。

    传入价格因子时，分红按该日因子折算到复权价空间 —— 持仓股数同样处于复权空间，
    两者口径必须一致，否则现金流入会被系统性高估。
    """
    factor_map = _factor_map(factors) if factors is not None and not factors.empty else {}

    cash: dict[Date, float] = {}
    for action in actions:
        if action.kind != "dividend" or action.amount is None:
            continue
        scaled = action.amount * factor_map.get(action.ex_date, 1.0)
        cash[action.ex_date] = cash.get(action.ex_date, 0.0) + scaled
    return cash


# ── 数据获取（best-effort，失败不硬错） ────────────────────────

def fetch_corporate_actions(symbol: str, market: Market) -> list[CorporateAction]:
    """
    拉取公司行为。优先 yfinance（US/HK），A 股走 AkShare。
    两者都是已有依赖，且在函数内惰性 import —— 未安装/无网络时返回空列表并 warning。
    """
    try:
        if market == Market.A:
            return _fetch_from_akshare(symbol)
        return _fetch_from_yfinance(symbol, market)
    except Exception as exc:   # noqa: BLE001 — 外部数据源不可控，降级不可中断回测
        logger.warning("拉取 %s(%s) 公司行为失败，按无公司行为处理：%s", symbol, market, exc)
        return []


def _fetch_from_yfinance(symbol: str, market: Market) -> list[CorporateAction]:
    import yfinance as yf

    from app.data.providers.yfinance_provider import to_yf_symbol

    raw = yf.Ticker(to_yf_symbol(symbol, market.value)).actions
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        logger.warning("yfinance 未返回 %s 的公司行为", symbol)
        return []

    actions: list[CorporateAction] = []
    for idx, row in raw.iterrows():
        ex_date = _as_date(idx)
        split = float(row.get("Stock Splits", 0.0) or 0.0)
        dividend = float(row.get("Dividends", 0.0) or 0.0)
        if split > 0:
            actions.append(
                CorporateAction(symbol=symbol, ex_date=ex_date, kind="split", ratio=split)
            )
        if dividend > 0:
            actions.append(
                CorporateAction(
                    symbol=symbol, ex_date=ex_date, kind="dividend", amount=dividend
                )
            )
    return actions


def _fetch_from_akshare(symbol: str) -> list[CorporateAction]:
    """
    A 股：AkShare `stock_zh_a_daily(adjust="hfq")` 直接给复权价，本项目只需事件流，
    因此用分红送配详情接口。接口字段为中文且不稳定，全部 best-effort。
    """
    import akshare as ak

    from app.data.providers.akshare_provider import _bare_code

    raw = ak.stock_fhps_detail_em(symbol=_bare_code(symbol))
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        logger.warning("AkShare 未返回 %s 的分红送配数据", symbol)
        return []

    actions: list[CorporateAction] = []
    for _, row in raw.iterrows():
        ex_date = _safe_date(row.get("除权除息日"))
        amount = _safe_float(row.get("现金分红-现金分红比例"))
        if ex_date is None or amount is None or amount <= 0:
            continue
        # A 股「每 10 股派 X 元」→ 每股 X/10
        actions.append(
            CorporateAction(
                symbol=symbol, ex_date=ex_date, kind="dividend", amount=amount / 10.0
            )
        )
    return actions


def _safe_date(value: object) -> Date | None:
    try:
        ts = pd.Timestamp(value)   # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date()


def _safe_float(value: object) -> float | None:
    try:
        number = float(value)   # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    return None if pd.isna(number) else number
