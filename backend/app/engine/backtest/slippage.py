"""
滑点模型

回测中模拟真实成交价偏移，避免过度乐观。
参考 backtrader 的滑点设计，提供两种模型：
1. 固定点数滑点（简单，适合低频策略）
2. 成交量比例滑点（更真实，适合高频/大单策略）

Wave K-b / K6 新增「成交量约束」维度：滑点模型除了调价（`apply`），还可以
通过 `fill_limit()` 限制单根 bar 的最大成交量，由 broker 产生部分成交。

冲击模型形式参考 zipline（Apache License 2.0）的
`zipline/finance/slippage.py::VolumeShareSlippage`：
    Copyright 2016 Quantopian, Inc.
    Licensed under the Apache License, Version 2.0
本文件为独立实现，仅沿用其公开的数学形式（冲击 ∝ 成交占比的平方）。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.data.models import Bar, Market

# 单根 bar 默认可吃掉的成交量占比（对齐 zipline VolumeShareSlippage 默认值）
DEFAULT_VOLUME_LIMIT = 0.025
# 平方冲击系数：impact = price_impact × share²
DEFAULT_PRICE_IMPACT = 0.1
# 平方根冲击系数：impact = impact_coef × sqrt(share)
DEFAULT_IMPACT_COEF = 0.02

_BUY = "BUY"


@dataclass(frozen=True)
class FillLimit:
    """
    一次撮合允许成交的最大数量。

    max_qty: 本根 bar 最多可成交的股数（0 表示本 bar 完全无法成交）。
    reason:  被截断时的诊断信息，broker 会写入 `Order.reject_reason`。
    """

    max_qty: int
    reason: str | None = None


class SlippageModel(ABC):
    @abstractmethod
    def apply(self, price: float, direction: str, bar: Bar) -> float:
        """
        返回调整后的成交价。
        direction: BUY → 价格上调（买贵）；SELL → 价格下调（卖便宜）
        """
        ...

    def apply_qty(self, price: float, direction: str, bar: Bar, qty: int) -> float:
        """
        带成交数量的滑点计算。

        默认忽略 qty 直接回退到 `apply()`，因此既有模型行为完全不变；
        需要按订单大小算市场冲击的模型（K6）覆盖此方法。
        """
        return self.apply(price, direction, bar)

    def fill_limit(self, order_qty: int, bar: Bar) -> FillLimit:
        """本根 bar 的成交量上限。默认不限量 —— 保持既有模型行为不变。"""
        return FillLimit(max_qty=order_qty)


class FixedSlippage(SlippageModel):
    """固定点数滑点（默认 0.01%）。"""

    def __init__(self, pct: float = 0.0001) -> None:
        self._pct = pct

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        delta = price * self._pct
        return price + delta if direction == _BUY else price - delta


class VolumeSlippage(SlippageModel):
    """
    成交量比例滑点。

    订单量占当根 K 线成交量比例越大，滑点越高。
    适合需要考虑市场冲击的中大型订单回测。
    """

    def __init__(self, volume_limit: float = 0.1, pct_per_volume: float = 0.005) -> None:
        self._volume_limit = volume_limit      # 单笔最多占 bar 成交量的 10%
        self._pct_per_volume = pct_per_volume  # 每 1% 占比产生 0.5% 滑点

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        # 固定使用 0.05% 基础滑点（无法知道单笔数量，由引擎层传入比例）
        slippage_pct = 0.0005
        delta = price * slippage_pct
        return price + delta if direction == _BUY else price - delta


class NoSlippage(SlippageModel):
    """零滑点（仅用于理想情况测试对比）。"""

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        return price


class _VolumeConstrainedSlippage(SlippageModel):
    """
    「成交量上限 + 市场冲击」两类模型的公共骨架（K6）。

    子类只需实现 `_impact_pct(share)`：给定成交占比返回冲击比例。
    """

    def __init__(self, volume_limit: float = DEFAULT_VOLUME_LIMIT) -> None:
        if not 0.0 < volume_limit <= 1.0:
            raise ValueError(f"volume_limit 必须落在 (0, 1]，收到 {volume_limit}")
        self._volume_limit = volume_limit

    @property
    def volume_limit(self) -> float:
        return self._volume_limit

    @abstractmethod
    def _impact_pct(self, share: float) -> float:
        """成交占比 → 冲击比例（非负）。"""
        ...

    def fill_limit(self, order_qty: int, bar: Bar) -> FillLimit:
        max_qty = int(self._volume_limit * max(bar.volume, 0))
        if max_qty >= order_qty:
            return FillLimit(max_qty=order_qty)
        return FillLimit(
            max_qty=max_qty,
            reason=(
                f"成交量约束: bar 成交量 {bar.volume}，单 bar 上限 "
                f"{self._volume_limit:.2%} → 最多成交 {max_qty} 股（请求 {order_qty} 股）"
            ),
        )

    def _share(self, qty: int, bar: Bar) -> float:
        """成交占比，硬性封顶在 volume_limit（超出部分本来就成交不了）。"""
        if bar.volume <= 0:
            return self._volume_limit
        return min(qty / bar.volume, self._volume_limit)

    def _adjust(self, price: float, direction: str, share: float) -> float:
        delta = price * self._impact_pct(share)
        return price + delta if direction == _BUY else price - delta

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        # 不知道数量时按上限估计（最保守），避免低估冲击
        return self._adjust(price, direction, self._volume_limit)

    def apply_qty(self, price: float, direction: str, bar: Bar, qty: int) -> float:
        return self._adjust(price, direction, self._share(qty, bar))


class VolumeShareSlippage(_VolumeConstrainedSlippage):
    """
    成交量份额滑点（移植自 zipline `VolumeShareSlippage`，Apache-2.0）。

    - 单 bar 最多成交 `volume_limit × bar.volume`（默认 2.5%）
    - 冲击 = `price_impact × 成交占比²`

    平方形式让小单几乎无成本、大单快速受罚，刻画「吃穿盘口深度」的非线性代价。
    """

    def __init__(
        self,
        volume_limit: float = DEFAULT_VOLUME_LIMIT,
        price_impact: float = DEFAULT_PRICE_IMPACT,
    ) -> None:
        super().__init__(volume_limit)
        if price_impact < 0.0:
            raise ValueError(f"price_impact 不能为负，收到 {price_impact}")
        self._price_impact = price_impact

    def _impact_pct(self, share: float) -> float:
        return self._price_impact * share * share


class MarketImpactSlippage(_VolumeConstrainedSlippage):
    """
    平方根市场冲击模型（Almgren 平方根律；zipline 同族模型的简化形式）。

    冲击 = `impact_coef × sqrt(成交占比)`。相比平方形式不会低估小单的冲击，
    更贴近大单实际执行成本（实证上冲击约与参与率的 0.5 次方成正比）。
    """

    def __init__(
        self,
        volume_limit: float = DEFAULT_VOLUME_LIMIT,
        impact_coef: float = DEFAULT_IMPACT_COEF,
    ) -> None:
        super().__init__(volume_limit)
        if impact_coef < 0.0:
            raise ValueError(f"impact_coef 不能为负，收到 {impact_coef}")
        self._impact_coef = impact_coef

    def _impact_pct(self, share: float) -> float:
        return self._impact_coef * math.sqrt(max(share, 0.0))


def get_slippage_model(market: Market) -> SlippageModel:
    """按市场返回默认滑点模型。"""
    if market == Market.US:
        return FixedSlippage(pct=0.0001)   # 美股 0.01%
    if market == Market.HK:
        return FixedSlippage(pct=0.0002)   # 港股流动性略差，0.02%
    return FixedSlippage(pct=0.0003)
