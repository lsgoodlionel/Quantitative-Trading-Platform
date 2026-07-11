"""
因子库横截面 IC 排行（B2 分析侧）

复用 factor_analysis 的 IC 概念（因子值与前瞻收益的相关性），但从「单标的滚动 IC」
升级为因子库场景下更合适的「**横截面 IC**」：在每个交易日跨 universe 内标的计算
因子值与前瞻收益的相关系数，得到 IC 时序，再汇总为 IC 均值 / 标准差 / ICIR。
这与 qlib SigAnaRecord 的 IC/RankIC 口径一致，是因子动物园的标准评价方式。

排序键：
  method="rank_ic" → 按 |RankIC 均值| 降序（更稳健，抗离群）
  method="ic"      → 按 |IC 均值| 降序
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.factor_lib.loader import FactorSpec
from app.quant.factor_lib.operators import EPS

# 单个交易日参与横截面相关性所需的最少标的数（低于则该日 IC 记为缺失）
DEFAULT_MIN_NAMES: int = 3


@dataclass(frozen=True)
class FactorICStat:
    name: str
    label: str
    group: str
    window: int
    expr: str
    ic_mean: float
    ic_std: float
    icir: float
    rank_ic_mean: float
    positive_rate: float
    coverage: float
    n_dates: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "group": self.group,
            "window": self.window,
            "expr": self.expr,
            "ic_mean": _round(self.ic_mean),
            "ic_std": _round(self.ic_std),
            "icir": _round(self.icir),
            "rank_ic_mean": _round(self.rank_ic_mean),
            "positive_rate": _round(self.positive_rate),
            "coverage": _round(self.coverage),
            "n_dates": self.n_dates,
        }


def _round(v: float) -> float | None:
    if v is None or np.isnan(v) or np.isinf(v):
        return None
    return round(float(v), 4)


def _cross_sectional_ic(
    factor: pd.Series,
    label: pd.Series,
    min_names: int,
) -> tuple[np.ndarray, np.ndarray]:
    """逐日计算横截面 (Pearson IC, Spearman RankIC)，返回两条 IC 时序数组。"""
    joined = pd.DataFrame({"f": factor, "r": label}).dropna()
    if joined.empty:
        return np.array([]), np.array([])

    ic_vals: list[float] = []
    rank_ic_vals: list[float] = []
    for _, grp in joined.groupby(level="datetime"):
        if len(grp) < min_names:
            continue
        f = grp["f"].to_numpy(dtype=float)
        r = grp["r"].to_numpy(dtype=float)
        if np.std(f) < EPS or np.std(r) < EPS:
            continue
        ic_vals.append(float(np.corrcoef(f, r)[0, 1]))
        fr = pd.Series(f).rank().to_numpy(dtype=float)
        rr = pd.Series(r).rank().to_numpy(dtype=float)
        if np.std(fr) >= EPS and np.std(rr) >= EPS:
            rank_ic_vals.append(float(np.corrcoef(fr, rr)[0, 1]))

    ic_arr = np.array([v for v in ic_vals if not np.isnan(v)])
    rank_arr = np.array([v for v in rank_ic_vals if not np.isnan(v)])
    return ic_arr, rank_arr


def _stat_for_spec(
    spec: FactorSpec,
    labeled_panel: pd.DataFrame,
    label_field: str,
    total_rows: int,
    min_names: int,
) -> FactorICStat:
    factor = labeled_panel[spec.name]
    label = labeled_panel[label_field]
    ic_arr, rank_arr = _cross_sectional_ic(factor, label, min_names)

    coverage = float(factor.notna().sum()) / total_rows if total_rows else 0.0
    if ic_arr.size == 0:
        return FactorICStat(
            name=spec.name, label=spec.label, group=spec.group,
            window=spec.window, expr=spec.expr,
            ic_mean=float("nan"), ic_std=float("nan"), icir=float("nan"),
            rank_ic_mean=float("nan"), positive_rate=float("nan"),
            coverage=coverage, n_dates=0,
        )

    ic_mean = float(np.mean(ic_arr))
    ic_std = float(np.std(ic_arr))
    icir = ic_mean / ic_std if ic_std > EPS else float("nan")
    rank_ic_mean = float(np.mean(rank_arr)) if rank_arr.size else float("nan")
    positive_rate = float(np.mean(ic_arr > 0))
    return FactorICStat(
        name=spec.name, label=spec.label, group=spec.group,
        window=spec.window, expr=spec.expr,
        ic_mean=ic_mean, ic_std=ic_std, icir=icir,
        rank_ic_mean=rank_ic_mean, positive_rate=positive_rate,
        coverage=coverage, n_dates=int(ic_arr.size),
    )


def rank_factor_library(
    labeled_panel: pd.DataFrame,
    specs: list[FactorSpec],
    label_field: str,
    method: str = "rank_ic",
    top_k: int | None = None,
    min_names: int = DEFAULT_MIN_NAMES,
) -> list[FactorICStat]:
    """对因子库逐因子计算横截面 IC 统计并排序。

    Parameters
    ----------
    labeled_panel : 含各因子列 + label_field 的 (datetime, instrument) 面板
    specs         : 因子定义列表（其 name 需为面板列）
    method        : "rank_ic"（默认，按 |RankIC| 排序）或 "ic"
    top_k         : 仅返回前 K 个（None = 全部）
    min_names     : 单日横截面最少标的数
    """
    if method not in ("rank_ic", "ic"):
        raise ValueError(f"method 非法: {method}（应为 rank_ic / ic）")

    total_rows = int(len(labeled_panel))
    stats = [
        _stat_for_spec(spec, labeled_panel, label_field, total_rows, min_names)
        for spec in specs
        if spec.name in labeled_panel.columns
    ]

    def _sort_key(s: FactorICStat) -> float:
        primary = s.rank_ic_mean if method == "rank_ic" else s.ic_mean
        return abs(primary) if primary is not None and not np.isnan(primary) else -1.0

    ranked = sorted(stats, key=_sort_key, reverse=True)
    return ranked[:top_k] if top_k else ranked
