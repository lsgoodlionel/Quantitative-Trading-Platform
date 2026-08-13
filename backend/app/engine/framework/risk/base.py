"""RiskManagementModel 抽象 + 直通实现 + 各风控模型共用的小工具（Wave K-d / L-b）

接口对齐 Lean 的 `IRiskManagementModel`（Apache-2.0）：Lean 的签名是
`manage_risk(algorithm, targets)`，本项目的等价签名是
`manage_risk(ctx: PortfolioContext, targets) -> list[PortfolioTarget]`。

**输出的是修正后的目标，不是订单**：清仓一律表达成 `quantity=0`，由
`ExecutionModel` 算出 diff 后下单。风控模型直接调 `ctx.sell()` 是错的 ——
既绕过了执行模型，也拿不到「目标 - 持仓」的 diff 语义。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.engine.backtest.trade import Trade
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

#: 风控落在 `PortfolioTarget.tag` 上的原因；执行模型减仓时会把它写进
#: `Order.exit_reason`，最终进 tag_metrics 的分组统计
RISK_TAG_MAX_DRAWDOWN = "risk_max_drawdown"
RISK_TAG_PORTFOLIO_DRAWDOWN = "risk_portfolio_drawdown"
RISK_TAG_UNREALIZED_PROFIT = "risk_unrealized_profit"
RISK_TAG_TRAILING_STOP = "risk_trailing_stop"
RISK_TAG_SECTOR_EXPOSURE = "risk_sector_exposure"


class RiskManagementModel(ABC):
    """在目标下达执行之前调整/否决它们。"""

    name: str = "risk"

    @abstractmethod
    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        """返回调整后的目标（可以增删改）。"""


class NullRiskModel(RiskManagementModel):
    """直通：不做任何调整（默认）。"""

    name = "null_risk"

    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        return targets


# ── 共用工具 ─────────────────────────────────────────────────


def liquidate(target: PortfolioTarget, tag: str) -> PortfolioTarget:
    """把目标改写成清仓（`quantity=0`）并留下风控原因，返回**新实例**。"""
    return replace(target, quantity=0, tag=tag)


def rescale(target: PortfolioTarget, factor: float, tag: str) -> PortfolioTarget:
    """按比例缩减目标持仓。取整方向朝零 —— 宁可少暴露也不要多暴露。"""
    return replace(target, quantity=int(target.quantity * factor), tag=tag)


def open_trades(ctx: PortfolioContext) -> dict[str, Trade]:
    """
    各标的的「本笔交易」（由 K4 的 `RiskExitMixin` 维护）。

    浮盈/浮亏的基准必须取 `Trade.open_price` 而不是 `Position.avg_cost` ——
    K-d 已在 `next_trade()` 里处理了「同向加仓改变基准并重置收益率极值」，
    另算一套会与策略级止损给出互相矛盾的收益率。

    券商不提供该能力时（例如实盘的 OMS 上下文）返回空表：基于 Trade 的风控
    模型因此退化为 no-op，而不是拿一个错误的基准去平仓。
    """
    trades = getattr(ctx.broker, "open_trades", None)
    if trades is None:
        logger.debug("券商未提供 open_trades，基于 Trade 的风控模型本次为 no-op")
        return {}
    return trades


def require_ratio(name: str, value: float, *, upper: float = 1.0) -> float:
    """校验 (0, upper] 区间内的比例参数。非法配置直接抛错，不静默降级。"""
    if not 0.0 < value <= upper:
        raise ValueError(f"{name} 必须落在 (0, {upper}] 区间内，收到 {value}")
    return value


__all__ = [
    "RISK_TAG_MAX_DRAWDOWN",
    "RISK_TAG_PORTFOLIO_DRAWDOWN",
    "RISK_TAG_SECTOR_EXPOSURE",
    "RISK_TAG_TRAILING_STOP",
    "RISK_TAG_UNREALIZED_PROFIT",
    "NullRiskModel",
    "RiskManagementModel",
    "liquidate",
    "open_trades",
    "require_ratio",
    "rescale",
]
