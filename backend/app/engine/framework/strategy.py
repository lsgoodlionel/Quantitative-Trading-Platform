"""FrameworkStrategy — 把四个模型串成一个可跑的组合策略（Wave K-d / K5）

    Alpha → Insight → PortfolioConstruction → PortfolioTarget → Risk → Execution → Order

同一个 `FrameworkStrategy` 必须能在回测与实盘跑：这里只依赖 `PortfolioContext`
的接口（`bars` / `histories` / `qty` / `submit`），实盘由 `StrategyEngine` 提供
等价实现即可，`ctx.submit` 路由到 OMS。本期只保证接口可行，实盘接线在 Wave L。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.engine.framework.alpha import AlphaModel
from app.engine.framework.execution import ExecutionModel, ImmediateExecutionModel
from app.engine.framework.insight import Insight
from app.engine.framework.portfolio_construction import PortfolioConstructionModel
from app.engine.framework.risk import NullRiskModel, RiskManagementModel
from app.strategy.base import PortfolioStrategyBase

if TYPE_CHECKING:
    from datetime import datetime

    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)


class FrameworkStrategy(PortfolioStrategyBase):
    """四段式编排。"""

    name = "framework"

    def __init__(
        self,
        alpha: AlphaModel,
        portfolio_construction: PortfolioConstructionModel,
        risk: RiskManagementModel | None = None,
        execution: ExecutionModel | None = None,
        params: dict | None = None,
    ) -> None:
        super().__init__(params)
        if not isinstance(alpha, AlphaModel):
            raise TypeError(f"alpha 必须是 AlphaModel，收到 {type(alpha).__name__}")
        if not isinstance(portfolio_construction, PortfolioConstructionModel):
            raise TypeError(
                f"portfolio_construction 必须是 PortfolioConstructionModel，"
                f"收到 {type(portfolio_construction).__name__}"
            )
        self.alpha = alpha
        self.pcm = portfolio_construction
        self.risk = risk or NullRiskModel()
        self.execution = execution or ImmediateExecutionModel()
        self.name = f"framework:{alpha.name}"
        #: (source, symbol) → 当前生效的观点
        self._active: dict[tuple[str, str], Insight] = {}
        #: 全部产出过的观点，供测试与后续归因（N4）回溯
        self.emitted: list[Insight] = []

    # ── 生命周期 ─────────────────────────────────────────────

    def on_start(self, ctx: PortfolioContext) -> None:
        self.alpha.on_start(ctx)

    def on_bars(self, ctx: PortfolioContext) -> None:
        insights = self.alpha.update(ctx)
        self.emitted.extend(insights)
        expired = self._refresh(insights, ctx.time)

        # 调仓时机：观点有变（新观点/过期）或到了配置的再平衡节奏。
        # 注意不要短路 —— should_rebalance 会推进内部计时器，必须每根 bar 都问一次。
        due = self.pcm.should_rebalance(ctx.time)
        if not (insights or expired or due):
            return

        targets = self.pcm.create_targets(ctx, list(self._active.values()))
        targets = self.risk.manage_risk(ctx, targets)
        for order in self.execution.execute(ctx, targets):
            ctx.submit(order)

    # ── 观点集合维护 ─────────────────────────────────────────

    def _refresh(self, insights: list[Insight], now: datetime) -> bool:
        """并入新观点并剔除过期观点，返回「是否有观点因过期被剔除」。"""
        for insight in insights:
            self._active[(insight.source, insight.symbol)] = insight

        stale = [k for k, i in self._active.items() if i.is_expired_at(now)]
        for key in stale:
            del self._active[key]
        return bool(stale)


__all__ = ["FrameworkStrategy"]
