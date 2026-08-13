"""
完整验证：单步结果归一化与参数推导（V3 · H2）

把各引擎（hyperopt / walkforward / bias_detection / mc_robustness）返回的
异构 dataclass 归一化成扁平字典，供 `validation_grade` 的规则读取、也直接作为
API 响应体。全部是纯函数，无 IO，可单测。
"""

from __future__ import annotations

import statistics
from typing import Any

# ── 参数空间自动推导 ──────────────────────────────────────────────
# 一键验证不强制用户填参数空间：缺省时围绕当前参数做「邻域扰动」，
# 既能跑通寻优，也正好用于度量参数敏感性。
PERTURB_RATIOS: tuple[float, ...] = (0.7, 1.0, 1.3)
MAX_DERIVED_PARAMS = 3          # 最多对 3 个数值参数做扰动，避免组合爆炸
MIN_INT_PARAM_VALUE = 2         # 周期类整数参数的下限
DEFAULT_PROBE_SPACE: dict[str, list[int]] = {
    # params 为空时的兜底空间：覆盖绝大多数均线/周期类策略的常用参数名
    "fast_period": [5, 10, 20],
    "slow_period": [30, 50, 80],
}

# ── Walk-Forward 窗口自适应 ───────────────────────────────────────
WF_TRAIN_RATIO = 0.6
WF_TEST_RATIO = 0.2
WF_MIN_WINDOW_BARS = 5

_EPS = 1e-9


def derive_param_space(params: dict[str, Any]) -> dict[str, list[Any]]:
    """
    由当前策略参数推导邻域参数空间（用户未显式提供 param_space 时使用）。

    只对 int/float 参数做 ±30% 扰动；非数值参数（如 ma_type）保持固定不参与寻优。
    没有任何数值参数时退回 `DEFAULT_PROBE_SPACE`。
    """
    numeric = [(k, v) for k, v in params.items() if isinstance(v, int | float) and not isinstance(v, bool)]
    numeric = numeric[:MAX_DERIVED_PARAMS]
    if not numeric:
        return {k: list(v) for k, v in DEFAULT_PROBE_SPACE.items()}

    space: dict[str, list[Any]] = {}
    for key, value in numeric:
        candidates = [_perturb(value, ratio) for ratio in PERTURB_RATIOS]
        unique = sorted(set(candidates))
        if len(unique) > 1:
            space[key] = unique
    return space or {k: list(v) for k, v in DEFAULT_PROBE_SPACE.items()}


def _perturb(value: float, ratio: float) -> Any:
    if isinstance(value, int):
        return max(MIN_INT_PARAM_VALUE, int(round(value * ratio)))
    return round(value * ratio, 6)


def auto_window_sizes(n_bars: int, train_size: int | None, test_size: int | None) -> tuple[int, int]:
    """
    解析 Walk-Forward 窗口大小；显式值放不下时按 bar 数比例自动缩小。

    返回 (train_size, test_size)，两者均 ≥ WF_MIN_WINDOW_BARS。
    """
    train = train_size or int(n_bars * WF_TRAIN_RATIO)
    test = test_size or int(n_bars * WF_TEST_RATIO)
    if train + test > n_bars:
        train = int(n_bars * WF_TRAIN_RATIO)
        test = int(n_bars * WF_TEST_RATIO)
    return max(WF_MIN_WINDOW_BARS, train), max(WF_MIN_WINDOW_BARS, test)


def compute_score_dispersion(scores: list[float]) -> float | None:
    """
    参数敏感性度量：(最优分 − 中位分) / max(|最优分|, eps)。

    越大说明只有极少数参数点有效，换一组邻近参数结果会大幅劣化。
    样本少于 3 个时返回 None（信息不足，不做判断）。
    """
    valid = [float(s) for s in scores if s is not None and _is_finite(s)]
    if len(valid) < 3:
        return None
    best = max(valid)
    median = statistics.median(valid)
    return round((best - median) / max(abs(best), _EPS), 6)


def _is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


# ── 各步骤结果归一化 ──────────────────────────────────────────────

def normalize_backtest(report: dict, final_value: float) -> dict:
    """回测步：只保留指标与终值，净值曲线由 /backtests/run 或历史详情提供。"""
    metrics = report.get("metrics", {})
    return {
        "final_value": round(float(final_value), 2),
        "metrics": metrics,
        "total_trades": int(metrics.get("total_trades", 0)),
    }


def normalize_optimize(outcome: Any, top_n: int = 10) -> dict:
    """寻优步：最优参数 + 得分离散度（参数敏感性依据）+ 前 N 组试验。"""
    scores = [float(t.score) for t in outcome.trials]
    return {
        "algorithm": outcome.algorithm,
        "loss_function": outcome.loss_name,
        "best_params": outcome.best_params,
        "best_score": round(float(outcome.best_score), 4),
        "best_metrics": outcome.best_metrics,
        "total_space": outcome.total_space,
        "evaluated": outcome.evaluated,
        "score_dispersion": compute_score_dispersion(scores),
        "trials": [
            {
                "params": t.params,
                "score": round(float(t.score), 4),
                "sharpe_ratio": t.metrics.get("sharpe_ratio", 0.0),
                "total_return_pct": t.metrics.get("total_return_pct", 0.0),
                "max_drawdown_pct": t.metrics.get("max_drawdown_pct", 0.0),
                "total_trades": t.metrics.get("total_trades", 0),
            }
            for t in outcome.trials[:top_n]
        ],
    }


def normalize_walkforward(outcome: Any) -> dict:
    """Walk-Forward 步：样本内外均值 + 效率 + 逐窗口摘要。"""
    return {
        "mode": outcome.mode,
        "train_size": outcome.train_size,
        "test_size": outcome.test_size,
        "total_windows": outcome.total_windows,
        "avg_is_sharpe": outcome.avg_is_sharpe,
        "avg_oos_sharpe": outcome.avg_oos_sharpe,
        "avg_is_return_pct": outcome.avg_is_return_pct,
        "avg_oos_return_pct": outcome.avg_oos_return_pct,
        "oos_is_efficiency": outcome.oos_is_efficiency,
        "oos_consistency": outcome.oos_consistency,
        "oos_win_windows": outcome.oos_win_windows,
        "windows": [
            {
                "index": w.index,
                "test_start": w.test_start,
                "test_end": w.test_end,
                "best_params": w.best_params,
                "is_sharpe": w.is_metrics.get("sharpe_ratio", 0.0),
                "oos_sharpe": w.oos_metrics.get("sharpe_ratio", 0.0),
                "oos_return_pct": w.oos_metrics.get("total_return_pct", 0.0),
            }
            for w in outcome.windows
        ],
    }


def normalize_bias(outcome: Any) -> dict:
    """偏差检测步：两类偏差命中标记 + 明细。"""
    return {
        "has_lookahead_bias": outcome.has_lookahead_bias,
        "has_recursive_bias": outcome.has_recursive_bias,
        "total_signals": outcome.total_signals,
        "lookahead": {
            "checked_signals": outcome.lookahead.checked_signals,
            "changed_signals": outcome.lookahead.changed_signals,
            "detail": outcome.lookahead.detail,
        },
        "recursive": [
            {
                "startup_candle": r.startup_candle,
                "checked_signals": r.checked_signals,
                "changed_signals": r.changed_signals,
            }
            for r in outcome.recursive
        ],
        "notes": outcome.notes,
    }


def normalize_robustness(result: Any) -> dict:
    """稳健性步：提取总收益/回撤的分位数（p5 用于评级规则）。"""
    by_name = {m.name: m for m in result.metrics}
    ret = by_name.get("total_return_pct")
    dd = by_name.get("max_drawdown_pct")
    return {
        "method": result.method,
        "n_scenarios": result.n_scenarios,
        "n_trades": result.n_trades,
        "prob_profit": result.prob_profit,
        "prob_beat_original": result.prob_beat_original,
        "original_total_return_pct": ret.original if ret else None,
        "p5_total_return_pct": ret.p5 if ret else None,
        "p50_total_return_pct": ret.p50 if ret else None,
        "p95_total_return_pct": ret.p95 if ret else None,
        "p5_max_drawdown_pct": dd.p5 if dd else None,
        "envelope": result.envelope,
    }
