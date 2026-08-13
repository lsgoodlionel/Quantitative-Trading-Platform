"""PortfolioConstructionModel — 观点 → 目标持仓（Wave K-d / K5）

翻译自 Lean 的 `Algorithm.Framework/Portfolio/*PortfolioConstructionModel.py`
（Apache-2.0），按本项目的 `PortfolioContext` 做了适配。

与契约的一处结构差异（有意为之）：契约把 `create_targets` 写成抽象方法，
这里把它实现成**模板方法**，抽象点下沉到 `compute_weights`。
理由：观点去重、不可交易标的剔除、分组原子性、权重 → 股数换算、
以及「持有但已无观点 → 清仓」这几件事每个 PCM 都要做且必须一致，
让每个子类各写一遍是重复代码，也是 bug 温床。子类仍可整体覆盖 `create_targets`。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.engine.framework.insight import Insight, InsightDirection
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)


class PortfolioConstructionModel(ABC):
    """观点 → 目标持仓。"""

    def __init__(self, rebalance_period: timedelta | None = None) -> None:
        self._rebalance_period = rebalance_period
        self._last_rebalance: datetime | None = None

    @property
    def rebalance_period(self) -> timedelta | None:
        return self._rebalance_period

    # ── 扩展点 ───────────────────────────────────────────────

    @abstractmethod
    def compute_weights(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> dict[str, float]:
        """
        观点 → 目标权重（带符号，占组合净值的比例）。

        入参已经过滤过：每标的只剩最新一条、不可交易的已剔除、分组已保证完整。
        """

    def should_rebalance(self, now: datetime) -> bool:
        """
        是否**按节奏**触发一次再平衡。

        注意语义：观点变化本身总会触发再平衡（由 `FrameworkStrategy` 判断），
        这里只回答「即使观点没变，是否也到了调仓时点」。未配置周期时恒为 False，
        即「只在观点变化时调仓」—— 否则每根 bar 都会因价格漂移产生无意义的碎单。
        """
        if self._rebalance_period is None:
            return False
        if self._last_rebalance is None or now - self._last_rebalance >= self._rebalance_period:
            self._last_rebalance = now
            return True
        return False

    # ── 模板方法 ─────────────────────────────────────────────

    def determine_target_percent(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> dict[str, float]:
        """过滤观点后计算目标权重。"""
        return self.compute_weights(ctx, self._usable(ctx, insights))

    def create_targets(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> list[PortfolioTarget]:
        """目标权重 → 目标股数，并为「持有但已无观点」的标的补上清仓目标。"""
        usable = self._usable(ctx, insights)
        percents = self.compute_weights(ctx, usable)
        groups = {i.symbol: i.group_id for i in usable}
        equity = ctx.portfolio_value

        targets = [
            PortfolioTarget(
                symbol=symbol,
                quantity=self._target_qty(ctx, symbol, percent, equity),
                tag=type(self).__name__,
                group_id=groups.get(symbol),
            )
            for symbol, percent in sorted(percents.items())
        ]
        targets.extend(self._liquidations(ctx, set(percents)))
        return targets

    # ── 内部工具 ─────────────────────────────────────────────

    @staticmethod
    def _target_qty(
        ctx: PortfolioContext, symbol: str, percent: float, equity: float
    ) -> int:
        price = ctx.price(symbol)
        if price is None or price <= 0:
            return 0
        return int(equity * percent / price)     # int() 向零取整 = 保守暴露

    @staticmethod
    def _liquidations(
        ctx: PortfolioContext, covered: set[str]
    ) -> list[PortfolioTarget]:
        """持有但本轮没有观点的标的必须显式清零，否则会被无限期遗留在账上。"""
        return [
            PortfolioTarget(symbol=symbol, quantity=0, tag="liquidate")
            for symbol in sorted(ctx.broker.positions.open_symbols)
            if symbol not in covered
        ]

    def _usable(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> list[Insight]:
        """每标的保留最新一条 → 剔除无价标的 → 丢弃残缺的分组。"""
        latest = self._latest_per_symbol(insights)
        untradable = {i.symbol for i in latest if not _is_tradable(ctx, i.symbol)}
        if not untradable:
            return latest

        broken = {i.group_id for i in latest if i.symbol in untradable and i.group_id}
        if broken:
            logger.warning(
                "观点分组 %s 中有腿无法交易，整组丢弃以避免留下单边敞口", sorted(broken)
            )
        return [
            i for i in latest
            if i.symbol not in untradable and i.group_id not in broken
        ]

    @staticmethod
    def _latest_per_symbol(insights: list[Insight]) -> list[Insight]:
        latest: dict[str, Insight] = {}
        for insight in insights:
            current = latest.get(insight.symbol)
            if current is None or insight.generated_at >= current.generated_at:
                latest[insight.symbol] = insight
        return [latest[symbol] for symbol in sorted(latest)]


def _is_tradable(ctx: PortfolioContext, symbol: str) -> bool:
    price = ctx.price(symbol)
    return price is not None and price > 0


class EqualWeightingPCM(PortfolioConstructionModel):
    """等权：每个非 FLAT 观点分到 1/N。FLAT 观点目标权重为 0（即清仓）。"""

    def compute_weights(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> dict[str, float]:
        active = [i for i in insights if i.direction is not InsightDirection.FLAT]
        percent = 1.0 / len(active) if active else 0.0
        return {i.symbol: int(i.direction) * percent for i in insights}


class InsightWeightingPCM(PortfolioConstructionModel):
    """
    按 `insight.weight` 加权。没有 weight 的观点被忽略（与 Lean 一致）。

    权重绝对值之和 > 1 时按比例缩放到 1 —— 无保证金模型时超配只会在撮合阶段
    因现金不足被随机截断，还不如在这里按比例压缩，至少是可解释的。
    """

    def compute_weights(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> dict[str, float]:
        weighted = [i for i in insights if i.weight is not None]
        total = sum(abs(i.weight) for i in weighted if i.direction is not InsightDirection.FLAT)
        factor = 1.0 / total if total > 1.0 else 1.0
        return {
            i.symbol: int(i.direction) * abs(i.weight) * factor
            for i in weighted
        }


__all__ = [
    "EqualWeightingPCM",
    "InsightWeightingPCM",
    "PortfolioConstructionModel",
]
