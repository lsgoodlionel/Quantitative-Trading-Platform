"""标准差择时执行模型（Wave L-b / L3）

语义对齐 Lean `Algorithm.Framework/Execution/StandardDeviationExecutionModel`
（Apache-2.0）；本项目 `refs/` 下没有 Lean 源码副本，按契约描述独立实现。

只在价格相对近期均值**朝有利方向**偏离 N 个标准差时才成交：
买单等价格跌到均值下方 N 倍标准差，卖单等价格涨到均值上方 N 倍标准差。
不满足条件的这一根 bar 不下单，等下一次调仓再判定。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.engine.backtest.broker import Order
from app.engine.framework.execution.base import ExecutionModel, build_plan, to_order
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

#: 计算标准差所需的最少样本数
_MIN_PERIOD = 2


class StandardDeviationExecution(ExecutionModel):
    """价格偏离均值达 `deviations` 个标准差且方向有利时才成交。"""

    name = "standard_deviation"

    def __init__(self, period: int = 60, deviations: float = 2.0) -> None:
        if period < _MIN_PERIOD:
            raise ValueError(f"period 至少为 {_MIN_PERIOD}，收到 {period}")
        if deviations <= 0:
            raise ValueError(f"deviations 必须为正数，收到 {deviations}")
        self.period = period
        self.deviations = deviations

    def execute(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[Order]:
        return [
            to_order(ctx, target, diff)
            for target, diff in build_plan(ctx, targets)
            if self._is_favorable(ctx, target.symbol, diff)
        ]

    def _is_favorable(self, ctx: PortfolioContext, symbol: str, diff: int) -> bool:
        """价格是否已经朝有利方向偏离足够远。样本不足一律判为不成交。"""
        band = self._band(ctx, symbol)
        if band is None:
            return False
        mean, deviation = band
        price = ctx.price(symbol)
        if price is None:                       # build_plan 已过滤，这里只是兜底
            return False
        return price < mean - deviation if diff > 0 else price > mean + deviation

    def _band(self, ctx: PortfolioContext, symbol: str) -> tuple[float, float] | None:
        """返回 `(均值, deviations × 标准差)`；历史不足或缺失时返回 None。"""
        try:
            closes = ctx.close_series(symbol, self.period)
        except KeyError:
            logger.warning("%s 没有历史数据，标准差执行模型本次不下单", symbol)
            return None
        if len(closes) < self.period:
            logger.debug(
                "%s 历史仅 %d 根，不足 %d 根，标准差执行模型本次不下单",
                symbol, len(closes), self.period,
            )
            return None
        # ddof=0：与 Lean 的 StandardDeviation 指标一致，取总体标准差
        return float(closes.mean()), self.deviations * float(closes.std(ddof=0))


__all__ = ["StandardDeviationExecution"]
