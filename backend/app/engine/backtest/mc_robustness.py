"""
蒙特卡洛稳健性 —— 逐笔重采样 (C4)

区别于既有的「价格路径 / 成交顺序打乱」蒙特卡洛 (backtests.py /montecarlo)：
本模块对回测产出的**逐笔盈亏序列**做统计重采样，回答「这条收益曲线有多少靠运气」。

两种重采样方法（算法定义参考 refs/jesse/jesse/research/monte_carlo/monte_carlo_trades.py，
仅借鉴思想，未复制代码；用 numpy 实现）：

  - bootstrap  有放回重采样：每次随机抽取 N 笔（可重复），交易「集合」本身改变，
               因此总收益与最大回撤都会波动 → 给出真正的收益/回撤置信区间。
  - shuffle    无放回打乱：保持交易集合不变、仅改变发生顺序，总收益不变、
               但回撤路径改变 → 检验「这段回撤是否只是排序运气」。

产出：每个指标 (收益/最大回撤/夏普/盈亏比) 的均值、标准差、分位数、90%/95% 置信区间、
p 值与显著性标记，外加净值包络带 (envelope) 与原始曲线，供前端绘图。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ── 常量 ──────────────────────────────────────────────────────────
ALPHA_5_PERCENT = 0.05
ALPHA_1_PERCENT = 0.01
MIN_TRADES = 5                 # 低于此笔数无法可靠重采样
MAX_ENVELOPE_STEPS = 150       # 包络带最多返回的步数（降采样）
_EPS = 1e-12

# 指标定义：name -> higher_is_better（用于 p 值方向）
_METRIC_DIRECTION = {
    "total_return_pct": True,
    # 回撤为负数，越接近 0（数值越大）越好 → higher_is_better=True
    "max_drawdown_pct": True,
    "sharpe_ratio": True,
    "profit_factor": True,
}


@dataclass(frozen=True)
class McMetricStat:
    """单个指标在所有模拟场景下的分布统计。"""
    name: str
    original: float
    mean: float
    std: float
    min: float
    max: float
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    ci90_lower: float
    ci90_upper: float
    ci95_lower: float
    ci95_upper: float
    p_value: float
    is_significant_5pct: bool
    is_significant_1pct: bool


@dataclass(frozen=True)
class McRobustnessResult:
    method: str
    n_scenarios: int
    n_trades: int
    prob_profit: float          # 收益为正的场景占比
    prob_beat_original: float   # 收益 ≥ 原始的场景占比
    metrics: list[McMetricStat]
    envelope: list[dict]        # [{step, p5, p25, p50, p75, p95}]
    original_curve: list[float]


# ── 单场景指标计算 ────────────────────────────────────────────────

def _reconstruct_equity(pnls: np.ndarray, initial_cash: float) -> np.ndarray:
    """由逐笔盈亏重建净值曲线（长度 = n_trades + 1，含起始点）。"""
    curve = np.empty(len(pnls) + 1, dtype=float)
    curve[0] = initial_cash
    curve[1:] = initial_cash + np.cumsum(pnls)
    return curve


def _total_return_pct(equity: np.ndarray, initial_cash: float) -> float:
    if initial_cash <= _EPS:
        return 0.0
    return (equity[-1] - initial_cash) / initial_cash * 100.0


def _max_drawdown_pct(equity: np.ndarray) -> float:
    """最大回撤（百分比，负数）。"""
    running_max = np.maximum.accumulate(equity)
    safe = np.where(running_max <= _EPS, _EPS, running_max)
    dd = (equity - running_max) / safe
    return float(dd.min()) * 100.0


def _trade_sharpe(equity: np.ndarray) -> float:
    """逐笔收益的夏普（非年化，∝ SQN）：mean/std * sqrt(N)。"""
    prev = equity[:-1]
    safe = np.where(np.abs(prev) <= _EPS, _EPS, prev)
    rets = np.diff(equity) / safe
    if len(rets) < 2:
        return 0.0
    std = float(rets.std(ddof=1))
    if std <= _EPS:
        return 0.0
    return float(rets.mean()) / std * math.sqrt(len(rets))


def _profit_factor(pnls: np.ndarray) -> float:
    wins = float(pnls[pnls > 0].sum())
    losses = float(np.abs(pnls[pnls < 0].sum()))
    if losses <= _EPS:
        return min(wins, 99.9) if wins > 0 else 0.0
    return min(wins / losses, 99.9)


def _scenario_metrics(pnls: np.ndarray, initial_cash: float) -> dict[str, float]:
    equity = _reconstruct_equity(pnls, initial_cash)
    return {
        "total_return_pct": _total_return_pct(equity, initial_cash),
        "max_drawdown_pct": _max_drawdown_pct(equity),
        "sharpe_ratio": _trade_sharpe(equity),
        "profit_factor": _profit_factor(pnls),
    }


# ── 主流程 ────────────────────────────────────────────────────────

def run_mc_robustness(
    pnls: list[float] | np.ndarray,
    initial_cash: float,
    n_scenarios: int = 1000,
    method: str = "bootstrap",
    seed: int = 42,
) -> McRobustnessResult:
    """对逐笔盈亏序列做重采样稳健性分析。

    method: 'bootstrap' 有放回重采样 / 'shuffle' 无放回打乱。
    """
    if method not in ("bootstrap", "shuffle"):
        raise ValueError(f"未知方法 '{method}'，可用: bootstrap / shuffle")
    pnl_arr = np.asarray(pnls, dtype=float)
    n = len(pnl_arr)
    if n < MIN_TRADES:
        raise ValueError(f"交易笔数不足：仅 {n} 笔，逐笔重采样至少需 {MIN_TRADES} 笔")
    if n_scenarios < 1:
        raise ValueError("模拟场景数须 ≥ 1")

    original = _scenario_metrics(pnl_arr, initial_cash)

    rng = np.random.default_rng(seed)
    samples = _sample_scenarios(pnl_arr, n_scenarios, method, rng)

    # 逐场景指标 + 净值矩阵（所有场景等长 = n+1）
    equity_matrix = np.empty((n_scenarios, n + 1), dtype=float)
    per_metric: dict[str, list[float]] = {k: [] for k in _METRIC_DIRECTION}
    returns_arr = np.empty(n_scenarios, dtype=float)

    for i in range(n_scenarios):
        row = samples[i]
        equity = _reconstruct_equity(row, initial_cash)
        equity_matrix[i] = equity
        m = _scenario_metrics(row, initial_cash)
        for key, values in per_metric.items():
            values.append(m[key])
        returns_arr[i] = m["total_return_pct"]

    metrics = [
        _summarize_metric(name, np.asarray(vals), original[name])
        for name, vals in per_metric.items()
    ]
    envelope = _build_envelope(equity_matrix)
    prob_profit = float(np.mean(returns_arr > 0))
    prob_beat = float(np.mean(returns_arr >= original["total_return_pct"]))

    return McRobustnessResult(
        method=method,
        n_scenarios=n_scenarios,
        n_trades=n,
        prob_profit=round(prob_profit, 4),
        prob_beat_original=round(prob_beat, 4),
        metrics=metrics,
        envelope=envelope,
        original_curve=_downsample(_reconstruct_equity(pnl_arr, initial_cash)),
    )


def _sample_scenarios(
    pnls: np.ndarray, n_scenarios: int, method: str, rng: np.random.Generator,
) -> np.ndarray:
    """返回形状 (n_scenarios, n_trades) 的重采样矩阵。"""
    n = len(pnls)
    if method == "bootstrap":
        idx = rng.integers(0, n, size=(n_scenarios, n))
        return pnls[idx]
    # shuffle：逐行独立打乱
    out = np.tile(pnls, (n_scenarios, 1))
    for i in range(n_scenarios):
        rng.shuffle(out[i])
    return out


def _summarize_metric(name: str, values: np.ndarray, original: float) -> McMetricStat:
    higher_is_better = _METRIC_DIRECTION[name]
    if higher_is_better:
        p_value = float(np.mean(values >= original))
    else:
        p_value = float(np.mean(values <= original))
    return McMetricStat(
        name=name,
        original=round(float(original), 4),
        mean=round(float(values.mean()), 4),
        std=round(float(values.std(ddof=1)) if len(values) > 1 else 0.0, 4),
        min=round(float(values.min()), 4),
        max=round(float(values.max()), 4),
        p5=round(float(np.percentile(values, 5)), 4),
        p25=round(float(np.percentile(values, 25)), 4),
        p50=round(float(np.percentile(values, 50)), 4),
        p75=round(float(np.percentile(values, 75)), 4),
        p95=round(float(np.percentile(values, 95)), 4),
        ci90_lower=round(float(np.percentile(values, 5)), 4),
        ci90_upper=round(float(np.percentile(values, 95)), 4),
        ci95_lower=round(float(np.percentile(values, 2.5)), 4),
        ci95_upper=round(float(np.percentile(values, 97.5)), 4),
        p_value=round(p_value, 4),
        is_significant_5pct=p_value < ALPHA_5_PERCENT,
        is_significant_1pct=p_value < ALPHA_1_PERCENT,
    )


def _build_envelope(equity_matrix: np.ndarray) -> list[dict]:
    """按交易步计算净值分位带（p5/25/50/75/95），降采样到 MAX_ENVELOPE_STEPS。"""
    n_steps = equity_matrix.shape[1]
    step_indices = _downsample_indices(n_steps)
    envelope: list[dict] = []
    for step in step_indices:
        col = equity_matrix[:, step]
        envelope.append({
            "step": int(step),
            "p5": round(float(np.percentile(col, 5)), 2),
            "p25": round(float(np.percentile(col, 25)), 2),
            "p50": round(float(np.percentile(col, 50)), 2),
            "p75": round(float(np.percentile(col, 75)), 2),
            "p95": round(float(np.percentile(col, 95)), 2),
        })
    return envelope


def _downsample_indices(n: int) -> list[int]:
    if n <= MAX_ENVELOPE_STEPS:
        return list(range(n))
    return sorted(set(np.linspace(0, n - 1, MAX_ENVELOPE_STEPS).astype(int).tolist()))


def _downsample(curve: np.ndarray) -> list[float]:
    return [round(float(curve[i]), 2) for i in _downsample_indices(len(curve))]
