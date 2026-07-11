"""
统计显著性检验 —— Bootstrap 假设检验 (C5)

回答「策略的 edge (逐笔平均盈亏 > 0) 是真信号还是随机噪声」。

核心思想（算法定义参考 refs/jesse/jesse/research/rule_significance_testing/bootstrap.py，
仅借鉴思想，未复制代码；用 numpy 实现）：

  原假设 H0：策略无 edge，逐笔期望盈亏 = 0。
  1. 将逐笔盈亏零中心化（减去观测均值）→ 强制满足 H0。
  2. 有放回重采样 N 次，每次计算均值 → 得到 H0 下的抽样分布。
  3. p 值 = 模拟均值 ≥ 观测均值的比例。p 越小，越不可能是运气。

同时输出：
  - 均值的 95% Bootstrap 置信区间（非中心化重采样）。
  - t 统计量、效应量 (mean/std)。
  - **规则贡献度**：按开仓标签 (entry_tag) 分组，逐组做同样的 bootstrap 检验，
    报告每条规则的盈亏占比与显著性，定位「哪条规则真正在赚钱」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ── 常量 ──────────────────────────────────────────────────────────
ALPHA_5_PERCENT = 0.05
ALPHA_1_PERCENT = 0.01
MIN_TRADES = 5                 # 低于此笔数检验不可靠
MIN_GROUP_TRADES = 3           # 规则分组做检验的最小笔数
NULL_HIST_BINS = 41            # 零分布直方图桶数
_EPS = 1e-12


@dataclass(frozen=True)
class RuleContribution:
    """单条规则（entry_tag）的贡献度与显著性。"""
    entry_tag: str
    n_trades: int
    total_pnl: float
    pnl_share_pct: float       # 占策略总盈亏的百分比
    mean_pnl: float
    win_rate: float
    p_value: float
    is_significant_5pct: bool
    tested: bool               # 笔数不足时为 False（未做检验）


@dataclass(frozen=True)
class SignificanceResult:
    n_trades: int
    n_simulations: int
    observed_mean_pnl: float
    observed_total_pnl: float
    win_rate: float
    t_stat: float
    effect_size: float
    p_value: float
    is_significant_5pct: bool
    is_significant_1pct: bool
    ci95_mean_lower: float
    ci95_mean_upper: float
    null_hist: list[dict]       # [{center, count}]  H0 下均值分布
    observed_marker: float      # 观测均值（用于在直方图上标注）
    rule_contributions: list[RuleContribution]


# ── Bootstrap 基元 ────────────────────────────────────────────────

def _bootstrap_null_means(
    pnls: np.ndarray, observed_mean: float, n_sims: int, rng: np.random.Generator,
) -> np.ndarray:
    """H0（均值=0）下的抽样均值分布：零中心化后有放回重采样。"""
    centered = pnls - observed_mean
    n = len(centered)
    idx = rng.integers(0, n, size=(n_sims, n))
    return centered[idx].mean(axis=1)


def _bootstrap_mean_ci(
    pnls: np.ndarray, n_sims: int, rng: np.random.Generator,
) -> tuple[float, float]:
    """真实均值的 95% Bootstrap 置信区间（非中心化重采样）。"""
    n = len(pnls)
    idx = rng.integers(0, n, size=(n_sims, n))
    means = pnls[idx].mean(axis=1)
    return (
        float(np.percentile(means, 2.5)),
        float(np.percentile(means, 97.5)),
    )


def _p_value(null_means: np.ndarray, observed_mean: float) -> float:
    """单侧 p 值：H0 分布中 ≥ 观测均值的比例。"""
    if len(null_means) == 0:
        return 1.0
    return float(np.mean(null_means >= observed_mean))


def _t_stat(pnls: np.ndarray) -> float:
    n = len(pnls)
    std = float(pnls.std(ddof=1)) if n > 1 else 0.0
    if std <= _EPS:
        return 0.0
    return float(pnls.mean()) / (std / math.sqrt(n))


# ── 规则贡献度 ────────────────────────────────────────────────────

def _rule_contribution(
    tag: str, group_pnls: np.ndarray, total_pnl: float,
    n_sims: int, rng: np.random.Generator,
) -> RuleContribution:
    n = len(group_pnls)
    grp_total = float(group_pnls.sum())
    mean_pnl = float(group_pnls.mean()) if n else 0.0
    win_rate = float(np.mean(group_pnls > 0)) if n else 0.0
    share = (grp_total / total_pnl * 100.0) if abs(total_pnl) > _EPS else 0.0

    tested = n >= MIN_GROUP_TRADES
    if tested:
        null_means = _bootstrap_null_means(group_pnls, mean_pnl, n_sims, rng)
        p_value = _p_value(null_means, mean_pnl)
    else:
        p_value = 1.0

    return RuleContribution(
        entry_tag=tag,
        n_trades=n,
        total_pnl=round(grp_total, 4),
        pnl_share_pct=round(share, 4),
        mean_pnl=round(mean_pnl, 4),
        win_rate=round(win_rate, 4),
        p_value=round(p_value, 4),
        is_significant_5pct=tested and p_value < ALPHA_5_PERCENT,
        tested=tested,
    )


def _group_by_tag(pnls: np.ndarray, tags: list[str]) -> dict[str, np.ndarray]:
    groups: dict[str, list[float]] = {}
    for pnl, tag in zip(pnls.tolist(), tags):
        groups.setdefault(tag, []).append(pnl)
    return {tag: np.asarray(vals, dtype=float) for tag, vals in groups.items()}


# ── 主流程 ────────────────────────────────────────────────────────

def analyze_significance(
    pnls: list[float] | np.ndarray,
    tags: list[str] | None = None,
    n_simulations: int = 2000,
    seed: int = 42,
) -> SignificanceResult:
    """对逐笔盈亏做 bootstrap 显著性检验 + 规则贡献度分解。"""
    pnl_arr = np.asarray(pnls, dtype=float)
    n = len(pnl_arr)
    if n < MIN_TRADES:
        raise ValueError(f"交易笔数不足：仅 {n} 笔，显著性检验至少需 {MIN_TRADES} 笔")
    if n_simulations < 1:
        raise ValueError("模拟次数须 ≥ 1")

    observed_mean = float(pnl_arr.mean())
    observed_total = float(pnl_arr.sum())
    win_rate = float(np.mean(pnl_arr > 0))

    rng = np.random.default_rng(seed)
    null_means = _bootstrap_null_means(pnl_arr, observed_mean, n_simulations, rng)
    p_value = _p_value(null_means, observed_mean)
    ci_lower, ci_upper = _bootstrap_mean_ci(pnl_arr, n_simulations, rng)

    std = float(pnl_arr.std(ddof=1)) if n > 1 else 0.0
    effect_size = observed_mean / std if std > _EPS else 0.0

    contributions = _build_contributions(pnl_arr, tags, observed_total, n_simulations, rng)

    return SignificanceResult(
        n_trades=n,
        n_simulations=n_simulations,
        observed_mean_pnl=round(observed_mean, 4),
        observed_total_pnl=round(observed_total, 4),
        win_rate=round(win_rate, 4),
        t_stat=round(_t_stat(pnl_arr), 4),
        effect_size=round(effect_size, 4),
        p_value=round(p_value, 4),
        is_significant_5pct=p_value < ALPHA_5_PERCENT,
        is_significant_1pct=p_value < ALPHA_1_PERCENT,
        ci95_mean_lower=round(ci_lower, 4),
        ci95_mean_upper=round(ci_upper, 4),
        null_hist=_build_null_hist(null_means),
        observed_marker=round(observed_mean, 4),
        rule_contributions=contributions,
    )


def _build_contributions(
    pnl_arr: np.ndarray, tags: list[str] | None, total_pnl: float,
    n_sims: int, rng: np.random.Generator,
) -> list[RuleContribution]:
    if not tags or len(tags) != len(pnl_arr):
        return []
    groups = _group_by_tag(pnl_arr, tags)
    # 单一分组无分解价值
    if len(groups) <= 1:
        return []
    contributions = [
        _rule_contribution(tag, vals, total_pnl, n_sims, rng)
        for tag, vals in groups.items()
    ]
    # 按盈亏贡献降序
    contributions.sort(key=lambda c: c.total_pnl, reverse=True)
    return contributions


def _build_null_hist(null_means: np.ndarray) -> list[dict]:
    if len(null_means) == 0:
        return []
    counts, edges = np.histogram(null_means, bins=NULL_HIST_BINS)
    centers = (edges[:-1] + edges[1:]) / 2.0
    return [
        {"center": round(float(c), 6), "count": int(cnt)}
        for c, cnt in zip(centers, counts)
    ]
