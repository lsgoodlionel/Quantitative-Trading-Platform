"""
成本感知因子适应度（Cost-Aware Factor Fitness, B4）

将 AlphaGPT `model_core/backtest.py` 的 `MemeBacktest.evaluate` 计算「形状」移植到
numpy/pandas，去除 torch 依赖，作用于平台的股票/加密 universe，返回**一个标量**
适应度分数外加可解释性拆解。

与 AlphaGPT 的差异：
  - 无 torch、无随机性；相同输入 → 相同标量（确定性）。
  - 用中位数（median）跨 universe 聚合，避免少数幸运标的主导评分。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import expit

from app.quant.processors import EPS

# 流动性安全阀：当提供 liquidity 面板时，流动性低于该分位的单元格禁止开仓
LIQUIDITY_FLOOR_QUANTILE: float = 0.10


@dataclass(frozen=True)
class FitnessConfig:
    fee_rate: float = 0.0010          # 单边费率（默认 10bps；AlphaGPT meme 用 60bps）
    max_impact: float = 0.02          # 每笔市场冲击滑点上限
    trade_notional: float = 10_000.0  # 冲击模型假设订单规模
    entry_threshold: float = 0.85     # sigmoid(signal) 开仓门槛
    drawdown_bar: float = 0.05        # 单 bar 亏损超过该值计一次「大回撤」
    drawdown_penalty: float = 2.0     # 每次大回撤的评分惩罚权重
    min_activity: int = 5             # 最少活跃持仓数，否则适应度触底
    inactivity_floor: float = -10.0   # 活跃度门槛不通过时返回的适应度


@dataclass(frozen=True)
class FitnessResult:
    fitness: float
    mean_net_return: float
    gross_return: float
    total_cost: float
    turnover: float
    avg_activity: float
    n_big_drawdowns: int
    activity_gate_passed: bool
    per_instrument_score: dict[str, float]


def _to_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    """把 (datetime, instrument) 单列面板透视为 time × instrument 矩阵。"""
    col = panel.columns[0]
    mat = panel[col].unstack(level="instrument")
    return mat.sort_index()


def _floored_fitness(instruments: list[str], config: FitnessConfig) -> FitnessResult:
    return FitnessResult(
        fitness=config.inactivity_floor,
        mean_net_return=0.0,
        gross_return=0.0,
        total_cost=0.0,
        turnover=0.0,
        avg_activity=0.0,
        n_big_drawdowns=0,
        activity_gate_passed=False,
        per_instrument_score={s: config.inactivity_floor for s in instruments},
    )


def compute_factor_fitness(
    factor_panel: pd.DataFrame,
    forward_return_panel: pd.DataFrame,
    liquidity_panel: pd.DataFrame | None = None,
    config: FitnessConfig = FitnessConfig(),
) -> FitnessResult:
    """计算成本感知因子适应度（见契约 §4.6）。"""
    F = _to_matrix(factor_panel)
    R = _to_matrix(forward_return_panel).reindex(index=F.index, columns=F.columns)
    instruments = [str(c) for c in F.columns]

    if F.empty or not np.isfinite(F.to_numpy(dtype=float)).any():
        return _floored_fitness(instruments, config)

    # 1. 信号 → 仓位
    signal = pd.DataFrame(expit(F.to_numpy(dtype=float)), index=F.index, columns=F.columns)
    position = (signal > config.entry_threshold).astype(float)

    # 流动性/安全阀（提供 L 时生效；股票默认不设阀）
    if liquidity_panel is not None:
        L = _to_matrix(liquidity_panel).reindex(index=F.index, columns=F.columns)
        l_arr = L.to_numpy(dtype=float)
        finite = l_arr[np.isfinite(l_arr)]
        if finite.size:
            floor = float(np.quantile(finite, LIQUIDITY_FLOOR_QUANTILE))
            position = position.where(L.fillna(0.0) >= floor, 0.0)
        # 2. 滑点冲击
        impact = np.clip(
            config.trade_notional / (L.fillna(0.0).to_numpy(dtype=float) + EPS),
            0.0,
            config.max_impact,
        )
        cost_rate = config.fee_rate + pd.DataFrame(impact, index=F.index, columns=F.columns)
    else:
        cost_rate = pd.DataFrame(config.fee_rate, index=F.index, columns=F.columns)

    # 3. 换手（首 bar 前仓位为 0）
    prev = position.shift(1).fillna(0.0)
    turnover_mat = (position - prev).abs()

    # 4. 盈亏（R 的 NaN 视为该单元格 0 贡献）
    gross = position * R.fillna(0.0)
    net = gross - turnover_mat * cost_rate

    # 5. 逐标的评分
    cum = net.sum(axis=0)
    gross_cum = gross.sum(axis=0)
    big_dd = (net < -config.drawdown_bar).sum(axis=0)
    score = cum - big_dd * config.drawdown_penalty

    # 6. 活跃度门槛
    activity = position.sum(axis=0)
    inactive_mask = activity < config.min_activity
    score = score.where(~inactive_mask, config.inactivity_floor)

    n_active_instruments = int((~inactive_mask).sum())
    gate_passed = n_active_instruments > 0
    if not gate_passed:
        return _floored_fitness(instruments, config)

    # 7. 稳健聚合
    fitness = float(np.median(score.to_numpy(dtype=float)))
    mean_net_return = float(np.mean(cum.to_numpy(dtype=float)))
    gross_return = float(np.mean(gross_cum.to_numpy(dtype=float)))
    total_cost = float((turnover_mat * cost_rate).to_numpy(dtype=float).sum())
    turnover = float(turnover_mat.to_numpy(dtype=float).sum())
    avg_activity = float(np.mean(activity.to_numpy(dtype=float)))
    n_big_drawdowns = int(big_dd.sum())

    per_instrument_score = {
        str(inst): _safe(score[inst]) for inst in F.columns
    }

    return FitnessResult(
        fitness=_safe(fitness),
        mean_net_return=_safe(mean_net_return),
        gross_return=_safe(gross_return),
        total_cost=_safe(total_cost),
        turnover=_safe(turnover),
        avg_activity=_safe(avg_activity),
        n_big_drawdowns=n_big_drawdowns,
        activity_gate_passed=gate_passed,
        per_instrument_score=per_instrument_score,
    )


def _safe(v: float) -> float:
    f = float(v)
    if np.isnan(f) or np.isinf(f):
        return 0.0
    return round(f, 6)
