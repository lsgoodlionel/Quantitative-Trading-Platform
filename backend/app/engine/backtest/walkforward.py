"""
Walk-Forward 分析（C2）

将历史数据切分为滚动的「训练窗口 + 测试窗口」，在训练窗口上寻优参数，
再用最优参数在紧邻的测试窗口（样本外）评估，衡量策略的抗曲线拟合能力。

设计参考（仅算法定义，非复制代码）:
  refs/freqtrade/freqtrade/freqai/freqai_interface.py   train/backtest 滚动窗口切分思想

两种模式:
  - rolling  （默认）固定长度训练窗口随测试窗口一起向前滑动
  - anchored 训练窗口起点锚定，长度随时间扩张（expanding）

核心产出：每个窗口的样本内(IS) vs 样本外(OOS)指标 + 汇总的 OOS 衰减/一致性统计。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from app.data.models import Bar

logger = logging.getLogger(__name__)

# 每个窗口至少需要的 bar 数（低于则跳过该窗口）
_MIN_WINDOW_BARS = 5

# params, bars -> metrics dict
BacktestSliceFn = Callable[[dict, list[Bar]], dict]
# bars -> best_params dict（在训练窗口上寻优）
OptimizeSliceFn = Callable[[list[Bar]], dict]


@dataclass
class WalkForwardWindow:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    best_params: dict
    is_metrics: dict   # 样本内（训练窗口）指标
    oos_metrics: dict  # 样本外（测试窗口）指标


@dataclass
class WalkForwardOutcome:
    mode: str
    train_size: int
    test_size: int
    windows: list[WalkForwardWindow] = field(default_factory=list)
    # 汇总
    avg_is_sharpe: float = 0.0
    avg_oos_sharpe: float = 0.0
    avg_is_return_pct: float = 0.0
    avg_oos_return_pct: float = 0.0
    oos_is_efficiency: float = 0.0   # OOS/IS 夏普比（<1 表示样本外衰减）
    oos_consistency: float = 0.0     # 样本外正收益窗口占比
    oos_win_windows: int = 0
    total_windows: int = 0


def _slice_windows(
    n: int, train_size: int, test_size: int, mode: str,
) -> list[tuple[int, int, int, int]]:
    """返回 [(train_lo, train_hi, test_lo, test_hi), ...]（半开区间索引）。"""
    windows: list[tuple[int, int, int, int]] = []
    test_lo = train_size
    while test_lo + test_size <= n:
        test_hi = test_lo + test_size
        train_lo = 0 if mode == "anchored" else test_lo - train_size
        windows.append((train_lo, test_lo, test_lo, test_hi))
        test_lo += test_size
    return windows


def run_walk_forward(
    bars: Sequence[Bar],
    optimize_fn: OptimizeSliceFn,
    backtest_fn: BacktestSliceFn,
    train_size: int,
    test_size: int,
    mode: str = "rolling",
) -> WalkForwardOutcome:
    """执行 walk-forward 分析。

    optimize_fn: 训练窗口 bars -> best_params
    backtest_fn: (params, bars) -> metrics dict
    """
    if mode not in ("rolling", "anchored"):
        raise ValueError(f"未知模式 '{mode}'，可用: rolling / anchored")
    bars = list(bars)
    n = len(bars)
    if train_size < _MIN_WINDOW_BARS or test_size < _MIN_WINDOW_BARS:
        raise ValueError(f"训练/测试窗口至少需 {_MIN_WINDOW_BARS} 根 bar")
    if n < train_size + test_size:
        raise ValueError(
            f"数据不足：共 {n} 根 bar，至少需要 训练{train_size}+测试{test_size}={train_size + test_size} 根"
        )

    idx_windows = _slice_windows(n, train_size, test_size, mode)
    if not idx_windows:
        raise ValueError("无法切分出任何 walk-forward 窗口，请调小窗口大小")

    windows: list[WalkForwardWindow] = []
    for i, (tr_lo, tr_hi, te_lo, te_hi) in enumerate(idx_windows):
        train_bars = bars[tr_lo:tr_hi]
        test_bars = bars[te_lo:te_hi]
        try:
            best_params = optimize_fn(train_bars)
            is_metrics = backtest_fn(best_params, train_bars)
            oos_metrics = backtest_fn(best_params, test_bars)
        except Exception:
            logger.warning("窗口 %d 评估失败，跳过", i, exc_info=True)
            continue
        windows.append(
            WalkForwardWindow(
                index=i,
                train_start=_bar_time(train_bars[0]),
                train_end=_bar_time(train_bars[-1]),
                test_start=_bar_time(test_bars[0]),
                test_end=_bar_time(test_bars[-1]),
                train_bars=len(train_bars),
                test_bars=len(test_bars),
                best_params=best_params,
                is_metrics=is_metrics,
                oos_metrics=oos_metrics,
            )
        )

    if not windows:
        raise ValueError("所有 walk-forward 窗口均评估失败")

    return _aggregate(windows, mode, train_size, test_size)


def _aggregate(
    windows: list[WalkForwardWindow], mode: str, train_size: int, test_size: int,
) -> WalkForwardOutcome:
    is_sharpes = [_g(w.is_metrics, "sharpe_ratio") for w in windows]
    oos_sharpes = [_g(w.oos_metrics, "sharpe_ratio") for w in windows]
    is_rets = [_g(w.is_metrics, "total_return_pct") for w in windows]
    oos_rets = [_g(w.oos_metrics, "total_return_pct") for w in windows]

    avg_is_sharpe = float(np.mean(is_sharpes))
    avg_oos_sharpe = float(np.mean(oos_sharpes))
    win_windows = sum(1 for r in oos_rets if r > 0)
    efficiency = avg_oos_sharpe / avg_is_sharpe if abs(avg_is_sharpe) > 1e-9 else 0.0

    return WalkForwardOutcome(
        mode=mode,
        train_size=train_size,
        test_size=test_size,
        windows=windows,
        avg_is_sharpe=round(avg_is_sharpe, 4),
        avg_oos_sharpe=round(avg_oos_sharpe, 4),
        avg_is_return_pct=round(float(np.mean(is_rets)), 4),
        avg_oos_return_pct=round(float(np.mean(oos_rets)), 4),
        oos_is_efficiency=round(efficiency, 4),
        oos_consistency=round(win_windows / len(windows), 4),
        oos_win_windows=win_windows,
        total_windows=len(windows),
    )


def _g(metrics: dict, key: str) -> float:
    val = metrics.get(key, 0.0)
    try:
        f = float(val)
    except (TypeError, ValueError):
        return 0.0
    return f if np.isfinite(f) else 0.0


def _bar_time(bar: Bar) -> str:
    t = bar.time
    return t.isoformat() if hasattr(t, "isoformat") else str(t)
