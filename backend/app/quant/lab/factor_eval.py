"""
单条表达式在**指定日期区间**上的打分（V3 · I2）

样本内与样本外必须用同一把尺子量，否则「衰减了多少」这个数字没有意义。
所以两边都走这一个函数：给一份 `SamplePanels`，返回一组标量指标。

关键动作是 `_restrict_to_labelled_dates`：因子面板按 OHLCV 的**全部**日期算出来
（滚动算子需要热身），但打分前会被裁到该段前瞻收益面板的日期集合上。
不裁的话，`compute_factor_fitness` 会把因子面板的索引当基准 reindex 收益面板，
热身段变成一堆 NaN 收益 —— 仓位与换手照记，收益记 0，样本外的适应度被凭空稀释。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.formula_factor import FormulaError
from app.quant.lab.sample_split import SamplePanels
from app.quant.mining.panel_eval import build_factor_panel, cross_sectional_ic_stats


@dataclass(frozen=True)
class FactorScore:
    """一条公式在某段样本上的评价指标。NaN 一律归一成 None，方便直接 JSON 化。"""

    fitness: float
    ic_mean: float | None
    rank_ic_mean: float | None
    icir: float | None
    mean_net_return: float
    turnover: float
    n_dates: int

    def to_dict(self, prefix: str = "") -> dict:
        return {
            f"{prefix}fitness": _round(self.fitness),
            f"{prefix}ic_mean": _round(self.ic_mean),
            f"{prefix}rank_ic_mean": _round(self.rank_ic_mean),
            f"{prefix}icir": _round(self.icir),
            f"{prefix}mean_net_return": _round(self.mean_net_return),
            f"{prefix}turnover": _round(self.turnover),
            f"{prefix}n_dates": self.n_dates,
        }


def score_tokens(
    tokens: tuple[str, ...] | list[str],
    panels: SamplePanels,
    fitness_config,
) -> FactorScore | None:
    """
    在 `panels` 这一段上给一条 RPN 公式打分。

    公式非法、求值失败或该段没有可评估的交叉点时返回 `None` ——
    调用方据此把这条候选标成「该段无法评估」，而不是伪造一个 0。
    """
    from app.quant.factor_fitness import compute_factor_fitness

    token_list = list(tokens)
    if not token_list or panels.is_empty:
        return None

    try:
        factor_panel = build_factor_panel(panels.ohlcv_by_symbol, token_list)
    except FormulaError:
        return None

    fwd = panels.forward_return_panel
    restricted = _restrict_to_labelled_dates(factor_panel, fwd)
    if restricted.empty:
        return None

    try:
        result = compute_factor_fitness(
            factor_panel=restricted,
            forward_return_panel=fwd,
            liquidity_panel=panels.liquidity_panel,
            config=fitness_config,
        )
    except Exception:  # noqa: BLE001 — 退化因子不该让整轮循环崩掉
        return None

    ic_mean, rank_ic_mean, icir = cross_sectional_ic_stats(
        restricted["factor"], fwd.iloc[:, 0]
    )
    return FactorScore(
        fitness=float(result.fitness),
        ic_mean=_clean(ic_mean),
        rank_ic_mean=_clean(rank_ic_mean),
        icir=_clean(icir),
        mean_net_return=float(result.mean_net_return),
        turnover=float(result.turnover),
        n_dates=int(restricted.index.get_level_values("datetime").nunique()),
    )


def _restrict_to_labelled_dates(
    factor_panel: pd.DataFrame, forward_return_panel: pd.DataFrame
) -> pd.DataFrame:
    """把因子面板裁到「该段确实有标签」的日期上（见模块 docstring）。"""
    labelled = forward_return_panel.index.get_level_values("datetime").unique()
    mask = factor_panel.index.get_level_values("datetime").isin(labelled)
    return factor_panel.loc[mask]


def _clean(value: float) -> float | None:
    f = float(value)
    return None if np.isnan(f) or np.isinf(f) else f


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if np.isnan(f) or np.isinf(f) else round(f, 6)
