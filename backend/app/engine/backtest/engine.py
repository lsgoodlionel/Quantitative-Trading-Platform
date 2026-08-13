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
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd

from app.data.adjustments import (
    CorporateAction,
    apply_adjustments,
    build_adjustment_factors,
    build_volume_factors,
    dividend_cash_per_share,
    fetch_corporate_actions,
)
from app.data.models import Bar, Market
from app.engine.backtest.broker import SimulatedBroker
from app.engine.backtest.commission import CommissionModel
from app.engine.backtest.metrics import (
    TRADING_DAYS_HK,
    TRADING_DAYS_US,
    BacktestMetrics,
)
from app.engine.backtest.slippage import SlippageModel
from app.engine.calendar.base import TradingCalendar
from app.engine.controls.base import AccountControl, TradingControl

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
    # ── K2 做空（默认关闭 = 现状行为）──────────────────────
    allow_short: bool = False
    short_borrow_rate: float = 0.0   # 年化融券费率，按持仓自然日计提
    # ── K7 交易日历（默认 None = 按 bar 顺序迭代，不做任何日历校验）──────
    calendar: TradingCalendar | None = None
    # ── K8 复权（默认 False = 用原始价）────────────────────────────────
    adjust_prices: bool = False
    # 公司行为事件流；adjust_prices=True 而此项为 None 时尝试联网拉取（失败降级）
    corporate_actions: list[CorporateAction] | None = None
    # ── L1 交易控制器（默认全空 = 现状；与实盘 OMS 共用同一批实例）────
    controls: list[TradingControl] = field(default_factory=list)
    account_controls: list[AccountControl] = field(default_factory=list)


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
        strategy: StrategyBase,
        bars: list[Bar],
        strategy_id: str = "backtest",
    ) -> BacktestResult:
        """签名与返回类型保持不变；内部委托给组合引擎的 1 标的特例。"""
        # 局部 import：portfolio_engine 反过来依赖本模块的配置与工具函数
        from app.engine.backtest.portfolio_engine import (
            PortfolioBacktestConfig,
            PortfolioBacktestEngine,
        )

        cfg = self._config
        if isinstance(cfg, PortfolioBacktestConfig):
            portfolio_cfg = cfg
        else:
            # 只搬 BacktestConfig 声明过的字段：其他子类的扩展字段原样丢弃，
            # 好过用 vars(cfg) 把未知键塞进构造函数直接炸掉
            portfolio_cfg = PortfolioBacktestConfig(
                **{f.name: getattr(cfg, f.name) for f in fields(BacktestConfig)}
            )
        return PortfolioBacktestEngine(portfolio_cfg).run_single(
            strategy, bars, strategy_id
        )


def _resolve_trading_days_per_year(cfg: BacktestConfig, bars: list[Bar]) -> int:
    """年化基准（按 bar 序列取区间）。"""
    return trading_days_per_year(cfg, bars[0].time.date(), bars[-1].time.date())


def trading_days_per_year(cfg: BacktestConfig, start: date, end: date) -> int:
    """
    年化基准。**未配置日历时必须返回既有常数** —— 否则所有历史指标全部漂移。
    配置日历时改用区间实测值（契约 waveKb §2.3 第 3 点）。
    """
    from app.engine.backtest.metrics import TRADING_DAYS_A

    if cfg.calendar is not None:
        return int(round(cfg.calendar.sessions_per_year(start, end)))

    if cfg.market == Market.HK:
        return TRADING_DAYS_HK
    if cfg.market == Market.A:
        return TRADING_DAYS_A
    return TRADING_DAYS_US


def _calendar_diagnostics(
    calendar: TradingCalendar | None, bars: list[Bar]
) -> dict | None:
    """
    日历校验：脏数据（非交易日的 bar）与数据缺口（日历有、数据没有）。
    只记录与告警，**不改变撮合逻辑**，也不抛错 —— 数据源脏数据太常见。
    """
    if calendar is None or not bars:
        return None

    bar_dates = [b.time.date() for b in bars]
    start, end = bar_dates[0], bar_dates[-1]

    non_sessions = calendar.non_sessions(bar_dates)
    gaps = calendar.missing_sessions(bar_dates, start, end)

    if non_sessions:
        logger.warning(
            "%s: %d 根 bar 落在非交易日（前 5 个：%s）",
            calendar.name, len(non_sessions), [d.isoformat() for d in non_sessions[:5]],
        )
    if gaps:
        logger.warning(
            "%s: 缺失 %d 个交易日的数据（前 5 个：%s）",
            calendar.name, len(gaps), [d.isoformat() for d in gaps[:5]],
        )

    return {
        "name": calendar.name,
        "expected_sessions": calendar.sessions_count(start, end),
        "observed_bars": len(bar_dates),
        "non_session_bars": [d.isoformat() for d in non_sessions],
        "data_gaps": [d.isoformat() for d in gaps],
    }


def _apply_price_adjustments(
    bars: list[Bar], cfg: BacktestConfig
) -> tuple[list[Bar], dict[date, float]]:
    """复权 bars 并返回除权日现金分红表。取不到公司行为时原样返回。"""
    actions = cfg.corporate_actions
    if actions is None:
        actions = fetch_corporate_actions(bars[0].symbol, cfg.market)
    if not actions:
        logger.warning(
            "adjust_prices=True 但 %s 无可用公司行为数据，按原始价回测", bars[0].symbol
        )
        return bars, {}

    # 按会话日去重：分钟级数据下同一天有多根 bar，直接建索引会得到重复的
    # DatetimeIndex，`prices.loc[key]` 会返回 Series 而非标量，float() 直接抛
    # TypeError。取每日**最后**一根 bar 的收盘价 = 当日收盘。
    session_close: dict[date, float] = {}
    for b in bars:
        session_close[b.time.date()] = b.close
    sessions = sorted(session_close)
    closes = pd.Series(
        [session_close[d] for d in sessions],
        index=pd.DatetimeIndex(sessions),
        name="close",
    )

    factors = build_adjustment_factors(actions, sessions, prices=closes)
    volume_factors = build_volume_factors(actions, sessions)
    adjusted = apply_adjustments(bars, factors, volume_factors)

    return adjusted, dividend_cash_per_share(actions, factors)


def _credit_dividend(
    broker: SimulatedBroker, bar: Bar, dividend_cash: dict[date, float]
) -> float:
    """除权日按持仓派发现金分红，返回本次入账金额。"""
    per_share = dividend_cash.get(bar.time.date())
    if not per_share:
        return 0.0

    qty = broker.positions.get(bar.symbol).qty
    if qty <= 0:
        return 0.0

    amount = qty * per_share
    broker.add_cash(amount, reason=f"{bar.symbol} 除权日分红")
    logger.debug("%s %s 分红入账 %.2f（%d 股 × %.4f）", bar.symbol, bar.time.date(), amount, qty, per_share)
    return amount


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
        # K2: 是否为平仓事件 —— metrics.total_trades 的计数口径
        "is_close": getattr(fill, "is_close", None),
    }
