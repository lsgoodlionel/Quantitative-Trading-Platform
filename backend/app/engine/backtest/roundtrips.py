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
    从扁平 fills[] 通过 FIFO 批次匹配重构回合交易列表。

    - BUY 开/加多头批次；SELL FIFO 消耗多头批次（当前引擎为多头现货）。
    - 每消耗一个批次切片即产出一个 RoundTrip。
    - pnl 按 (consumed / sell.qty) 比例分摊券商 realized_pnl，保证不变量成立。
    - entry_tag 取自开仓 fill，exit_reason 取自平仓 fill（缺省用回退常量）。
    """
    trips: list[RoundTrip] = []
    open_lots: dict[str, deque[_OpenLot]] = defaultdict(deque)
    tid = 0

    for fill in sorted(fills, key=lambda f: str(f.get("filled_at") or "")):
        side = str(fill.get("side", "")).upper()
        qty = float(fill.get("qty", 0) or 0)
        if qty <= _EPS:
            continue
        price = float(fill.get("price", 0) or 0)
        commission = float(fill.get("commission", 0) or 0)
        filled_at = _parse_time(fill.get("filled_at"))
        symbol = fill.get("symbol", "")

        if side == "BUY":
            tag = fill.get("entry_tag") or DEFAULT_ENTRY_TAG
            open_lots[symbol].append(
                _OpenLot(qty=qty, price=price, time=filled_at, tag=tag,
                         commission=commission, orig_qty=qty)
            )
            continue

        if side != "SELL":
            continue

        realized = float(fill.get("realized_pnl", 0) or 0)
        exit_reason = fill.get("exit_reason") or DEFAULT_EXIT_REASON
        remaining = qty
        lots = open_lots[symbol]

        while remaining > _EPS and lots:
            lot = lots[0]
            consumed = min(remaining, lot.qty)

            frac_sell = consumed / qty if qty > _EPS else 0.0
            frac_lot = consumed / lot.orig_qty if lot.orig_qty > _EPS else 0.0
            buy_comm = lot.commission * frac_lot
            sell_comm = commission * frac_sell
            pnl_slice = realized * frac_sell

            holding_bars = _holding_bars(lot.time, filled_at, bars_index)
            holding_days = max((filled_at - lot.time).total_seconds() / 86400.0, 0.0)

            tid += 1
            trips.append(RoundTrip(
                trip_id=tid,
                entry_time=lot.time,
                exit_time=filled_at,
                direction="long",
                entry_tag=lot.tag,
                exit_reason=exit_reason,
                qty=consumed,
                entry_price=lot.price,
                exit_price=price,
                pnl=pnl_slice,
                commission=buy_comm + sell_comm,
                holding_bars=holding_bars,
                holding_days=holding_days,
            ))

            lot.qty -= consumed
            remaining -= consumed
            if lot.qty <= _EPS:
                lots.popleft()

    return trips


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
