"""落袋为安类组合风控模型（Wave L-b / L2）

语义对齐 Lean `Algorithm.Framework/Risk/MaximumUnrealizedProfitPercentPerSecurity`
（Apache-2.0）；本项目 `refs/` 下没有 Lean 源码副本，按契约描述独立实现。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.engine.framework.risk.base import (
    RISK_TAG_UNREALIZED_PROFIT,
    RiskManagementModel,
    liquidate,
    open_trades,
    require_ratio,
)
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.engine.backtest.trade import Trade
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)


class MaximumUnrealizedProfitPerSecurity(RiskManagementModel):
    """
    单标的浮盈达到 `max_profit` → 落袋，该标的目标清零。

    收益率基准同样取 `Trade.open_price`（带方向：空头跌即为盈），
    与单标的最大回撤共用一套口径。
    """

    name = "max_unrealized_profit_per_security"

    def __init__(self, max_profit: float = 0.05) -> None:
        self.max_profit = require_ratio("max_profit", max_profit, upper=float("inf"))

    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        trades = open_trades(ctx)
        if not trades:
            return targets
        return [self._checked(ctx, trades, target) for target in targets]

    def _checked(
        self,
        ctx: PortfolioContext,
        trades: dict[str, Trade],
        target: PortfolioTarget,
    ) -> PortfolioTarget:
        trade = trades.get(target.symbol)
        if trade is None:
            return target
        price = ctx.price(target.symbol)
        if price is None or price <= 0:
            logger.debug("%s 无可用价格，本次跳过浮盈判定", target.symbol)
            return target
        profit = trade.current_profit(price)
        if profit < self.max_profit:
            return target
        logger.debug("%s 浮盈 %.2f%% 触发落袋风控", target.symbol, profit * 100)
        return liquidate(target, RISK_TAG_UNREALIZED_PROFIT)


__all__ = ["MaximumUnrealizedProfitPerSecurity"]
