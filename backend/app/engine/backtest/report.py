"""
回测报告序列化

将 BacktestEngine 运行结果转换为 API 返回的字典。
新增: 回撤序列、月度收益矩阵、交易分布、更丰富指标。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.engine.backtest.metrics import (
    BacktestMetrics,
    compute_drawdown_series,
    compute_monthly_returns,
)


def build_report(
    strategy_id: str,
    strategy_name: str,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    initial_cash: float,
    final_value: float,
    metrics: BacktestMetrics,
    equity_curve: pd.Series,
    fills: list[dict],
    params: dict,
) -> dict:
    """构建完整回测报告字典，包含图表所需的序列数据。"""
    equity_points = _equity_to_points(equity_curve)

    # 回撤序列
    dd_series = compute_drawdown_series(equity_curve)
    drawdown_points = _series_to_points(dd_series * 100)  # 转为百分比

    # 月度收益矩阵
    monthly_returns = compute_monthly_returns(equity_curve)

    # 交易盈亏分布（直方图数据）
    sell_pnls = [f.get("realized_pnl", 0.0) for f in fills if f.get("side") in ("SELL", "sell")]
    pnl_distribution = _build_pnl_histogram(sell_pnls)

    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "symbol": symbol,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "initial_cash": initial_cash,
        "final_value": round(final_value, 2),
        "params": params,
        "metrics": _metrics_to_dict(metrics),
        "equity_curve": equity_points,
        "drawdown_series": drawdown_points,
        "monthly_returns": monthly_returns,
        "pnl_distribution": pnl_distribution,
        "fills": fills[:500],
        "generated_at": datetime.utcnow().isoformat(),
    }


def _metrics_to_dict(m: BacktestMetrics) -> dict:
    return {
        # 收益
        "total_return_pct": round(m.total_return * 100, 4),
        "annual_return_pct": round(m.annual_return * 100, 4),
        "volatility_pct": round(m.volatility * 100, 4),
        "trading_days": m.trading_days,
        # 风险调整
        "sharpe_ratio": m.sharpe_ratio,
        "sortino_ratio": m.sortino_ratio,
        "calmar_ratio": m.calmar_ratio,
        "omega_ratio": m.omega_ratio,
        # 回撤
        "max_drawdown_pct": round(m.max_drawdown * 100, 4),
        "max_drawdown_duration": m.max_drawdown_duration,
        # 交易统计
        "total_trades": m.total_trades,
        "win_rate_pct": round(m.win_rate * 100, 4),
        "profit_factor": m.profit_factor,
        "expectancy": m.expectancy,
        "avg_win": m.avg_win,
        "avg_loss": m.avg_loss,
        "avg_trade_return": m.avg_trade_return,
        "sqn": m.sqn,
        # 连胜连败
        "max_consecutive_wins": m.max_consecutive_wins,
        "max_consecutive_losses": m.max_consecutive_losses,
        # 基准
        "buy_hold_return_pct": round(m.buy_hold_return * 100, 4),
    }


def _equity_to_points(equity_curve: pd.Series) -> list[dict]:
    """将 equity_curve 转换为前端图表点列表（最多 1000 点）。"""
    if equity_curve.empty:
        return []
    series = equity_curve
    if len(series) > 1000:
        step = len(series) // 1000
        indices = list(range(0, len(series), step))
        if indices[-1] != len(series) - 1:
            indices.append(len(series) - 1)
        series = series.iloc[indices]
    return [
        {
            "time": _fmt_time(idx),
            "value": round(float(val), 2),
        }
        for idx, val in series.items()
    ]


def _series_to_points(series: pd.Series) -> list[dict]:
    """通用序列转点列表。"""
    if series.empty:
        return []
    # 采样
    if len(series) > 1000:
        step = len(series) // 1000
        indices = list(range(0, len(series), step))
        if indices[-1] != len(series) - 1:
            indices.append(len(series) - 1)
        series = series.iloc[indices]
    return [
        {"time": _fmt_time(idx), "value": round(float(val), 4)}
        for idx, val in series.items()
    ]


def _build_pnl_histogram(pnls: list[float], bins: int = 20) -> list[dict]:
    """构建盈亏分布直方图数据，返回区间标签和频次。"""
    if not pnls:
        return []
    import numpy as np
    arr = np.array(pnls)
    counts, edges = np.histogram(arr, bins=bins)
    result = []
    for i, count in enumerate(counts):
        label = f"{edges[i]:.0f}~{edges[i+1]:.0f}"
        result.append({
            "range": label,
            "count": int(count),
            "positive": edges[i] >= 0,
        })
    return result


def _fmt_time(idx) -> str:
    if isinstance(idx, (pd.Timestamp, datetime)):
        return idx.isoformat()
    return str(idx)
