"""把一次回测的结果压成可比对的 JSON 快照。

浮点一律按固定位数取整：抹平不同 CPU/BLAS 的末位差异，
同时保留足以发现真实行为漂移的精度。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.data.models import Market
from app.engine.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.strategy.presets import STRATEGY_REGISTRY
from tests.regression.fixtures import INITIAL_CASH, Regime, make_bars

# 取整位数：价格/金额 6 位，比率类 8 位
_MONEY_DP = 6
_RATIO_DP = 8


def _round(value: Any, dp: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(value, int):
        return value
    return round(float(value), dp)


def _fill_row(fill: dict) -> list[Any]:
    """成交记录压成定长数组：逐笔比对的最小充分信息。"""
    return [
        fill["side"],
        fill["qty"],
        _round(fill["price"], _MONEY_DP),
        _round(fill["commission"], _MONEY_DP),
        _round(fill["realized_pnl"], _MONEY_DP),
        fill["filled_at"],
    ]


def run_case(strategy_name: str, market: Market, regime: Regime) -> BacktestResult:
    """跑一个回归用例。参数一律用策略默认值，避免基线依赖外部配置。"""
    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    bars = make_bars(market, regime)
    engine = BacktestEngine(BacktestConfig(initial_cash=INITIAL_CASH, market=market))
    return engine.run(strategy_cls(), bars, strategy_id=f"regression-{strategy_name}")


def snapshot_result(result: BacktestResult) -> dict[str, Any]:
    """BacktestResult → 稳定的可序列化字典。"""
    metrics = {k: _round(v, _RATIO_DP) for k, v in asdict(result.metrics).items()}
    equity = result.equity_curve

    return {
        "final_value": _round(result.final_value, _MONEY_DP),
        "metrics": metrics,
        "fills_count": len(result.fills),
        "fills": [_fill_row(f) for f in result.fills],
        # 净值曲线不整条入库（太大），只锚定长度与首/中/末三点
        "equity_len": int(len(equity)),
        "equity_head": _round(float(equity.iloc[0]), _MONEY_DP),
        "equity_mid": _round(float(equity.iloc[len(equity) // 2]), _MONEY_DP),
        "equity_tail": _round(float(equity.iloc[-1]), _MONEY_DP),
    }


def build_snapshot(strategy_name: str, market: Market, regime: Regime) -> dict[str, Any]:
    return snapshot_result(run_case(strategy_name, market, regime))
