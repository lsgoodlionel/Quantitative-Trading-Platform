"""FormulaFactorAlphaModel — RPN 公式因子 → Insight（Wave K-d / K5）

这就是 V3 记录的断链「因子挖掘结果只存 Redis 实验记录，无法交易」的接口：
因子产出的是**截面打分**，策略要的是**下单**，`Insight` 是中间的承接物。

打分复用既有的 `app/quant/formula_factor.evaluate_formula`（RPN 栈式求值），
本模块只负责「打分 → 分位阈值 → 方向 + 建议权重」。
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import TYPE_CHECKING

from app.engine.framework.alpha import AlphaModel
from app.engine.framework.insight import Insight, InsightDirection
from app.quant.formula_factor import evaluate_formula

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

#: 截面比较至少需要两个标的
MIN_CROSS_SECTION = 2
#: 默认只取最近这么多根 bar 求值（滚动算子够用，且成本不随回测长度增长）
DEFAULT_EVAL_WINDOW = 250


class FormulaFactorAlphaModel(AlphaModel):
    """
    按截面分位把因子打分转成观点。

    - 分数 ≥ `long_quantile` 分位 → UP
    - 分数 ≤ `short_quantile` 分位 → DOWN（未配置则这些标的记 FLAT）
    - 其余 → FLAT（有仓则清仓）

    `weight` 给的是被选中标的内部按分数绝对值归一化的建议权重，
    这样 `InsightWeightingPCM` 可以直接用，`EqualWeightingPCM` 也不受影响。
    """

    def __init__(
        self,
        tokens: list[str],
        *,
        period: timedelta = timedelta(days=5),
        long_quantile: float = 0.8,
        short_quantile: float | None = None,
        min_history: int = 60,
        eval_window: int = DEFAULT_EVAL_WINDOW,
        name: str = "formula_factor",
    ) -> None:
        if not tokens:
            raise ValueError("tokens 不能为空")
        # 允许 0.0：分数 ≥ 最小值 ⇒ 全体做多。这是「不做截面筛选、只靠 PCM 定权重」
        # 的合法配置（如 A-a 的 max_positions 按权重取前 N），不该被挡在门外。
        if not 0.0 <= long_quantile <= 1.0:
            raise ValueError(f"long_quantile 必须落在 [0, 1]，收到 {long_quantile}")
        if short_quantile is not None and not 0.0 <= short_quantile < long_quantile:
            raise ValueError("short_quantile 必须小于 long_quantile 且非负")

        self._tokens = list(tokens)
        self._period = period
        self._long_q = long_quantile
        self._short_q = short_quantile
        self._min_history = min_history
        self._eval_window = eval_window
        self.name = name

    def update(self, ctx: PortfolioContext) -> list[Insight]:
        scores = self._scores(ctx)
        if len(scores) < MIN_CROSS_SECTION:
            return []

        ordered = sorted(scores.values())
        long_cut = _quantile(ordered, self._long_q)
        short_cut = _quantile(ordered, self._short_q) if self._short_q is not None else None

        directions = {
            symbol: _classify(score, long_cut, short_cut)
            for symbol, score in scores.items()
        }
        weights = _normalized_weights(scores, directions)
        return [
            Insight(
                symbol=symbol,
                direction=direction,
                period=self._period,
                generated_at=ctx.time,
                weight=weights.get(symbol),
                source=self.name,
                tag=f"score={scores[symbol]:.6g}",
            )
            for symbol, direction in sorted(directions.items())
        ]

    def _scores(self, ctx: PortfolioContext) -> dict[str, float]:
        """各标的当前的因子值（历史不足或求值为 NaN 的标的直接不参与截面）。"""
        scores: dict[str, float] = {}
        for symbol in ctx.bars:
            history = ctx.histories[symbol]
            if len(history) < self._min_history:
                continue
            values = evaluate_formula(history.tail(self._eval_window), self._tokens)
            score = float(values.iloc[-1]) if len(values) else math.nan
            if math.isfinite(score):
                scores[symbol] = score
        return scores


def _quantile(ordered: list[float], q: float) -> float:
    """升序序列的分位数（线性插值，避免为一个分位再拉一次 numpy）。"""
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _classify(
    score: float, long_cut: float, short_cut: float | None
) -> InsightDirection:
    if score >= long_cut:
        return InsightDirection.UP
    if short_cut is not None and score <= short_cut:
        return InsightDirection.DOWN
    return InsightDirection.FLAT


def _normalized_weights(
    scores: dict[str, float], directions: dict[str, InsightDirection]
) -> dict[str, float]:
    """被选中标的按 |分数| 归一化；分数全为 0 时退回等权。"""
    selected = [s for s, d in directions.items() if d is not InsightDirection.FLAT]
    if not selected:
        return {}
    total = sum(abs(scores[s]) for s in selected)
    if total <= 0:
        return dict.fromkeys(selected, 1.0 / len(selected))
    return {s: abs(scores[s]) / total for s in selected}


__all__ = ["FormulaFactorAlphaModel"]
