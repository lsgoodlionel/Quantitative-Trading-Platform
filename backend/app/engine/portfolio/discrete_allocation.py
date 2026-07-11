"""
离散配置 (D2)：连续权重 → 整数股数

将优化器输出的连续权重，在给定现金预算与最新价格下，转换为可执行的整数股数。

两种算法：
- greedy（默认，无求解器）：先按 floor 买入，再逐股填补最欠配的资产。
- lp（scipy.optimize.milp 整数线性规划）：最小化目标金额偏差 + 剩余现金。

Wave-1b 仅支持 long-only（负权重直接报错）。

参考签名（不复制实现）:
- refs/PyPortfolioOpt/pypfopt/discrete_allocation.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NEGLIGIBLE_WEIGHT = 1e-4  # 低于此权重的资产被丢弃


class AllocationMethod(str, Enum):
    GREEDY = "greedy"  # 贪心迭代（无求解器，默认）
    LP = "lp"          # 整数线性规划（scipy.optimize.milp）


@dataclass
class DiscreteAllocationResult:
    method: str
    shares: dict[str, int]                          # symbol → 整数股数（零仓位剔除）
    leftover_cash: float                            # 未花出的现金
    allocated_value: float                          # Σ shares[s] * price[s]
    total_value: float                              # 输入预算
    allocation_weights: dict[str, float]            # 实际权重 = shares*price / allocated_value
    rmse: float                                     # 实际与目标权重的 RMSE
    skipped: list[str] = field(default_factory=list)  # 无价格 / 被丢弃的 symbol


# ── 输入清洗与校验 ────────────────────────────────────────────

def _prepare(
    weights: dict[str, float],
    latest_prices: dict[str, float] | pd.Series,
    total_value: float,
) -> tuple[list[str], np.ndarray, np.ndarray, list[str]]:
    """
    清洗输入，返回 (symbols, target_weights, prices, skipped)。
    权重已在保留资产上重新归一化。
    """
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights 必须是非空 dict")
    if total_value <= 0:
        raise ValueError("total_value 必须大于 0")

    if isinstance(latest_prices, pd.Series):
        price_map = {str(k): float(v) for k, v in latest_prices.items()}
    else:
        price_map = {str(k): float(v) for k, v in latest_prices.items()}

    skipped: list[str] = []
    kept: dict[str, float] = {}

    for sym, w in weights.items():
        if w is None or (isinstance(w, float) and np.isnan(w)):
            raise ValueError(f"权重包含 NaN: {sym}")
        if w < 0:
            raise ValueError("不支持负权重（Wave-1b 仅 long-only）")
        if w < NEGLIGIBLE_WEIGHT:
            continue  # 可忽略权重，丢弃但不计入 skipped
        price = price_map.get(sym)
        if price is None or price <= 0:
            skipped.append(sym)
            continue
        kept[sym] = w

    if not kept:
        raise ValueError("清洗后没有可配置的资产（价格缺失或权重过小）")

    symbols = list(kept.keys())
    raw = np.array([kept[s] for s in symbols], dtype=float)
    target_weights = raw / raw.sum()  # 在保留资产上重新归一化
    prices = np.array([price_map[s] for s in symbols], dtype=float)
    return symbols, target_weights, prices, skipped


def _build_result(
    method: str,
    symbols: list[str],
    shares_arr: np.ndarray,
    prices: np.ndarray,
    target_weights: np.ndarray,
    total_value: float,
    skipped: list[str],
) -> DiscreteAllocationResult:
    """由整数股数数组构造结果对象（含实际权重与 RMSE）。"""
    values = shares_arr * prices
    allocated_value = float(values.sum())
    leftover_cash = float(total_value - allocated_value)

    shares_dict: dict[str, int] = {}
    realized_weights: dict[str, float] = {}
    for i, sym in enumerate(symbols):
        s = int(round(shares_arr[i]))
        if s <= 0:
            continue
        shares_dict[sym] = s
        realized_weights[sym] = (
            round(float(values[i] / allocated_value), 4) if allocated_value > 0 else 0.0
        )

    # RMSE：实际权重 vs 目标权重（按保留资产对齐）
    if allocated_value > 0:
        realized_arr = values / allocated_value
    else:
        realized_arr = np.zeros_like(values)
    rmse = float(np.sqrt(np.mean((realized_arr - target_weights) ** 2)))

    return DiscreteAllocationResult(
        method=method,
        shares=shares_dict,
        leftover_cash=round(max(leftover_cash, 0.0), 2),
        allocated_value=round(allocated_value, 2),
        total_value=round(float(total_value), 2),
        allocation_weights=realized_weights,
        rmse=round(rmse, 6),
        skipped=skipped,
    )


# ── greedy 算法 ───────────────────────────────────────────────

def greedy_allocation(
    weights: dict[str, float],
    latest_prices: dict[str, float] | pd.Series,
    total_value: float,
) -> DiscreteAllocationResult:
    """
    贪心配置：先 floor 买入（绝不超支），再逐股填补最欠配资产，
    直到没有可负担且能缩小缺口的资产为止。
    """
    symbols, target_weights, prices, skipped = _prepare(weights, latest_prices, total_value)

    # 第一遍：floor 买入
    target_values = target_weights * total_value
    shares = np.floor(target_values / prices)
    spent = float((shares * prices).sum())
    remaining = total_value - spent

    # 第二遍：逐股填补最欠配的资产
    n = len(symbols)
    while True:
        affordable = prices <= remaining + 1e-9
        if not affordable.any():
            break
        current_value = shares * prices
        current_weights = current_value / total_value
        deficits = target_weights - current_weights  # 正值 = 欠配
        # 仅在可负担资产中选缺口最大者
        masked = np.where(affordable, deficits, -np.inf)
        best = int(np.argmax(masked))
        if masked[best] <= 0:
            break  # 无欠配资产可改善
        shares[best] += 1
        remaining -= prices[best]
        if remaining < prices.min() - 1e-9:
            break
    del n

    return _build_result(
        AllocationMethod.GREEDY.value, symbols, shares, prices,
        target_weights, total_value, skipped,
    )


# ── lp 算法（scipy.optimize.milp）─────────────────────────────

def lp_allocation(
    weights: dict[str, float],
    latest_prices: dict[str, float] | pd.Series,
    total_value: float,
) -> DiscreteAllocationResult:
    """
    整数 LP 配置：最小化 Σ|target_value_i - shares_i*price_i| + leftover，
    约束 shares_i ≥ 0 整数、Σ shares_i*price_i ≤ total_value。

    MILP 不可行 / 无界时回退到 greedy。
    """
    from scipy.optimize import LinearConstraint, milp, Bounds

    symbols, target_weights, prices, skipped = _prepare(weights, latest_prices, total_value)
    n = len(symbols)
    target_values = target_weights * total_value

    # 决策变量: [shares_0..n-1, u_0..n-1]
    #   u_i ≥ |target_value_i - shares_i*price_i|（用两条线性约束线性化）
    # 目标: min Σ u_i + leftover
    #   leftover = total_value - Σ shares_i*price_i（常数项忽略，等价于 min -Σ shares_i*price_i）
    # 合并目标: min Σ u_i - Σ price_i*shares_i
    num_vars = 2 * n

    cost = np.zeros(num_vars)
    cost[:n] = -prices          # -Σ price_i*shares_i（鼓励花出现金 → 最小化 leftover）
    cost[n:] = 1.0              # Σ u_i

    constraints: list[LinearConstraint] = []

    # 预算: Σ price_i*shares_i ≤ total_value
    budget_row = np.zeros(num_vars)
    budget_row[:n] = prices
    constraints.append(LinearConstraint(budget_row, -np.inf, total_value))

    # 绝对值线性化:
    #   u_i - price_i*shares_i ≥ -target_value_i
    #   u_i + price_i*shares_i ≥  target_value_i
    a_neg = np.zeros((n, num_vars))
    a_pos = np.zeros((n, num_vars))
    for i in range(n):
        a_neg[i, i] = -prices[i]
        a_neg[i, n + i] = 1.0
        a_pos[i, i] = prices[i]
        a_pos[i, n + i] = 1.0
    constraints.append(LinearConstraint(a_neg, -target_values, np.inf))
    constraints.append(LinearConstraint(a_pos, target_values, np.inf))

    # 变量边界与整数性
    max_shares = np.floor(total_value / prices) + 1
    lb = np.zeros(num_vars)
    ub = np.concatenate([max_shares, np.full(n, np.inf)])
    bounds = Bounds(lb, ub)
    integrality = np.concatenate([np.ones(n), np.zeros(n)])  # shares 为整数，u 连续

    try:
        res = milp(
            c=cost,
            constraints=constraints,
            integrality=integrality,
            bounds=bounds,
        )
    except Exception as e:
        logger.warning("MILP 求解异常，回退 greedy: %s", e)
        return greedy_allocation(weights, latest_prices, total_value)

    if not res.success or res.x is None:
        logger.warning("MILP 不可行/无界，回退 greedy: %s", getattr(res, "message", ""))
        return greedy_allocation(weights, latest_prices, total_value)

    shares = np.round(res.x[:n])
    return _build_result(
        AllocationMethod.LP.value, symbols, shares, prices,
        target_weights, total_value, skipped,
    )


# ── 主入口 ────────────────────────────────────────────────────

def allocate(
    weights: dict[str, float],
    latest_prices: dict[str, float] | pd.Series,
    total_value: float,
    method: AllocationMethod | str = AllocationMethod.GREEDY,
) -> DiscreteAllocationResult:
    """连续权重 → 整数股数配置的统一入口。"""
    try:
        m = AllocationMethod(method) if not isinstance(method, AllocationMethod) else method
    except ValueError:
        raise ValueError(f"未知的配置方法: {method}")

    if m == AllocationMethod.LP:
        return lp_allocation(weights, latest_prices, total_value)
    return greedy_allocation(weights, latest_prices, total_value)
