"""
回测绩效指标计算

参考: refs/quantstats/quantstats/stats.py
计算标准金融指标: Sharpe、Sortino、MaxDD、Calmar、年化收益等。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import numpy as np


TRADING_DAYS_US = 252
TRADING_DAYS_HK = 245


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float          # 总收益率 (0.15 = 15%)
    annual_return: float         # 年化收益率
    sharpe_ratio: float          # 夏普比率（无风险利率 = 0）
    sortino_ratio: float         # 索提诺比率
    max_drawdown: float          # 最大回撤（负数，如 -0.12 = -12%）
    calmar_ratio: float          # 卡玛比率 = 年化收益 / |最大回撤|
    win_rate: float              # 胜率（按交易次数）
    profit_factor: float         # 获利因子 = 总盈利 / |总亏损|
    total_trades: int            # 总交易次数（成交笔数）
    volatility: float            # 年化波动率
    avg_trade_return: float      # 平均每笔交易收益率
    trading_days: int            # 实际交易天数


def compute_metrics(
    equity_curve: pd.Series,    # index=datetime, values=组合净值（绝对金额）
    fills: list[dict],          # Fill 成交记录列表（含 realized_pnl）
    initial_cash: float,
    trading_days_per_year: int = TRADING_DAYS_US,
) -> BacktestMetrics:
    """
    从净值曲线和成交记录计算全套绩效指标。

    equity_curve: pd.Series，index 为日期，value 为当日组合总价值。
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return _empty_metrics()

    returns = equity_curve.pct_change().dropna()
    n_days = len(returns)

    # 总收益率
    total_return = (equity_curve.iloc[-1] - initial_cash) / initial_cash

    # 年化收益率（几何年化）
    years = n_days / trading_days_per_year
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    # 年化波动率
    volatility = float(returns.std() * math.sqrt(trading_days_per_year))

    # Sharpe Ratio（无风险利率 = 0）
    sharpe = (annual_return / volatility) if volatility > 1e-10 else 0.0

    # Sortino Ratio（仅下行波动率）
    downside = returns[returns < 0]
    downside_std = float(downside.std() * math.sqrt(trading_days_per_year)) if len(downside) > 1 else 1e-10
    sortino = annual_return / downside_std if downside_std > 1e-10 else 0.0

    # Max Drawdown
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_dd = float(drawdown.min())

    # Calmar Ratio
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

    # 胜率、获利因子（按交易维度）
    pnls = [f.get("realized_pnl", 0.0) for f in fills if f.get("side") == "SELL"]
    total_trades = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_profit / total_loss if total_loss > 1e-10 else float("inf")
    avg_trade_return = sum(pnls) / total_trades if total_trades > 0 else 0.0

    return BacktestMetrics(
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd, 6),
        calmar_ratio=round(calmar, 4),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        total_trades=total_trades,
        volatility=round(volatility, 6),
        avg_trade_return=round(avg_trade_return, 4),
        trading_days=n_days,
    )


def _empty_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_return=0.0,
        annual_return=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        max_drawdown=0.0,
        calmar_ratio=0.0,
        win_rate=0.0,
        profit_factor=0.0,
        total_trades=0,
        volatility=0.0,
        avg_trade_return=0.0,
        trading_days=0,
    )


def equity_to_drawdown_series(equity_curve: pd.Series) -> pd.Series:
    """返回每日回撤序列（百分比，负数）。"""
    returns = equity_curve.pct_change().fillna(0)
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    return (cumulative - rolling_max) / rolling_max
