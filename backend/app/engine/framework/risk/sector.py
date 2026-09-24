"""行业敞口风控（Wave L-b / L2）

语义对齐 Lean `Algorithm.Framework/Risk/MaximumSectorExposureRiskManagementModel`
（Apache-2.0）；本项目 `refs/` 下没有 Lean 源码副本，按契约描述独立实现。

**行业数据从哪来**：本项目没有统一的行业字典（`app/data/symbol_dict.py` 只有
中文名，行业分类是蓝图 C4 的缺口）。因此行业经 `sector_of` 注入，签名
`Callable[[str], str | None]`。

**拿不到行业必须跳过该标的**（契约 §2.1.2）：把所有未知行业的标的并成同一个
"None 行业"，会让它们的敞口相加成一个巨大的单一行业敞口，然后被整体砍掉 ——
这不是风控，这是随机减仓。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.engine.framework.risk.base import (
    RISK_TAG_SECTOR_EXPOSURE,
    RiskManagementModel,
    require_ratio,
    rescale,
)
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

SectorResolver = Callable[[str], "str | None"]


def default_sector_of(symbol: str) -> str | None:
    """
    默认行业解析：本项目尚无行业分类体系（蓝图 C4），一律返回 None。

    结果是 `MaximumSectorExposure` 在不显式注入 `sector_of` 时是 no-op ——
    这是刻意的：宁可不生效，也不要拿一个假的行业划分去砍仓位。
    """
    return None


class MaximumSectorExposure(RiskManagementModel):
    """
    单行业目标敞口超过 `max_exposure` → 按比例缩减该行业内**各**标的目标。

    敞口 = 该行业各目标的名义金额绝对值之和 / 组合净值。多空同时计入毛敞口
    （对冲不抵消），因为行业集中风险来自总头寸规模而不是净头寸。
    """

    name = "max_sector_exposure"

    def __init__(
        self,
        max_exposure: float = 0.20,
        sector_of: SectorResolver | None = None,
    ) -> None:
        self.max_exposure = require_ratio("max_exposure", max_exposure)
        self.sector_of = sector_of or default_sector_of

    def manage_risk(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[PortfolioTarget]:
        equity = ctx.portfolio_value
        if equity <= 0:
            logger.warning("组合净值 %.2f 不可用，行业敞口风控本次跳过", equity)
            return targets

        sectors = self._sectors_of(ctx, targets)
        if not sectors:
            return targets

        factors = self._scale_factors(ctx, targets, sectors, equity)
        if not factors:
            return targets
        return [self._scaled(target, sectors, factors) for target in targets]

    # ── 内部 ─────────────────────────────────────────────────

    def _sectors_of(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> dict[str, str]:
        """`symbol → 行业`，行业未知或无可用价格的标的直接不进这张表（= 跳过）。"""
        resolved: dict[str, str] = {}
        for target in targets:
            sector = self.sector_of(target.symbol)
            if sector is None:
                logger.debug("%s 行业未知，行业敞口风控跳过该标的", target.symbol)
                continue
            price = ctx.price(target.symbol)
            if price is None or price <= 0:
                logger.debug("%s 无可用价格，无法折算敞口，跳过", target.symbol)
                continue
            resolved[target.symbol] = sector
        return resolved

    def _scale_factors(
        self,
        ctx: PortfolioContext,
        targets: list[PortfolioTarget],
        sectors: dict[str, str],
        equity: float,
    ) -> dict[str, float]:
        """超限行业 → 缩减系数（未超限的行业不出现在结果里）。"""
        exposure: dict[str, float] = {}
        for target in targets:
            sector = sectors.get(target.symbol)
            if sector is None:
                continue
            price = ctx.price(target.symbol) or 0.0
            exposure[sector] = exposure.get(sector, 0.0) + abs(target.quantity) * price

        factors: dict[str, float] = {}
        for sector, notional in exposure.items():
            ratio = notional / equity
            if ratio > self.max_exposure:
                factors[sector] = self.max_exposure / ratio
                logger.info(
                    "行业 %s 目标敞口 %.2f%% 超过上限 %.2f%%，按 %.4f 缩减",
                    sector, ratio * 100, self.max_exposure * 100, factors[sector],
                )
        return factors

    @staticmethod
    def _scaled(
        target: PortfolioTarget, sectors: dict[str, str], factors: dict[str, float]
    ) -> PortfolioTarget:
        factor = factors.get(sectors.get(target.symbol, ""))
        if factor is None:
            return target
        return rescale(target, factor, RISK_TAG_SECTOR_EXPOSURE)


__all__ = ["MaximumSectorExposure", "SectorResolver", "default_sector_of"]
