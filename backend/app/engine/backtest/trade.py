"""单笔交易（Trade）与策略级退出规则（Wave K-d / K4）

现有 `Position` 是**聚合持仓**（管成本），回答不了「本笔持仓多久」「本笔浮盈多少」
这两个问题，而止损 / ROI / 追踪止损全都建立在这两个量上。`Trade` 补上这一层：
`SimulatedBroker` 为每个有仓标的维护一个 `Trade`，Position 管成本，Trade 管本笔状态。

> **许可证**：止损 / minimal_roi / 追踪止损的**语义**设计参考自 freqtrade
> （GPL-3.0）的 IStrategy 风险闸门。此处仅阅读其算法思路后**独立实现**，
> 未复制其任何代码。QuantBot 不受 GPL 传染。

规则语义（本模块的权威定义）：

- **minimal_roi**：`{持仓分钟数: 目标收益率}`。取「不超过当前持仓时长」的最大键
  作为当前阈值，收益率达到即离场。`{0: 0.10, 60: 0.05, 120: 0}` 表示
  开仓即要 10%、持满 1 小时降到 5%、满 2 小时只要不亏就走。
- **stoploss**：负数，如 `-0.10` 表示本笔亏 10% 离场。
- **trailing_stop**：从**本笔收益率的历史峰值**回撤固定距离即离场。
  距离取 `trailing_stop_positive`，未配置时取 `abs(stoploss)`；
  `trailing_stop_positive_offset > 0` 时，峰值达到该值之前追踪不启用。

判定优先级固定为 **ROI → 止损 → 追踪止损**。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

#: 平仓单的 exit_reason 取值（直接进 tag_metrics.py 的分组统计）
EXIT_ROI = "roi"
EXIT_STOP_LOSS = "stop_loss"
EXIT_TRAILING_STOP = "trailing_stop_loss"

#: 由风险闸门产生的全部退出原因，引擎用它判断某标的是否已有在途平仓单
RISK_EXIT_REASONS = frozenset({EXIT_ROI, EXIT_STOP_LOSS, EXIT_TRAILING_STOP})

TradeDirection = Literal["long", "short"]

#: 一分钟的秒数
_SECONDS_PER_MINUTE = 60.0


@dataclass(frozen=True)
class Trade:
    """
    一笔仍未平掉的交易。

    刻意做成 frozen：极值跟踪走 `mark_profit()` 返回新实例，
    券商把新实例写回 `open_trades`，避免任何人拿着旧引用悄悄改状态。
    """

    symbol: str
    direction: TradeDirection
    open_time: datetime
    open_price: float
    qty: int
    entry_tag: str | None = None
    #: 本笔收益率的历史极值（追踪止损用）
    max_profit_seen: float = 0.0
    min_profit_seen: float = 0.0

    def current_profit(self, price: float) -> float:
        """带方向的收益率：多头 = 涨幅，空头 = 跌幅。"""
        if self.open_price <= 0:
            return 0.0
        raw = price / self.open_price - 1.0
        return raw if self.direction == "long" else -raw

    def duration_minutes(self, now: datetime) -> int:
        """持仓时长（分钟，向下取整）。时钟回拨等异常情况一律记 0。"""
        elapsed = (now - self.open_time).total_seconds()
        if elapsed <= 0:
            return 0
        return int(elapsed // _SECONDS_PER_MINUTE)

    def mark_profit(self, profit: float) -> Trade:
        """刷新收益率极值，返回新实例（原实例不变）。"""
        return replace(
            self,
            max_profit_seen=max(self.max_profit_seen, profit),
            min_profit_seen=min(self.min_profit_seen, profit),
        )


def next_trade(
    current: Trade | None,
    *,
    symbol: str,
    net_qty: int,
    avg_cost: float,
    filled_at: datetime,
    entry_tag: str | None,
) -> Trade | None:
    """
    一笔成交之后，该标的的本笔交易应当变成什么（None = 已平掉）。

    纯函数，便于单测：持仓归零即关闭；方向翻转则开新的一笔；同向加仓把开仓价
    对齐到新的平均成本并**重置收益率极值** —— 基准变了，旧峰值再拿来做追踪止损
    会在完全没有回撤的情况下把新仓位打掉。
    """
    if net_qty == 0:
        return None

    direction: TradeDirection = "long" if net_qty > 0 else "short"
    if current is not None and current.direction == direction:
        reopened = abs(net_qty) > current.qty
        return replace(
            current,
            qty=abs(net_qty),
            open_price=avg_cost,
            max_profit_seen=0.0 if reopened else current.max_profit_seen,
            min_profit_seen=0.0 if reopened else current.min_profit_seen,
        )

    return Trade(
        symbol=symbol,
        direction=direction,
        open_time=filled_at,
        open_price=avg_cost,
        qty=abs(net_qty),
        entry_tag=entry_tag,
    )


@dataclass(frozen=True)
class ExitRules:
    """策略的类级声明式退出配置。三项全不配置 = 现状行为（风险闸门为 no-op）。"""

    stoploss: float | None = None
    trailing_stop: bool = False
    trailing_stop_positive: float | None = None
    trailing_stop_positive_offset: float = 0.0
    minimal_roi: dict[int, float] | None = None

    @property
    def is_enabled(self) -> bool:
        return (
            self.stoploss is not None
            or self.trailing_stop
            or bool(self.minimal_roi)
        )

    def validate(self) -> None:
        """配置自检。非法配置直接抛错，绝不降级成「静默不生效」。"""
        if self.stoploss is not None and self.stoploss >= 0:
            raise ValueError(f"stoploss 必须为负数（如 -0.10），收到 {self.stoploss}")
        if self.trailing_stop_positive is not None and self.trailing_stop_positive <= 0:
            raise ValueError(
                f"trailing_stop_positive 必须为正数，收到 {self.trailing_stop_positive}"
            )
        if self.trailing_stop_positive_offset < 0:
            raise ValueError("trailing_stop_positive_offset 不能为负")
        if self.trailing_stop and self._trailing_distance is None:
            raise ValueError(
                "追踪止损需要一个回撤距离：请配置 trailing_stop_positive 或 stoploss"
            )
        for minutes, target in (self.minimal_roi or {}).items():
            if minutes < 0:
                raise ValueError(f"minimal_roi 的持仓分钟数不能为负: {minutes}")
            if target < 0:
                raise ValueError(f"minimal_roi 的目标收益率不能为负: {target}")

    @property
    def _trailing_distance(self) -> float | None:
        if self.trailing_stop_positive is not None:
            return self.trailing_stop_positive
        if self.stoploss is not None:
            return abs(self.stoploss)
        return None


def roi_threshold(ladder: dict[int, float] | None, duration_minutes: int) -> float | None:
    """取「不超过持仓时长」的最大档位阈值；无匹配档位返回 None。"""
    if not ladder:
        return None
    reached = [minutes for minutes in ladder if minutes <= duration_minutes]
    if not reached:
        return None
    return ladder[max(reached)]


def evaluate_exit(
    rules: ExitRules,
    trade: Trade,
    profit: float,
    duration_minutes: int,
    *,
    custom_stoploss: float | None = None,
    custom_roi: float | None = None,
) -> str | None:
    """
    判定本笔是否应当离场，返回 exit_reason（不离场返回 None）。

    `trade.max_profit_seen` 必须**已经**包含本次的 profit（调用方先 `mark_profit`），
    否则追踪止损会在创出新高的那一根 bar 上少看一档峰值。

    两个 custom 钩子的返回值在这里校验：类级配置走 `ExitRules.validate()`，
    运行期覆盖若不校验就会绕开那道关 —— 一个返回正数的 `custom_stoploss` 会让
    `profit <= stoploss` 几乎每根 bar 都成立，仓位被无声打光。
    """
    _validate_overrides(custom_stoploss, custom_roi)

    target = custom_roi if custom_roi is not None else roi_threshold(
        rules.minimal_roi, duration_minutes
    )
    if target is not None and profit >= target:
        return EXIT_ROI

    stoploss = custom_stoploss if custom_stoploss is not None else rules.stoploss
    if stoploss is not None and profit <= stoploss:
        return EXIT_STOP_LOSS

    if rules.trailing_stop and _trailing_hit(rules, trade, profit):
        return EXIT_TRAILING_STOP
    return None


def _validate_overrides(custom_stoploss: float | None, custom_roi: float | None) -> None:
    if custom_stoploss is not None and custom_stoploss >= 0:
        raise ValueError(
            f"custom_stoploss 必须返回负数（如 -0.05）或 None，收到 {custom_stoploss}"
        )
    if custom_roi is not None and custom_roi < 0:
        raise ValueError(f"custom_roi 必须返回非负数或 None，收到 {custom_roi}")


def _trailing_hit(rules: ExitRules, trade: Trade, profit: float) -> bool:
    """峰值达到 offset 之后，回撤超过距离即触发。"""
    peak = trade.max_profit_seen
    if peak < rules.trailing_stop_positive_offset:
        return False
    distance = rules._trailing_distance
    if distance is None:
        raise ValueError("追踪止损缺少回撤距离配置（trailing_stop_positive / stoploss）")
    return profit <= peak - distance


__all__ = [
    "EXIT_ROI",
    "EXIT_STOP_LOSS",
    "EXIT_TRAILING_STOP",
    "RISK_EXIT_REASONS",
    "ExitRules",
    "Trade",
    "evaluate_exit",
    "next_trade",
    "roi_threshold",
]
