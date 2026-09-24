"""
回合交易重构 (Round-Trip Reconstruction) — C7

将引擎输出的扁平 fills[] (BUY/SELL 事件流) 通过 FIFO 批次匹配还原成
"开仓→平仓" 的完整回合 (RoundTrip)。这是 C7 逐笔分析与标签分组的规范数据源。

参考:
- refs/backtrader/backtrader/analyzers/tradeanalyzer.py — 交易配对逻辑
- refs/jesse/jesse/services/metrics.py — 持仓周期统计

关键不变量 (§4.0):
    sum(trip.pnl for trips) == sum(sell_fill.realized_pnl)
确保 C7 与既有 metrics.expectancy/total_trades 口径一致 —— 直接复用券商
已实现盈亏 (realized_pnl)，不重新计算。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# 无标签时的回退常量 (§1.1)
DEFAULT_ENTRY_TAG = "untagged"
DEFAULT_EXIT_REASON = "signal"

_EPS = 1e-9


@dataclass
class _OpenLot:
    """FIFO 队列中的一个开仓批次。"""
    qty: float
    price: float
    time: datetime
    tag: str
    commission: float   # 该批次买入佣金（按比例分摊）
    orig_qty: float     # 原始开仓数量（用于佣金分摊）


@dataclass(frozen=True)
class RoundTrip:
    """一笔完整的开仓→平仓回合。"""
    trip_id: int
    entry_time: datetime
    exit_time: datetime
    direction: str          # "long" | "short"
    entry_tag: str
    exit_reason: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float              # 净已实现盈亏（货币，已扣佣金，复用券商值）
    commission: float
    holding_bars: int
    holding_days: float

    @property
    def pnl_pct(self) -> float:
        notional = self.entry_price * self.qty
        return self.pnl / notional * 100 if abs(notional) > 1e-12 else 0.0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def is_loss(self) -> bool:
        return self.pnl < 0

    def to_row(self) -> dict:
        """序列化为 API RoundTripRow 结构。"""
        return {
            "trip_id": self.trip_id,
            "entry_time": _fmt(self.entry_time),
            "exit_time": _fmt(self.exit_time),
            "direction": self.direction,
            "entry_tag": self.entry_tag,
            "exit_reason": self.exit_reason,
            "qty": round(self.qty, 4),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "pnl": round(self.pnl, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            "commission": round(self.commission, 4),
            "holding_bars": self.holding_bars,
            "holding_days": round(self.holding_days, 4),
        }


def build_round_trips(
    fills: list[dict],
    bars_index: pd.DatetimeIndex | None = None,
) -> list[RoundTrip]:
    """
    从扁平 fills[] 通过 FIFO 批次匹配重构回合交易列表（支持多空双向）。

    - SELL 先 FIFO 消耗多头批次（产出 long 回合），剩余部分开空头批次。
    - BUY  先 FIFO 消耗空头批次（产出 short 回合），剩余部分开多头批次。
    - 每消耗一个批次切片即产出一个 RoundTrip。
    - pnl 按 (consumed / 本笔平仓数量) 比例分摊券商 realized_pnl，保证
      `sum(trip.pnl) == sum(realized_pnl)` 这一不变量成立。
    - entry_tag 取自开仓 fill，exit_reason 取自平仓 fill（缺省用回退常量）。

    纯多头场景下与做空上线前逐笔一致：空头队列恒为空，SELL 全部用于平多。
    """
    trips: list[RoundTrip] = []
    long_lots: dict[str, deque[_OpenLot]] = defaultdict(deque)
    short_lots: dict[str, deque[_OpenLot]] = defaultdict(deque)
    counter = _TripCounter()

    for fill in sorted(fills, key=lambda f: str(f.get("filled_at") or "")):
        side = str(fill.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            continue
        qty = float(fill.get("qty", 0) or 0)
        if qty <= _EPS:
            continue

        is_buy = side == "BUY"
        closing = short_lots[fill.get("symbol", "")] if is_buy else long_lots[fill.get("symbol", "")]
        opening = long_lots[fill.get("symbol", "")] if is_buy else short_lots[fill.get("symbol", "")]

        consumed_total = _close_against(
            fill, qty, closing, trips, counter,
            direction="short" if is_buy else "long",
            bars_index=bars_index,
        )
        remaining = qty - consumed_total
        if remaining > _EPS:
            commission = float(fill.get("commission", 0) or 0)
            opening.append(_OpenLot(
                qty=remaining,
                price=float(fill.get("price", 0) or 0),
                time=_parse_time(fill.get("filled_at")),
                tag=fill.get("entry_tag") or DEFAULT_ENTRY_TAG,
                # 整笔开仓时原样保留佣金，避免 (c*q)/q 的浮点末位漂移
                commission=commission if remaining == qty else commission * (remaining / qty),
                orig_qty=remaining,
            ))

    return trips


class _TripCounter:
    """回合序号发号器（避免在闭包里改外层变量）。"""

    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def _close_against(
    fill: dict,
    qty: float,
    lots: deque[_OpenLot],
    trips: list[RoundTrip],
    counter: _TripCounter,
    *,
    direction: str,
    bars_index: pd.DatetimeIndex | None,
) -> float:
    """用本笔成交 FIFO 消耗反向批次，产出回合。返回实际平仓数量。"""
    closing_qty = min(qty, sum(lot.qty for lot in lots))
    if closing_qty <= _EPS:
        return 0.0

    price = float(fill.get("price", 0) or 0)
    commission = float(fill.get("commission", 0) or 0)
    filled_at = _parse_time(fill.get("filled_at"))
    realized = float(fill.get("realized_pnl", 0) or 0)
    exit_reason = fill.get("exit_reason") or DEFAULT_EXIT_REASON

    remaining = closing_qty
    while remaining > _EPS and lots:
        lot = lots[0]
        consumed = min(remaining, lot.qty)

        frac_close = consumed / closing_qty
        frac_lot = consumed / lot.orig_qty if lot.orig_qty > _EPS else 0.0
        entry_comm = lot.commission * frac_lot
        exit_comm = commission * (consumed / qty)

        trips.append(RoundTrip(
            trip_id=counter.next(),
            entry_time=lot.time,
            exit_time=filled_at,
            direction=direction,
            entry_tag=lot.tag,
            exit_reason=exit_reason,
            qty=consumed,
            entry_price=lot.price,
            exit_price=price,
            pnl=realized * frac_close,
            commission=entry_comm + exit_comm,
            holding_bars=_holding_bars(lot.time, filled_at, bars_index),
            holding_days=max((filled_at - lot.time).total_seconds() / 86400.0, 0.0),
        ))

        lot.qty -= consumed
        remaining -= consumed
        if lot.qty <= _EPS:
            lots.popleft()

    return closing_qty - remaining


def _holding_bars(entry: datetime, exit_: datetime, bars_index: pd.DatetimeIndex | None) -> int:
    """用 bar 索引估算持仓 bar 数；无索引时返回 0。"""
    if bars_index is None or len(bars_index) == 0:
        return 0
    try:
        entry_ts = pd.Timestamp(entry)
        exit_ts = pd.Timestamp(exit_)
        i0 = int(bars_index.searchsorted(entry_ts))
        i1 = int(bars_index.searchsorted(exit_ts))
        return max(i1 - i0, 0)
    except Exception:
        return 0


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return datetime.utcnow()


def _fmt(dt: datetime) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)
