"""多标的组合回测引擎（Wave K-c / K1）

主时间轴 = 各标的时间戳的**并集**。每个时点：

1. `advance_day()`（A股 T+1 解锁）
2. 除权日派息（K8，默认关闭）
3. 撮合本时点有 bar 的标的（`PortfolioBroker.process_bars`）
4. **风险闸门**：ROI → 止损 → 追踪止损，命中即挂平仓单（K4，默认全部关闭）
5. **仓位调整**：DCA / 分批止盈（L4，`position_adjustment_enable=False` 时 no-op）
6. 调用 `strategy.on_bars(ctx)`（预热期内跳过）
7. 记录组合净值 —— **停牌标的用最后已知价估值**，绝不当成 0

> 第 4 步必须早于第 5、6 步：风险闸门要先于策略意图执行，
> 否则策略会在一个本该止损的仓位上继续加仓。
> 第 5 步早于第 6 步同理 —— 仓位调整属于持仓管理，先于新的策略意图。

时间轴对齐的思路参考自 freqtrade 的 `time_pair_generator`（GPL-3.0）。
出于许可证隔离，此处**只阅读其算法描述后独立实现**，未复制任何代码。

单标的回测是本引擎的特例：`run_single()` 用 `_SingleSymbolAdapter` 把
`StrategyBase` 包成组合策略，产出与旧引擎逐笔一致的 `BacktestResult`，
`BacktestEngine.run()` 直接委托到这里。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd

from app.core.errors import StrategyContractError
from app.data.models import Bar
from app.engine.backtest.daily_result import DailyPnlTracker, PortfolioDailyResult
from app.engine.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    _apply_price_adjustments,
    _bars_to_df,
    _calendar_diagnostics,
    _credit_dividend,
    _fill_to_dict,
    trading_days_per_year,
)
from app.engine.backtest.metrics import BacktestMetrics, compute_metrics
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.report import build_report, metrics_to_dict
from app.engine.backtest.trade import ExitRules, Trade
from app.strategy.context import PortfolioContext, StrategyContext
from app.strategy.precompute import (
    IndicatorBook,
    IndicatorSpec,
    SymbolIndicators,
    build_indicator_store,
    indicator_spec_of,
)

if TYPE_CHECKING:
    from app.strategy.base import PortfolioStrategyBase, StrategyBase

logger = logging.getLogger(__name__)

#: 组合回测最少需要的时点数（与单标的引擎一致）
MIN_TIMEPOINTS = 2


@dataclass
class PortfolioBacktestConfig(BacktestConfig):
    """组合回测配置。继承单标的的全部字段，新增两项组合级约束。"""

    #: 同时持仓标的数上限（None = 不限）
    max_open_positions: int | None = None
    #: 每仓固定金额，供 `ctx.buy_value()` 缺省使用（None = 由策略决定）
    cash_per_position: float | None = None


@dataclass
class PortfolioBacktestResult:
    strategy_name: str
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    initial_cash: float
    final_value: float
    metrics: BacktestMetrics
    equity_curve: pd.Series
    fills: list[dict]
    daily_results: list[PortfolioDailyResult]
    per_symbol_metrics: dict[str, BacktestMetrics]
    report: dict
    #: N4 拒绝信号台账（被拒/被撤的订单）
    rejections: list = field(default_factory=list)
    #: 因台账上限而未留存的拒单数（0 表示台账即全量）
    rejection_overflow: int = 0


class _LazyHistories(Mapping):
    """
    截至当前时点的各标的历史，**按需物化**。

    组合回测下不能每个时点为所有标的都切一遍 DataFrame（50 标的 × 750 时点
    = 3.75 万次无谓切片）。这里只在策略真的取某个标的历史时才做一次
    `iloc[:cursor]` 切片 —— pandas 的整段切片是视图，不复制数据。
    """

    __slots__ = ("_frames", "_cursors")

    def __init__(self, frames: dict[str, pd.DataFrame], cursors: dict[str, int]) -> None:
        self._frames = frames
        self._cursors = cursors

    def __getitem__(self, symbol: str) -> pd.DataFrame:
        frame = self._frames[symbol]           # KeyError 语义即「不在回测标的内」
        return frame.iloc[: self._cursors.get(symbol, 0)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._frames)

    def __len__(self) -> int:
        return len(self._frames)


@dataclass
class _PreparedData:
    """预处理后的回测输入（复权已应用、时间轴已对齐）。"""

    symbols: list[str]
    bars_by_symbol: dict[str, list[Bar]]
    frames: dict[str, pd.DataFrame]
    timeline: list[datetime]
    bars_at: list[dict[str, Bar]]
    dividend_cash: dict[str, dict[date, float]]
    calendar_reports: dict[str, dict]


@dataclass
class _RunState:
    """一次主循环跑完后的原始产出。"""

    broker: PortfolioBroker
    equity: list[tuple[datetime, float]] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    daily_results: list[PortfolioDailyResult] = field(default_factory=list)
    dividend_cash: float = 0.0
    #: 策略是否覆盖了任一 L5 下单钩子（整轮回测判一次，避免逐 bar 反射）
    uses_order_hooks: bool = False
    #: E-a 预算指标（整轮回测算一次）。None = 策略未声明，整条路径不启用
    indicator_store: dict[str, SymbolIndicators] | None = None


class PortfolioBacktestEngine:
    """
    多标的组合回测引擎。

    用法::

        engine = PortfolioBacktestEngine(PortfolioBacktestConfig(initial_cash=1e6))
        result = engine.run(strategy, {"AAPL": bars_a, "MSFT": bars_m})
    """

    def __init__(self, config: PortfolioBacktestConfig | None = None) -> None:
        self._config = config or PortfolioBacktestConfig()

    # ── 组合入口 ─────────────────────────────────────────────

    def run(
        self,
        strategy: PortfolioStrategyBase,
        bars_by_symbol: dict[str, list[Bar]],
        strategy_id: str = "portfolio-backtest",
    ) -> PortfolioBacktestResult:
        prep = self._prepare(bars_by_symbol)
        state = self._execute(strategy, prep)
        return self._build_result(strategy, prep, state, strategy_id)

    # ── 单标的入口（旧引擎的特例）─────────────────────────────

    def run_single(
        self,
        strategy: StrategyBase,
        bars: list[Bar],
        strategy_id: str = "backtest",
    ) -> BacktestResult:
        """把单标的回测跑成组合回测的 1 标的特例，返回旧的 `BacktestResult`。"""
        if len(bars) < MIN_TIMEPOINTS:
            raise ValueError("At least 2 bars required for backtest")

        symbol = bars[0].symbol
        prep = self._prepare({symbol: bars})
        adapter = _SingleSymbolAdapter(strategy, symbol)
        state = self._execute(adapter, prep)
        return self._build_single_result(strategy, prep, state, strategy_id)

    # ── 数据准备 ─────────────────────────────────────────────

    def _prepare(self, bars_by_symbol: dict[str, list[Bar]]) -> _PreparedData:
        if not bars_by_symbol:
            raise ValueError("bars_by_symbol 不能为空")

        cfg = self._config
        symbols = sorted(bars_by_symbol)
        prepared: dict[str, list[Bar]] = {}
        dividends: dict[str, dict[date, float]] = {}
        calendars: dict[str, dict] = {}

        for symbol in symbols:
            bars = sorted(bars_by_symbol[symbol], key=lambda b: b.time)
            if not bars:
                raise ValueError(f"{symbol} 没有任何 bar")
            if cfg.adjust_prices:
                bars, dividend = _apply_price_adjustments(bars, cfg)
                if dividend:
                    dividends[symbol] = dividend
            prepared[symbol] = bars
            report = _calendar_diagnostics(cfg.calendar, bars)
            if report:
                calendars[symbol] = report

        timeline, bars_at = _align_timeline(prepared)
        if len(timeline) < MIN_TIMEPOINTS:
            raise ValueError("At least 2 bars required for backtest")

        return _PreparedData(
            symbols=symbols,
            bars_by_symbol=prepared,
            frames={s: _bars_to_df(b) for s, b in prepared.items()},
            timeline=timeline,
            bars_at=bars_at,
            dividend_cash=dividends,
            calendar_reports=calendars,
        )

    # ── 主循环 ───────────────────────────────────────────────

    def _execute(
        self,
        strategy: PortfolioStrategyBase | _SingleSymbolAdapter,
        prep: _PreparedData,
    ) -> _RunState:
        cfg = self._config
        broker = PortfolioBroker(
            initial_cash=cfg.initial_cash,
            market=cfg.market,
            commission_model=cfg.commission_model,
            slippage_model=cfg.slippage_model,
            allow_short=cfg.allow_short,
            short_borrow_rate=cfg.short_borrow_rate,
            max_open_positions=cfg.max_open_positions,
            controls=cfg.controls,
            account_controls=cfg.account_controls,
        )
        state = _RunState(
            broker=broker,
            uses_order_hooks=_order_hooks_enabled(strategy),
            indicator_store=_build_indicator_store(strategy, prep),
        )
        tracker = DailyPnlTracker()

        cursors = dict.fromkeys(prep.symbols, 0)
        first = {s: (1 if s in prep.bars_at[0] else 0) for s in prep.symbols}
        start_ctx = self._context(prep, state, 0, first)
        self._bind_order_hooks(strategy, state, start_ctx)
        strategy.on_start(start_ctx)

        for idx, ts in enumerate(prep.timeline):
            bars_now = prep.bars_at[idx]
            for symbol in bars_now:
                cursors[symbol] += 1
            self._step(strategy, prep, state, tracker, cursors, idx, ts)

        broker.cancel_all_pending()
        last = len(prep.timeline) - 1
        stop_ctx = self._context(prep, state, last, cursors)
        self._bind_order_hooks(strategy, state, stop_ctx)
        strategy.on_stop(stop_ctx)

        state.daily_results = tracker.finish()
        return state

    def _step(
        self,
        strategy: PortfolioStrategyBase | _SingleSymbolAdapter,
        prep: _PreparedData,
        state: _RunState,
        tracker: DailyPnlTracker,
        cursors: dict[str, int],
        idx: int,
        ts: datetime,
    ) -> None:
        """推进一个时点：解锁 → 派息 → 撮合 → 风险闸门 → 仓位调整 → 策略 → 记净值。"""
        broker = state.broker
        broker.advance_day()

        # 分红在撮合前入账、融券费在 process_bars 里计提，两头都要圈进这个增量
        before = broker.external_cash_flow
        state.dividend_cash += _credit_dividends(broker, prep, idx)

        tracker.begin_day(ts, broker.positions.net_quantities())
        fills = broker.process_bars(ts, prep.bars_at[idx])
        state.fills.extend(_fill_to_dict(f) for f in fills)
        tracker.record(
            fills, broker.mark_prices(), broker.external_cash_flow - before
        )

        # ★ 风险闸门必须先于策略意图执行：否则策略会在一个本该止损的仓位上继续加仓
        self._check_exits(strategy, prep, state, cursors, idx, ts)
        # ★ 仓位调整接在风险闸门之后：已挂出平仓单的标的不会再被加仓
        self._adjust_positions(strategy, prep, state, cursors, idx, ts)

        if idx >= self._config.warmup_bars:
            ctx = self._context(prep, state, idx, cursors)
            self._bind_order_hooks(strategy, state, ctx)
            try:
                strategy.on_bars(ctx)
            except (NotImplementedError, StrategyContractError):
                # 装配错误（把组合策略当单标的策略跑）与契约违规（钩子返回非法值）
                # 都必须炸出来：两者都是确定性的、每根 bar 都会重复的错误，
                # 吞掉只会得到一份「跑完了但结果是假的」的报告。
                # 真正的运行时错误（下一个分支）才该跳过本时点、让回测继续。
                raise
            except Exception:
                logger.exception("Strategy error at %s", ts)

        state.equity.append((ts, broker.portfolio_value()))

    def _check_exits(
        self,
        strategy: PortfolioStrategyBase | _SingleSymbolAdapter,
        prep: _PreparedData,
        state: _RunState,
        cursors: dict[str, int],
        idx: int,
        ts: datetime,
    ) -> None:
        """K4 风险闸门。策略未配置止损/ROI/追踪止损时是一条 getattr + 布尔判断。"""
        if not _exit_rules_of(strategy).is_enabled:
            return
        ctx = self._context(prep, state, idx, cursors)
        self._bind_order_hooks(strategy, state, ctx)
        prices = {s: bar.close for s, bar in prep.bars_at[idx].items()}
        state.broker.check_exit_conditions(strategy, ctx, prices, ts)

    def _adjust_positions(
        self,
        strategy: PortfolioStrategyBase | _SingleSymbolAdapter,
        prep: _PreparedData,
        state: _RunState,
        cursors: dict[str, int],
        idx: int,
        ts: datetime,
    ) -> None:
        """L4 仓位调整。`position_adjustment_enable=False`（默认）时只有一次 getattr。"""
        if not getattr(strategy, "position_adjustment_enable", False):
            return
        ctx = self._context(prep, state, idx, cursors)
        self._bind_order_hooks(strategy, state, ctx)
        prices = {s: bar.close for s, bar in prep.bars_at[idx].items()}
        state.broker.adjust_positions(strategy, ctx, prices, ts)

    @staticmethod
    def _bind_order_hooks(
        strategy: PortfolioStrategyBase | _SingleSymbolAdapter,
        state: _RunState,
        ctx: PortfolioContext,
    ) -> None:
        """把 L5 钩子连到券商上。策略一个钩子都没覆盖时**根本不绑**，撮合路径零开销。"""
        if state.uses_order_hooks:
            state.broker.bind_order_hooks(strategy, ctx)

    def _context(
        self,
        prep: _PreparedData,
        state: _RunState,
        idx: int,
        cursors: dict[str, int],
    ) -> PortfolioContext:
        cfg = self._config
        # 游标在主循环里原地推进，上下文必须拿一份快照 —— 历史视图与指标视图
        # 共用同一份，两者对「现在是第几根 bar」的理解不能有第二个来源
        snapshot = dict(cursors)
        store = state.indicator_store
        return PortfolioContext(
            time=prep.timeline[idx],
            bars=prep.bars_at[idx],
            symbols=prep.symbols,
            broker=state.broker,
            histories=_LazyHistories(prep.frames, snapshot),
            market=cfg.market,
            cash_per_position=cfg.cash_per_position,
            indicators=None if store is None else IndicatorBook(store, snapshot),
        )

    # ── 结果组装 ─────────────────────────────────────────────

    def _build_result(
        self,
        strategy: PortfolioStrategyBase,
        prep: _PreparedData,
        state: _RunState,
        strategy_id: str,
    ) -> PortfolioBacktestResult:
        cfg = self._config
        equity_curve = _to_series(state.equity)
        final_value = state.broker.portfolio_value()
        days = trading_days_per_year(
            cfg, prep.timeline[0].date(), prep.timeline[-1].date()
        )

        # 组合的基准是「各标的等权买入持有」，不是任何单一标的的涨跌
        metrics = replace(
            compute_metrics(
                equity_curve=equity_curve,
                fills=state.fills,
                initial_cash=cfg.initial_cash,
                trading_days_per_year=days,
            ),
            buy_hold_return=round(_equal_weight_buy_hold(prep), 6),
        )
        per_symbol = _per_symbol_metrics(prep, state, cfg.initial_cash, days)

        report = self._report(
            strategy_id, strategy.name, strategy._params,
            symbol=", ".join(prep.symbols),
            span=(prep.timeline[0], prep.timeline[-1]),
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            state=state,
        )
        report.update(_portfolio_report_sections(prep, state, per_symbol))
        self._attach_optional_sections(report, prep, state)

        return PortfolioBacktestResult(
            strategy_name=strategy.name,
            symbols=prep.symbols,
            start_date=prep.timeline[0],
            end_date=prep.timeline[-1],
            initial_cash=cfg.initial_cash,
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=state.fills,
            daily_results=state.daily_results,
            per_symbol_metrics=per_symbol,
            report=report,
            rejections=state.broker.rejections,
            rejection_overflow=state.broker.rejection_overflow,
        )

    def _build_single_result(
        self,
        strategy: StrategyBase,
        prep: _PreparedData,
        state: _RunState,
        strategy_id: str,
    ) -> BacktestResult:
        """还原旧 `BacktestResult` 的字段与取值口径，逐笔与旧引擎一致。"""
        cfg = self._config
        symbol = prep.symbols[0]
        bars = prep.bars_by_symbol[symbol]
        equity_curve = _to_series(state.equity)
        final_value = state.broker.portfolio_value()

        metrics = compute_metrics(
            equity_curve=equity_curve,
            fills=state.fills,
            initial_cash=cfg.initial_cash,
            bars_open=bars[0].open,
            bars_close=bars[-1].close,
            trading_days_per_year=trading_days_per_year(
                cfg, bars[0].time.date(), bars[-1].time.date()
            ),
        )
        report = self._report(
            strategy_id, strategy.name, strategy._params,
            symbol=symbol,
            span=(bars[0].time, bars[-1].time),
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            state=state,
        )
        self._attach_optional_sections(report, prep, state)

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            start_date=bars[0].time,
            end_date=bars[-1].time,
            initial_cash=cfg.initial_cash,
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=state.fills,
            report=report,
            daily_results=state.daily_results,
            rejections=state.broker.rejections,
            rejection_overflow=state.broker.rejection_overflow,
        )

    def _report(
        self,
        strategy_id: str,
        strategy_name: str,
        params: dict,
        *,
        symbol: str,
        span: tuple[datetime, datetime],
        final_value: float,
        metrics: BacktestMetrics,
        equity_curve: pd.Series,
        state: _RunState,
    ) -> dict:
        """组合与单标的共用的报告骨架（两条路径的 report 结构必须保持一致）。"""
        return build_report(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=span[0],
            end_date=span[1],
            initial_cash=self._config.initial_cash,
            final_value=final_value,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=state.fills,
            params=params,
        )

    def _attach_optional_sections(
        self, report: dict, prep: _PreparedData, state: _RunState
    ) -> None:
        """日历/复权关闭时**不往 report 里塞任何新键**，保持既有输出结构不变。"""
        if prep.calendar_reports:
            single = len(prep.symbols) == 1
            first = prep.calendar_reports[prep.symbols[0]] if single else None
            report["calendar"] = first if single else prep.calendar_reports
            report["data_gaps"] = (
                first["data_gaps"] if single
                else {s: r["data_gaps"] for s, r in prep.calendar_reports.items()}
            )
        if self._config.adjust_prices:
            report["adjustments"] = {
                "applied": True,
                "dividend_cash": round(state.dividend_cash, 4),
            }


class _SingleSymbolAdapter:
    """
    把单标的 `StrategyBase` 包装成组合策略。

    刻意**不继承** `PortfolioStrategyBase`：它只是主循环与旧策略之间的转接头，
    对外不该被当成一个可注册的组合策略。
    """

    def __init__(self, inner: StrategyBase, symbol: str) -> None:
        self._inner = inner
        self._symbol = symbol

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def _params(self) -> dict:
        return self._inner._params

    def on_start(self, ctx: PortfolioContext) -> None:
        self._inner.on_start(self._single(ctx))

    def on_bars(self, ctx: PortfolioContext) -> None:
        self._inner.on_bar(self._single(ctx))

    def on_stop(self, ctx: PortfolioContext) -> None:
        self._inner.on_stop(self._single(ctx))

    # ── E-a 指标预算的透传 ───────────────────────────────────

    def indicator_spec(self) -> IndicatorSpec | None:
        return indicator_spec_of(self._inner)

    # ── K4 风险闸门的透传 ────────────────────────────────────

    def exit_rules(self) -> ExitRules:
        return _exit_rules_of(self._inner)

    def custom_stoploss(self, ctx: PortfolioContext, trade: Trade, profit: float) -> float | None:
        return self._inner.custom_stoploss(self._single_or_none(ctx), trade, profit)

    def custom_roi(self, ctx: PortfolioContext, trade: Trade, profit: float) -> float | None:
        return self._inner.custom_roi(self._single_or_none(ctx), trade, profit)

    # ── L4 仓位调整的透传 ────────────────────────────────────

    @property
    def position_adjustment_enable(self) -> bool:
        return getattr(self._inner, "position_adjustment_enable", False)

    @property
    def max_position_adjustments(self) -> int:
        return getattr(self._inner, "max_position_adjustments", 0)

    def adjust_position(
        self, ctx: PortfolioContext, trade: Trade, profit: float
    ) -> float | None:
        return self._inner.adjust_position(self._single_or_none(ctx), trade, profit)

    # ── L5 下单钩子的透传 ────────────────────────────────────

    @property
    def entry_timeout_minutes(self) -> int | None:
        return getattr(self._inner, "entry_timeout_minutes", None)

    @property
    def exit_timeout_minutes(self) -> int | None:
        return getattr(self._inner, "exit_timeout_minutes", None)

    def has_order_hooks(self) -> bool:
        return _order_hooks_enabled(self._inner)

    def confirm_entry(
        self, ctx: PortfolioContext, symbol: str, qty: int, price: float,
        entry_tag: str | None,
    ) -> bool:
        return self._inner.confirm_entry(
            self._single_or_none(ctx), symbol, qty, price, entry_tag
        )

    def confirm_exit(
        self, ctx: PortfolioContext, symbol: str, qty: int, price: float,
        exit_reason: str,
    ) -> bool:
        return self._inner.confirm_exit(
            self._single_or_none(ctx), symbol, qty, price, exit_reason
        )

    def custom_entry_price(
        self, ctx: PortfolioContext, symbol: str, proposed: float
    ) -> float | None:
        return self._inner.custom_entry_price(self._single_or_none(ctx), symbol, proposed)

    def custom_exit_price(
        self, ctx: PortfolioContext, symbol: str, proposed: float, exit_reason: str
    ) -> float | None:
        return self._inner.custom_exit_price(
            self._single_or_none(ctx), symbol, proposed, exit_reason
        )

    def _single_or_none(self, ctx: PortfolioContext) -> StrategyContext | None:
        """本时点该标的停牌时没有 bar，此时把 None 交给钩子，好过硬造一根假 bar。"""
        if self._symbol not in ctx.bars:
            return None
        return self._single(ctx)

    def _single(self, ctx: PortfolioContext) -> StrategyContext:
        book = ctx.indicators
        return StrategyContext(
            bar=ctx.bars[self._symbol],
            history=ctx.histories[self._symbol],
            broker=ctx.broker,
            indicators=None if book is None else book.view(self._symbol),
        )


# ── 模块级辅助 ────────────────────────────────────────────────


def _exit_rules_of(strategy: object) -> ExitRules:
    """
    取策略的退出规则。

    引擎历史上一直接受**鸭子类型**的策略桩（只实现 on_start/on_bar/on_stop），
    它们没有 `exit_rules`。对这类策略视为「未配置任何风险闸门」，
    而不是让一个与止损无关的回测因为缺方法直接崩掉。
    """
    getter = getattr(strategy, "exit_rules", None)
    return getter() if callable(getter) else ExitRules()


def _build_indicator_store(
    strategy: object, prep: _PreparedData
) -> dict[str, SymbolIndicators] | None:
    """
    E-a 指标预算：整轮回测把声明的指标算一次。

    未声明 `declare_indicators` 的策略在这里就返回 None —— 后面 `_context`
    连 `IndicatorBook` 都不构造，旧策略的每一根 bar 一行不变。
    """
    spec: IndicatorSpec | None = indicator_spec_of(strategy)
    if spec is None or not len(spec):
        return None
    return build_indicator_store(spec, prep.frames)


def _order_hooks_enabled(strategy: object) -> bool:
    """
    策略是否覆盖了任一 L5 下单钩子。

    与 `_exit_rules_of` 同理：引擎接受鸭子类型的策略桩，它们没有 `has_order_hooks`，
    一律视为「未覆盖」而不是让回测因缺方法崩掉。
    """
    getter = getattr(strategy, "has_order_hooks", None)
    return bool(getter()) if callable(getter) else False


def _align_timeline(
    bars_by_symbol: dict[str, list[Bar]]
) -> tuple[list[datetime], list[dict[str, Bar]]]:
    """
    主时间轴 = 各标的时间戳的并集（升序）；每个时点只放**当前有 bar** 的标的。

    同一标的同一时间戳出现多根 bar 时保留最后一根并告警 —— 静默丢弃重复数据
    会让回测结果无法解释。
    """
    indexed: dict[str, dict[datetime, Bar]] = {}
    for symbol, bars in bars_by_symbol.items():
        by_time: dict[datetime, Bar] = {}
        for bar in bars:
            if bar.time in by_time:
                logger.warning("%s 在 %s 有重复 bar，保留最后一根", symbol, bar.time)
            by_time[bar.time] = bar
        indexed[symbol] = by_time

    timeline = sorted({ts for by_time in indexed.values() for ts in by_time})
    bars_at = [
        {
            symbol: by_time[ts]
            for symbol, by_time in indexed.items()
            if ts in by_time
        }
        for ts in timeline
    ]
    return timeline, bars_at


def _credit_dividends(broker: PortfolioBroker, prep: _PreparedData, idx: int) -> float:
    """本时点各标的的除权分红入账合计（未开启复权时为零开销）。"""
    if not prep.dividend_cash:
        return 0.0
    total = 0.0
    for symbol, bar in prep.bars_at[idx].items():
        schedule = prep.dividend_cash.get(symbol)
        if schedule:
            total += _credit_dividend(broker, bar, schedule)
    return total


def _to_series(points: list[tuple[datetime, float]]) -> pd.Series:
    return pd.Series(
        [v for _, v in points],
        index=pd.DatetimeIndex([t for t, _ in points]),
        name="equity",
    )


def _equal_weight_buy_hold(prep: _PreparedData) -> float:
    """组合基准：各标的等权买入持有的算术平均收益。"""
    returns = []
    for bars in prep.bars_by_symbol.values():
        if bars[0].open > 1e-10:
            returns.append((bars[-1].close - bars[0].open) / bars[0].open)
    return sum(returns) / len(returns) if returns else 0.0


def _per_symbol_metrics(
    prep: _PreparedData, state: _RunState, initial_cash: float, days: int
) -> dict[str, BacktestMetrics]:
    """
    逐标的归因（**贡献度口径**）：各腿都以组合初始资金为基数，
    按日度 `net_pnl` 累加出「若只看这个标的、组合净值会怎么走」的曲线。

    不用「初始资金 / 标的数」作基数：集中型策略（如 50 选 5）单腿盈亏可以
    远超其 1/N 份额，那样算出来的 total_return 会跌破 -100%，
    年化收益的 `(1+r)**x` 直接变成 NaN，整张归因表作废。
    """
    seed = initial_cash
    dates = [d.date for d in state.daily_results]
    if len(dates) < MIN_TIMEPOINTS:
        return {}

    index = pd.DatetimeIndex(dates)
    result: dict[str, BacktestMetrics] = {}
    for symbol in prep.symbols:
        equity, running = [], seed
        for day in state.daily_results:
            contract = day.contracts.get(symbol)
            running += contract.net_pnl if contract else 0.0
            equity.append(running)
        bars = prep.bars_by_symbol[symbol]
        result[symbol] = compute_metrics(
            equity_curve=pd.Series(equity, index=index, name="equity"),
            fills=[f for f in state.fills if f["symbol"] == symbol],
            initial_cash=seed,
            bars_open=bars[0].open,
            bars_close=bars[-1].close,
            trading_days_per_year=days,
        )
    return result


def _portfolio_report_sections(
    prep: _PreparedData, state: _RunState, per_symbol: dict[str, BacktestMetrics]
) -> dict:
    """组合特有的 report 片段（单标的报告不含这些键）。"""
    marks = state.broker.mark_prices()
    daily = state.daily_results
    return {
        "symbols": prep.symbols,
        "final_cash": round(state.broker.cash, 2),
        "positions": state.broker.positions.snapshot(marks),
        "per_symbol_metrics": {s: metrics_to_dict(m) for s, m in per_symbol.items()},
        "daily_summary": {
            "days": len(daily),
            "total_turnover": round(sum(d.turnover for d in daily), 2),
            "total_commission": round(sum(d.commission for d in daily), 2),
            "total_net_pnl": round(sum(d.net_pnl for d in daily), 2),
            "trade_count": sum(d.trade_count for d in daily),
        },
    }


__all__ = [
    "PortfolioBacktestConfig",
    "PortfolioBacktestEngine",
    "PortfolioBacktestResult",
]
