"""
回测报告序列化

将 BacktestEngine 运行结果转换为可持久化到数据库、可通过 API 返回的字典。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

import pandas as pd

from app.engine.backtest.metrics import BacktestMetrics


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
    """
    构建完整回测报告字典。
    equity_curve: index=datetime, values=portfolio_value
    """
    equity_points = _equity_to_points(equity_curve)

    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "symbol": symbol,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "initial_cash": initial_cash,
        "final_value": round(final_value, 2),
        "params": params,
        "metrics": {
            "total_return_pct": round(metrics.total_return * 100, 4),
            "annual_return_pct": round(metrics.annual_return * 100, 4),
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "max_drawdown_pct": round(metrics.max_drawdown * 100, 4),
            "calmar_ratio": metrics.calmar_ratio,
            "win_rate_pct": round(metrics.win_rate * 100, 4),
            "profit_factor": metrics.profit_factor,
            "total_trades": metrics.total_trades,
            "volatility_pct": round(metrics.volatility * 100, 4),
            "avg_trade_return": metrics.avg_trade_return,
            "trading_days": metrics.trading_days,
        },
        "equity_curve": equity_points,
        "fills": fills[:500],  # 最多返回 500 笔成交（避免响应过大）
        "generated_at": datetime.utcnow().isoformat(),
    }


def _equity_to_points(equity_curve: pd.Series) -> list[dict]:
    """将 equity_curve 转换为前端图表可用的点列表。"""
    if equity_curve.empty:
        return []
    # 采样：超过 1000 点时按等间隔采样，保留首尾
    series = equity_curve
    if len(series) > 1000:
        indices = list(range(0, len(series), len(series) // 1000))
        if indices[-1] != len(series) - 1:
            indices.append(len(series) - 1)
        series = series.iloc[indices]

    return [
        {
            "time": (
                idx.isoformat()
                if isinstance(idx, (pd.Timestamp, datetime))
                else str(idx)
            ),
            "value": round(float(val), 2),
        }
        for idx, val in series.items()
    ]
