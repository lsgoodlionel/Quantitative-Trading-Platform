"""
回测引擎主循环

Cerebro 风格的 bar 驱动引擎。
参考: refs/backtrader/backtrader/cerebro.py Cerebro.run()
参考: refs/zipline-reloaded/zipline/algorithm.py TradingAlgorithm

流程:
  1. 加载历史 bar 序列
  2. 初始化 SimulatedBroker
  3. 逐 bar 迭代：
     a. 撮合上一个 bar 挂的订单（用当前 bar 的 open）
     b. 构建 StrategyContext（含历史 DataFrame）
     c. 调用 strategy.on_bar(ctx)
     d. 记录当前净值点
  4. 结束后调用 on_stop()，计算绩效指标，生成报告
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from app.data.models import Bar, Market
from app.engine.backtest.broker import SimulatedBroker
from app.engine.backtest.commission import CommissionModel
from app.engine.backtest.metrics import (
    BacktestMetrics,
    compute_metrics,
    TRADING_DAYS_US,
    TRADING_DAYS_HK,
)
from app.engine.backtest.report import build_report
from app.engine.backtest.slippage import SlippageModel
from app.strategy.context import StrategyContext

if TYPE_CHECKING:
    from app.strategy.base import StrategyBase

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    market: Market = Market.US
    commission_model: CommissionModel | None = None
    slippage_model: SlippageModel | None = None
    # 预热期：前 warmup_bars 根 bar 不下单（等指标稳定）
    warmup_bars: int = 0


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    start_date: datetime
    end_date: datetime
    initial_cash: float
    final_value: float
    metrics: BacktestMetrics
    equity_curve: pd.Series
    fills: list[dict]
    report: dict


class BacktestEngine:
    """
    单标的回测引擎。

    用法:
        engine = BacktestEngine(config)
        result = engine.run(strategy, bars)
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()

    def run(
        self,
        strategy: "StrategyBase",
        bars: list[Bar],
        strategy_id: str = "backtest",
    ) -> BacktestResult:
        if len(bars) < 2:
            raise ValueError("At least 2 bars required for backtest")

        bars = sorted(bars, key=lambda b: b.time)
        cfg = self._config

        broker = SimulatedBroker(
            initial_cash=cfg.initial_cash,
            market=cfg.market,
            commission_model=cfg.commission_model,
            slippage_model=cfg.slippage_model,
        )

        # 将 bars 转为 DataFrame，便于策略访问历史
        df_all = _bars_to_df(bars)

        equity_points: list[tuple[datetime, float]] = []
        all_fills: list[dict] = []

        # 构建初始 context 调用 on_start
        init_ctx = StrategyContext(bar=bars[0], history=df_all.iloc[:1], broker=broker)
        strategy.on_start(init_ctx)

        for i, bar in enumerate(bars):
            # A股 T+1: 新交易日开始时解除前一日买入的限制
            broker.advance_day()

            # 先撮合（用当前 bar open 成交上一根 bar 的挂单）
            new_fills = broker.process_bar(bar)
            for fill in new_fills:
                all_fills.append(_fill_to_dict(fill))

            # 预热期内不调用策略逻辑
            if i < cfg.warmup_bars:
                prices = {bar.symbol: bar.close}
                equity_points.append((bar.time, broker.portfolio_value(prices)))
                continue

            # 构建上下文（历史含当前 bar）
            history = df_all.iloc[: i + 1]
            ctx = StrategyContext(bar=bar, history=history, broker=broker)

            try:
                strategy.on_bar(ctx)
            except Exception:
                logger.exception("Strategy error at bar %s", bar.time)

            prices = {bar.symbol: bar.close}
            equity_points.append((bar.time, broker.portfolio_value(prices)))

        # 清空剩余挂单，结束
        broker.cancel_all_pending()
        final_prices = {bars[-1].symbol: bars[-1].close}
        final_ctx = StrategyContext(bar=bars[-1], history=df_all, broker=broker)
        strategy.on_stop(final_ctx)

        final_value = broker.portfolio_value(final_prices)

        equity_curve = pd.Series(
            [v for _, v in equity_points],
            index=pd.DatetimeIndex([t for t, _ in equity_points]),
            name="equity",
        )

        from app.engine.backtest.metrics import TRADING_DAYS_A
        if cfg.market == Market.HK:
            trading_days_per_year = TRADING_DAYS_HK
        elif cfg.market == Market.A:
            trading_days_per_year = TRADING_DAYS_A
        else:
            trading_days_per_year = TRADING_DAYS_US

        metrics = compute_metrics(
            equity_curve=equity_curve,
            fills=all_fills,
            initial_cash=cfg.initial_cash,
            bars_open=bars[0].open,
            bars_close=bars[-1].close,
            trading_days_per_year=trading_days_per_year,
        )

        report = build_report(
            strategy_id=strategy_id,
            strategy_name=strategy.name,
            symbol=bars[0].symbol,
            start_date=bars[0].time,
            end_date=bars[-1].time,
            initial_cash=cfg.initial_cash,
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=all_fills,
            params=strategy._params,
        )

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=bars[0].symbol,
            start_date=bars[0].time,
            end_date=bars[-1].time,
            initial_cash=cfg.initial_cash,
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=all_fills,
            report=report,
        )


def _bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time": b.time,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "vwap": b.vwap,
            }
            for b in bars
        ]
    ).set_index("time")


def _fill_to_dict(fill) -> dict:
    return {
        "order_id": fill.order_id,
        "symbol": fill.symbol,
        "market": fill.market.value if hasattr(fill.market, "value") else str(fill.market),
        "side": fill.side.value if hasattr(fill.side, "value") else str(fill.side),
        "qty": fill.qty,
        "price": round(fill.price, 4),
        "commission": round(fill.commission, 4),
        "filled_at": fill.filled_at.isoformat() if fill.filled_at else None,
        "realized_pnl": round(fill.realized_pnl, 4),
        # C7 回合分析 / 标签分组维度（缺省 None，roundtrips 会优雅回退）
        "entry_tag": getattr(fill, "entry_tag", None),
        "exit_reason": getattr(fill, "exit_reason", None),
        "direction": getattr(fill, "direction", "long"),
    }
