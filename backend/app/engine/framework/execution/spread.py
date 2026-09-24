"""价差择时执行模型（Wave L-b / L3）

语义对齐 Lean `Algorithm.Framework/Execution/SpreadExecutionModel`（Apache-2.0）；
本项目 `refs/` 下没有 Lean 源码副本，按契约描述独立实现。
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


class SpreadExecution(ExecutionModel):
    """
    价差收窄到 `accepted_spread_percent` 以内才成交，否则等下一次调仓。

    ⚠️ **这是近似，不是真实盘口价差**。本项目当前只有 OHLCV，没有 bid/ask
    （Tick / QuoteBar 是蓝图 C3，Wave M+）。这里用 `(high - low) / close` ——
    也就是**这根 bar 的日内波动幅度** —— 作为流动性代理：

    - 它衡量的是一段时间内的价格区间，而真实价差衡量的是某一瞬间的买卖报价距离；
    - 数值上通常**显著高于**真实价差（日线尤甚），Lean 那套按真实盘口标定的
      经验阈值（如 0.5%）直接套用会导致几乎永远不成交；
    - 阈值必须按标的与 bar 周期自行标定。

    真实盘口到位后，把 `_spread_percent()` 换成 `(ask - bid) / mid` 即可，
    模型的其余逻辑不变。
    """

    name = "spread"

    def __init__(self, accepted_spread_percent: float = 0.005) -> None:
        if accepted_spread_percent <= 0:
            raise ValueError(
                f"accepted_spread_percent 必须为正数，收到 {accepted_spread_percent}"
            )
        self.accepted_spread_percent = accepted_spread_percent

    def execute(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[Order]:
        return [
            to_order(ctx, target, diff)
            for target, diff in build_plan(ctx, targets)
            if self._is_tight_enough(ctx, target.symbol)
        ]

    def _is_tight_enough(self, ctx: PortfolioContext, symbol: str) -> bool:
        spread = self._spread_percent(ctx, symbol)
        if spread is None:
            return False
        if spread > self.accepted_spread_percent:
            logger.debug(
                "%s 近似价差 %.4f%% 超过阈值 %.4f%%，本次不成交",
                symbol, spread * 100, self.accepted_spread_percent * 100,
            )
            return False
        return True

    @staticmethod
    def _spread_percent(ctx: PortfolioContext, symbol: str) -> float | None:
        """`(high - low) / close` 近似价差；本时点无 bar 或价格非法时返回 None。"""
        bar = ctx.bar(symbol)
        if bar is None:
            logger.debug("%s 本时点无 bar，无法估算价差，跳过", symbol)
            return None
        if bar.close <= 0:
            logger.warning("%s 收盘价 %.4f 非法，无法估算价差，跳过", symbol, bar.close)
            return None
        return (bar.high - bar.low) / bar.close


__all__ = ["SpreadExecution"]
