"""
回测绩效指标计算

参考: refs/backtrader/backtrader/analyzers/  (SQN, DrawDown, TradeAnalyzer)
     refs/jesse/jesse/services/report.py     (连胜连败, Expectancy)
     refs/freqtrade/freqtrade/optimize/      (Omega ratio)
计算标准金融指标: Sharpe、Sortino、MaxDD、Calmar、年化收益等。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import numpy as np


TRADING_DAYS_US = 252
TRADING_DAYS_HK = 245
TRADING_DAYS_A = 242    # 沪深 A 股约 242 个交易日/年


@dataclass(frozen=True)
class BacktestMetrics:
    # ── 收益类 ────────────────────────────────────────────────
    total_return: float          # 总收益率 (0.15 = 15%)
    annual_return: float         # 年化收益率
    volatility: float            # 年化波动率
    trading_days: int            # 实际交易天数

    # ── 风险调整收益 ──────────────────────────────────────────
    sharpe_ratio: float          # 夏普比率（无风险利率 = 0）
    sortino_ratio: float         # 索提诺比率（仅下行波动）
    calmar_ratio: float          # 卡玛比率 = 年化 / |最大回撤|
    omega_ratio: float           # Omega 比率（阈值 = 0）

    # ── 回撤 ─────────────────────────────────────────────────
    max_drawdown: float          # 最大回撤（负数，如 -0.12）
    max_drawdown_duration: int   # 最长回撤持续天数

    # ── 交易统计 ─────────────────────────────────────────────
    total_trades: int            # 总交易次数（按成交卖出方向计）
    win_rate: float              # 胜率
    profit_factor: float         # 获利因子 = 总盈利 / |总亏损|
    expectancy: float            # 期望值 = 平均每笔期望盈亏（货币）
    avg_win: float               # 平均盈利笔
    avg_loss: float              # 平均亏损笔（负数）
    avg_trade_return: float      # 平均每笔收益率（%）
    sqn: float                   # System Quality Number (Van Tharp)

    # ── 连胜连败 ──────────────────────────────────────────────
    max_consecutive_wins: int    # 最大连胜次数
    max_consecutive_losses: int  # 最大连败次数

    # ── 基准对比 ──────────────────────────────────────────────
    buy_hold_return: float       # 同期买入持有收益率


def compute_metrics(
    equity_curve: pd.Series,    # index=datetime, values=组合净值
    fills: list[dict],          # Fill 成交记录列表（含 realized_pnl）
    initial_cash: float,
    bars_open: float | None = None,   # 首根 K 线开盘价（用于买入持有对比）
    bars_close: float | None = None,  # 末根 K 线收盘价
    trading_days_per_year: int = TRADING_DAYS_US,
) -> BacktestMetrics:
    """从净值曲线和成交记录计算全套绩效指标。"""
    if equity_curve.empty or len(equity_curve) < 2:
        return _empty_metrics()

    returns = equity_curve.pct_change().dropna()
    n_days = len(returns)

    # ── 收益 ──────────────────────────────────────────────────
    total_return = (equity_curve.iloc[-1] - initial_cash) / initial_cash
    years = n_days / trading_days_per_year
    annual_return = (1 + total_return) ** (1 / max(years, 1e-6)) - 1
    volatility = float(returns.std() * math.sqrt(trading_days_per_year))

    # ── 风险调整收益 ──────────────────────────────────────────
    sharpe = annual_return / volatility if volatility > 1e-10 else 0.0

    downside = returns[returns < 0]
    downside_std = float(downside.std() * math.sqrt(trading_days_per_year)) if len(downside) > 1 else 1e-10
    sortino = annual_return / downside_std if downside_std > 1e-10 else 0.0

    # Omega ratio: sum(max(r-threshold,0)) / sum(max(threshold-r,0)), threshold=0
    gains_sum = float(returns[returns > 0].sum())
    losses_sum = float(abs(returns[returns < 0].sum()))
    omega = gains_sum / losses_sum if losses_sum > 1e-10 else float("inf")

    # ── 回撤 ──────────────────────────────────────────────────
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdown_series = (cumulative - rolling_max) / rolling_max
    max_dd = float(drawdown_series.min())

    # 最长回撤持续时长
    in_dd = drawdown_series < -1e-6
    max_dd_duration = _max_consecutive_true(in_dd)

    calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

    # ── 交易统计（按卖出方向计一笔完整交易）─────────────────────
    sell_fills = [f for f in fills if f.get("side") in ("SELL", "sell")]
    pnls = [f.get("realized_pnl", 0.0) for f in sell_fills]
    total_trades = len(pnls)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_profit / total_loss if total_loss > 1e-10 else float("inf")

    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = float(np.mean(pnls)) if pnls else 0.0
    avg_trade_return = expectancy / initial_cash * 100 if initial_cash > 0 else 0.0

    # SQN = (E[pnl] / std[pnl]) * sqrt(N)
    if total_trades >= 2:
        pnl_std = float(np.std(pnls, ddof=1))
        sqn = (expectancy / pnl_std * math.sqrt(total_trades)) if pnl_std > 1e-10 else 0.0
    else:
        sqn = 0.0

    # 连胜/连败
    max_consec_wins, max_consec_losses = _consecutive_stats(pnls)

    # ── 买入持有对比 ──────────────────────────────────────────
    if bars_open and bars_close and bars_open > 1e-10:
        buy_hold = (bars_close - bars_open) / bars_open
    else:
        buy_hold = 0.0

    return BacktestMetrics(
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        volatility=round(volatility, 6),
        trading_days=n_days,
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4),
        omega_ratio=round(min(omega, 99.9), 4),
        max_drawdown=round(max_dd, 6),
        max_drawdown_duration=max_dd_duration,
        total_trades=total_trades,
        win_rate=round(win_rate, 4),
        profit_factor=round(min(profit_factor, 99.9), 4),
        expectancy=round(expectancy, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        avg_trade_return=round(avg_trade_return, 4),
        sqn=round(sqn, 4),
        max_consecutive_wins=max_consec_wins,
        max_consecutive_losses=max_consec_losses,
        buy_hold_return=round(buy_hold, 6),
    )


def compute_drawdown_series(equity_curve: pd.Series) -> pd.Series:
    """返回每日回撤序列（百分比，负数）。"""
    returns = equity_curve.pct_change().fillna(0)
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    return (cumulative - rolling_max) / rolling_max


def compute_monthly_returns(equity_curve: pd.Series) -> dict[str, dict[str, float]]:
    """
    计算月度收益矩阵: {year: {month: return_pct}}
    用于月度热力图。
    """
    if equity_curve.empty:
        return {}
    monthly = equity_curve.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()
    result: dict[str, dict[str, float]] = {}
    for ts, val in monthly_ret.items():
        year = str(ts.year)
        month = f"{ts.month:02d}"
        if year not in result:
            result[year] = {}
        result[year][month] = round(float(val) * 100, 2)
    return result


def _max_consecutive_true(series: pd.Series) -> int:
    """最长连续 True 的长度。"""
    max_len = cur_len = 0
    for v in series:
        if v:
            cur_len += 1
            max_len = max(max_len, cur_len)
        else:
            cur_len = 0
    return max_len


def _consecutive_stats(pnls: list[float]) -> tuple[int, int]:
    """返回 (最大连胜次数, 最大连败次数)。"""
    if not pnls:
        return 0, 0
    max_wins = max_losses = cur_wins = cur_losses = 0
    for p in pnls:
        if p > 0:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)
    return max_wins, max_losses


def _empty_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_return=0.0, annual_return=0.0, volatility=0.0, trading_days=0,
        sharpe_ratio=0.0, sortino_ratio=0.0, calmar_ratio=0.0, omega_ratio=0.0,
        max_drawdown=0.0, max_drawdown_duration=0,
        total_trades=0, win_rate=0.0, profit_factor=0.0,
        expectancy=0.0, avg_win=0.0, avg_loss=0.0, avg_trade_return=0.0, sqn=0.0,
        max_consecutive_wins=0, max_consecutive_losses=0,
        buy_hold_return=0.0,
    )


# 向后兼容别名
def equity_to_drawdown_series(equity_curve: pd.Series) -> pd.Series:
    return compute_drawdown_series(equity_curve)
