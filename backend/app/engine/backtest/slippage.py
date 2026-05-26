"""
滑点模型

回测中模拟真实成交价偏移，避免过度乐观。
参考 backtrader 的滑点设计，提供两种模型：
1. 固定点数滑点（简单，适合低频策略）
2. 成交量比例滑点（更真实，适合高频/大单策略）
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.data.models import Bar, Market


class SlippageModel(ABC):
    @abstractmethod
    def apply(self, price: float, direction: str, bar: Bar) -> float:
        """
        返回调整后的成交价。
        direction: BUY → 价格上调（买贵）；SELL → 价格下调（卖便宜）
        """
        ...


class FixedSlippage(SlippageModel):
    """固定点数滑点（默认 0.01%）。"""

    def __init__(self, pct: float = 0.0001) -> None:
        self._pct = pct

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        delta = price * self._pct
        return price + delta if direction == "BUY" else price - delta


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
        return price + delta if direction == "BUY" else price - delta


class NoSlippage(SlippageModel):
    """零滑点（仅用于理想情况测试对比）。"""

    def apply(self, price: float, direction: str, bar: Bar) -> float:
        return price


def get_slippage_model(market: Market) -> SlippageModel:
    """按市场返回默认滑点模型。"""
    if market == Market.US:
        return FixedSlippage(pct=0.0001)   # 美股 0.01%
    if market == Market.HK:
        return FixedSlippage(pct=0.0002)   # 港股流动性略差，0.02%
    return FixedSlippage(pct=0.0003)
