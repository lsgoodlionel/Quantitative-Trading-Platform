"""
滚动 / Tearsheet 序列 (Rolling Stats) — C7

产出 pyfolio 式 tearsheet 所需的时间序列：收益序列、累计收益(增长$1)、
滚动夏普、滚动波动、滚动 Beta、仓位暴露、累计换手，以及全样本 Beta/Alpha 标量。

参考: pyfolio rolling Sharpe/beta/vol、exposure、turnover
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from app.engine.backtest.report import _series_to_points

_DEFAULT_WINDOW = 63        # ≈3 个月交易日


def compute_rolling_stats(
    equity_curve: pd.Series,
    returns: pd.Series,
    benchmark_returns: pd.Series | None,
    fills: list[dict],
    window: int = _DEFAULT_WINDOW,
    periods_per_year: int = 252,
) -> dict:
    """计算 C7 tearsheet 滚动序列与全样本 Beta/Alpha，返回可序列化 dict。"""
    if equity_curve is None or equity_curve.empty or len(returns) < 2:
        return _empty(window)

    sqrt_p = math.sqrt(periods_per_year)

    # 收益序列（%）与累计增长
    returns_pct = returns * 100
    cum_returns = (1 + returns).cumprod()

    # 滚动夏普 / 波动
    eff_window = min(window, max(len(returns), 2))
    roll_mean = returns.rolling(eff_window).mean()
    roll_std = returns.rolling(eff_window).std()
    rolling_sharpe = (roll_mean / roll_std * sqrt_p).replace([np.inf, -np.inf], np.nan).dropna()
    rolling_vol = (roll_std * sqrt_p * 100).dropna()

    # 滚动 Beta（对齐基准）
    bench = _align_benchmark(benchmark_returns, returns)
    rolling_beta = _rolling_beta(returns, bench, eff_window)

    # 仓位暴露 / 累计换手
    exposure = _exposure_series(fills, equity_curve)
    turnover = _turnover_series(fills, equity_curve)

    # 全样本 Beta / Alpha
    beta, alpha_annual = _full_beta_alpha(returns, bench, periods_per_year)

    avg_exposure = float(exposure.mean()) * 100 if not exposure.empty else 0.0
    total_turnover = float(turnover.iloc[-1]) if not turnover.empty else 0.0

    return {
        "window": window,
        "returns_series": _series_to_points(returns_pct),
        "cum_returns": _series_to_points(cum_returns),
        "rolling_sharpe": _series_to_points(rolling_sharpe),
        "rolling_volatility": _series_to_points(rolling_vol),
        "rolling_beta": _series_to_points(rolling_beta),
        "exposure_series": _series_to_points(exposure),
        "turnover_series": _series_to_points(turnover),
        "avg_exposure_pct": round(avg_exposure, 4),
        "total_turnover": round(total_turnover, 4),
        "beta": round(beta, 4),
        "alpha_annual_pct": round(alpha_annual, 4),
    }


def _align_benchmark(benchmark_returns: pd.Series | None, returns: pd.Series) -> pd.Series:
    if benchmark_returns is None or benchmark_returns.empty:
        return pd.Series(0.0, index=returns.index)
    return benchmark_returns.reindex(returns.index).fillna(0.0)


def _rolling_beta(returns: pd.Series, bench: pd.Series, window: int) -> pd.Series:
    cov = returns.rolling(window).cov(bench)
    var = bench.rolling(window).var()
    beta = (cov / var).replace([np.inf, -np.inf], np.nan).dropna()
    return beta


def _full_beta_alpha(returns: pd.Series, bench: pd.Series, periods: int) -> tuple[float, float]:
    """全样本 OLS: strategy_returns = alpha + beta * benchmark_returns。"""
    var = float(bench.var())
    if var < 1e-12:
        return 0.0, 0.0
    beta = float(returns.cov(bench) / var)
    alpha_daily = float(returns.mean() - beta * bench.mean())
    return beta, alpha_daily * periods * 100


def _exposure_series(fills: list[dict], equity_curve: pd.Series) -> pd.Series:
    """
    近似仓位暴露 (0/1 阶跃)：由 fills 累计净持仓量推断每根 bar 是否在场。
    (缺乏逐 bar 持仓市值，用有仓/无仓指示近似。)
    """
    if not fills or equity_curve.empty:
        return pd.Series(dtype=float)

    events = []
    for f in fills:
        side = str(f.get("side", "")).upper()
        qty = float(f.get("qty", 0) or 0)
        signed = qty if side == "BUY" else -qty if side == "SELL" else 0.0
        events.append((_parse(f.get("filled_at")), signed))
    events.sort(key=lambda e: e[0])

    exposure = pd.Series(0.0, index=equity_curve.index)
    net = 0.0
    ei = 0
    for ts in equity_curve.index:
        while ei < len(events) and events[ei][0] <= pd.Timestamp(ts):
            net += events[ei][1]
            ei += 1
        exposure.loc[ts] = 1.0 if net > 1e-9 else 0.0
    return exposure


def _turnover_series(fills: list[dict], equity_curve: pd.Series) -> pd.Series:
    """累计换手: cumsum(|成交名义|) / 平均净值。"""
    if not fills or equity_curve.empty:
        return pd.Series(dtype=float)

    avg_equity = float(equity_curve.mean())
    if avg_equity < 1e-9:
        return pd.Series(dtype=float)

    notional = pd.Series(0.0, index=equity_curve.index)
    for f in fills:
        ts = pd.Timestamp(_parse(f.get("filled_at")))
        value = abs(float(f.get("qty", 0) or 0) * float(f.get("price", 0) or 0))
        pos = equity_curve.index.searchsorted(ts)
        pos = min(pos, len(equity_curve) - 1)
        notional.iloc[pos] += value
    return notional.cumsum() / avg_equity


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return datetime.utcnow()


def _empty(window: int) -> dict:
    return {
        "window": window,
        "returns_series": [], "cum_returns": [], "rolling_sharpe": [],
        "rolling_volatility": [], "rolling_beta": [], "exposure_series": [],
        "turnover_series": [],
        "avg_exposure_pct": 0.0, "total_turnover": 0.0, "beta": 0.0, "alpha_annual_pct": 0.0,
    }
