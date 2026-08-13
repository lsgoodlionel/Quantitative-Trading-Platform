"""回撤类组合风控模型（Wave L-b / L2）

三个模型，共同点是「跌得太多就砍」，差别在于比较基准：

| 模型 | 基准 | 作用范围 |
|---|---|---|
| `MaximumDrawdownPerSecurity` | 本笔交易的开仓价 | 单个标的 |
| `MaximumDrawdownPortfolio` | 组合净值高水位 | 全部目标 |
| `TrailingStopRiskManagement` | 本笔持仓期内的最高浮盈 | 单个标的 |

语义对齐 Lean `Algorithm.Framework/Risk` 下的同名模型（Apache-2.0）。
本项目 `refs/` 下没有 Lean 源码副本，此处按契约描述的语义独立实现。

**阈值边界**：一律取「达到即触发」（`<=`），与 K4 `evaluate_exit()` 的
`profit <= stoploss` 口径一致 —— 两套闸门用同一个边界才不会互相矛盾。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.engine.framework.risk.base import (
    RISK_TAG_MAX_DRAWDOWN,
    RISK_TAG_PORTFOLIO_DRAWDOWN,
    RISK_TAG_TRAILING_STOP,
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

#: 本笔交易的身份指纹：开仓基准一变（换向 / 同向加仓）就要重置追踪状态
_TradeKey = tuple[str, float, object]


def _fingerprint(trade: Trade) -> _TradeKey:
    return (trade.direction, trade.open_price, trade.open_time)


class MaximumDrawdownPerSecurity(RiskManagementModel):
    """单标的浮亏达到 `max_drawdown` → 该标的目标清零。"""

    name = "max_drawdown_per_security"

    def __init__(self, max_drawdown: float = 0.05) -> None:
        self.max_drawdown = require_ratio("max_drawdown", max_drawdown)

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
        profit = _current_profit(ctx, trades, target.symbol)
        if profit is None or profit > -self.max_drawdown:
            return target
        logger.debug("%s 浮亏 %.2f%% 触发单标的最大回撤风控", target.symbol, profit * 100)
        return liquidate(target, RISK_TAG_MAX_DRAWDOWN)


class MaximumDrawdownPortfolio(RiskManagementModel):
    """
    组合净值较高水位回撤达到 `max_drawdown` → **全部**目标清零。

    `is_trailing=False`（默认）时高水位在首次调用时锁定，之后不再抬高：
    衡量的是「相对起点亏了多少」。`is_trailing=True` 时高水位随净值创新高而
    上移：衡量的是「相对峰值回吐了多少」。

    触发清仓后高水位复位，下一次调用重新起算 —— 否则一旦破线就会在同一个
    高水位下永远处于触发态，此后任何建仓都会被立刻打掉。
    """

    name = "max_drawdown_portfolio"

    def __init__(self, max_drawdown: float = 0.05, is_trailing: bool = False) -> None:
        self.max_drawdown = require_ratio("max_drawdown", max_drawdown)
        self.is_trailing = is_trailing
        self._high_water_mark: float | None = None

    @property
    def high_water_mark(self) -> float | None:
        """当前生效的净值高水位（None = 尚未起算）。"""
        return self._high_water_mark

    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        value = ctx.portfolio_value
        if value <= 0:
            logger.warning("组合净值 %.2f 不可用，组合回撤风控本次跳过", value)
            return targets

        if self._high_water_mark is None:
            self._high_water_mark = value
        elif self.is_trailing and value > self._high_water_mark:
            self._high_water_mark = value

        drawdown = value / self._high_water_mark - 1.0
        if drawdown > -self.max_drawdown:
            return targets

        logger.info("组合净值较高水位回撤 %.2f%%，清空全部目标", drawdown * 100)
        self._high_water_mark = None          # 复位，下次调用重新起算
        return [liquidate(t, RISK_TAG_PORTFOLIO_DRAWDOWN) for t in targets]


class TrailingStopRiskManagement(RiskManagementModel):
    """
    从本笔持仓期内的**最高浮盈**回撤达到 `max_drawdown` → 该标的目标清零。

    峰值由本模型自己跟踪（`Trade.max_profit_seen` 只在 K4 风险闸门开启时才会
    被刷新，不能依赖）。峰值的**下限**取 0.0，与 `Trade.max_profit_seen` 的默认值
    一致（首次观测时取 `max(0.0, 当前收益率)`）：从未盈利过的仓位因此等价于
    普通的 `-max_drawdown` 止损。

    同向加仓 / 换向会改变 `Trade.open_price`，收益率基准随之改变，峰值必须
    一起重置 —— 否则加仓后的新仓位会在完全没有回撤的情况下被旧峰值打掉
    （K-d `next_trade()` 已在 Trade 层做了同样的事）。
    """

    name = "trailing_stop_risk"

    def __init__(self, max_drawdown: float = 0.05) -> None:
        self.max_drawdown = require_ratio("max_drawdown", max_drawdown)
        #: symbol → (本笔交易指纹, 收益率峰值)
        self._peaks: dict[str, tuple[_TradeKey, float]] = {}

    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        live = self._refresh_peaks(ctx)
        if not live:
            return targets
        return [self._checked(live, target) for target in targets]

    def _checked(
        self, live: dict[str, tuple[float, float]], target: PortfolioTarget
    ) -> PortfolioTarget:
        tracked = live.get(target.symbol)
        if tracked is None:
            return target
        peak, profit = tracked
        if profit > peak - self.max_drawdown:
            return target
        logger.debug(
            "%s 收益率 %.2f%% 较峰值 %.2f%% 回撤过大，触发追踪止损",
            target.symbol, profit * 100, peak * 100,
        )
        return liquidate(target, RISK_TAG_TRAILING_STOP)

    def _refresh_peaks(self, ctx: PortfolioContext) -> dict[str, tuple[float, float]]:
        """刷新各标的的收益率峰值，返回 `{symbol: (峰值, 当前收益率)}`。"""
        trades = open_trades(ctx)
        self._forget(set(trades))

        live: dict[str, tuple[float, float]] = {}
        for symbol, trade in trades.items():
            profit = _current_profit(ctx, trades, symbol)
            if profit is None:
                continue
            key = _fingerprint(trade)
            previous = self._peaks.get(symbol)
            same_basis = previous is not None and previous[0] == key
            peak = max(previous[1], profit) if same_basis else max(0.0, profit)
            self._peaks[symbol] = (key, peak)
            live[symbol] = (peak, profit)
        return live

    def _forget(self, alive: set[str]) -> None:
        """持仓已平掉的标的不再占用状态，避免重新建仓时沿用上一笔的峰值。"""
        for symbol in set(self._peaks) - alive:
            del self._peaks[symbol]


def _current_profit(
    ctx: PortfolioContext, trades: dict[str, Trade], symbol: str
) -> float | None:
    """本笔交易的带方向浮动收益率；无持仓或无可用价格时返回 None。"""
    trade = trades.get(symbol)
    if trade is None:
        return None
    price = ctx.price(symbol)
    if price is None or price <= 0:
        logger.debug("%s 无可用价格，本次跳过浮盈判定", symbol)
        return None
    return trade.current_profit(price)


__all__ = [
    "MaximumDrawdownPerSecurity",
    "MaximumDrawdownPortfolio",
    "TrailingStopRiskManagement",
]
