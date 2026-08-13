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

import pandas as pd

from app.engine.framework.alpha import AlphaModel
from app.engine.framework.insight import Insight, InsightDirection
from app.quant.cross_section import to_wide
from app.quant.formula_factor import (
    evaluate_formula,
    evaluate_formula_panel,
    formula_requires_panel,
)

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
        # 含 CS_* 的公式必须整块 universe 一起算，走 panel 路径（M2）
        self._needs_panel = formula_requires_panel(self._tokens)
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
        if self._needs_panel:
            return self._panel_scores(ctx)

        scores: dict[str, float] = {}
        for symbol in ctx.bars:
            history = ctx.histories[symbol]
            if len(history) < self._min_history:
                continue
            score = self._score_one(history.tail(self._eval_window))
            if math.isfinite(score):
                scores[symbol] = score
        return scores

    def _score_one(self, history: pd.DataFrame) -> float:
        """单标的的因子值。子类可覆写以换掉打分方式，分位→观点的逻辑完全复用。"""
        values = evaluate_formula(history, self._tokens)
        return float(values.iloc[-1]) if len(values) else math.nan

    # ── 面板（截面）打分路径 ─────────────────────────────────

    def _panel_scores(self, ctx: PortfolioContext) -> dict[str, float]:
        """把整个 universe 拼成面板一次算完，取最后一个时点的截面因子值。"""
        panel = self._build_panel(ctx)
        if panel is None:
            return {}
        try:
            values = self._score_panel(panel)
        except Exception:
            logger.exception("面板因子求值失败（本轮不出观点）")
            return {}
        if values.empty:
            return {}

        latest = to_wide(values).iloc[-1]
        return {
            str(symbol): float(score)
            for symbol, score in latest.items()
            if math.isfinite(float(score))
        }

    def _build_panel(self, ctx: PortfolioContext) -> pd.DataFrame | None:
        """由 ctx.histories 构建 (datetime, instrument) 面板；标的不足 2 个则放弃。"""
        frames: list[pd.DataFrame] = []
        for symbol in sorted(ctx.bars):
            history = ctx.histories[symbol]
            if len(history) < self._min_history:
                continue
            frame = history.tail(self._eval_window).copy()
            frame.index = pd.Index(frame.index, name="datetime")
            frame["instrument"] = symbol
            frames.append(frame.set_index("instrument", append=True))

        if len(frames) < MIN_CROSS_SECTION:
            return None
        return pd.concat(frames).sort_index()

    def _score_panel(self, panel: pd.DataFrame) -> pd.Series:
        """面板打分。子类可覆写以换掉打分方式（如因子库的面板型条目）。"""
        return evaluate_formula_panel(panel, self._tokens)


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


class LibraryFactorAlphaModel(FormulaFactorAlphaModel):
    """
    用**声明式因子库**的条目打分，其余（分位 → 观点 → 权重）与公式因子完全一致。

    这条路径的存在是因为因子库与 RPN 公式引擎是两套不同的表达式语言：
    因子库条目的 `expr`（如 `($close-$open)/$open`）是 Qlib 风格的**展示用标注**，
    RPN 引擎的词表则是 `MOM20` / `ATR_RATIO` 这类 token，两者无法互相解析。

    但**根本不需要解析** —— `FactorSpec` 携带的 `compute` 本身就是可调用对象。
    直接调它，比先把 expr 翻译成 RPN 再求值既准确又省事。

    用法::

        specs = generate_factor_library()
        LibraryFactorAlphaModel(next(s for s in specs if s.name == "KMID"))
    """

    def __init__(self, spec, **kwargs) -> None:
        if getattr(spec, "is_panel", False):
            raise ValueError(
                f"因子 {spec.name} 是面板型（截面），请改用 PanelLibraryFactorAlphaModel"
            )
        # 因子库条目自带滚动窗口；历史不足窗口长度时算不出有意义的值。
        # 调用方没显式给 min_history 时按窗口推一个下界，避免早期用一堆 NaN 建仓。
        kwargs.setdefault("min_history", max(getattr(spec, "window", 0) * 2, 60))
        kwargs.setdefault("name", f"library_factor:{spec.name}")
        # 父类要求 tokens 非空；这里走的是 compute 分支，塞一个占位值即可
        super().__init__(["ZERO"], **kwargs)
        self._spec = spec

    def _score_one(self, history: pd.DataFrame) -> float:
        values = self._spec.compute(history)
        return float(values.iloc[-1]) if len(values) else math.nan


class PanelLibraryFactorAlphaModel(FormulaFactorAlphaModel):
    """
    用**面板型**因子库条目（Alpha101）打分。

    与 `LibraryFactorAlphaModel` 的唯一差别是求值粒度：截面型 alpha 必须拿到整个
    universe 才有意义，所以覆写的是 `_score_panel` 而非 `_score_one`。
    分位 → 观点 → 权重的逻辑与另外两条路径完全一致。
    """

    def __init__(self, spec, **kwargs) -> None:
        if not getattr(spec, "is_panel", False):
            raise ValueError(
                f"因子 {spec.name} 是单标的型，请改用 LibraryFactorAlphaModel"
            )
        kwargs.setdefault("min_history", max(getattr(spec, "window", 0) * 2, 60))
        kwargs.setdefault("name", f"panel_factor:{spec.name}")
        super().__init__(["ZERO"], **kwargs)
        # 父类按 tokens 判定路径；这里是面板型条目，直接置位
        self._needs_panel = True
        self._spec = spec

    def _score_panel(self, panel: pd.DataFrame) -> pd.Series:
        return self._spec.compute_panel(panel)


__all__ = [
    "FormulaFactorAlphaModel",
    "LibraryFactorAlphaModel",
    "PanelLibraryFactorAlphaModel",
]
