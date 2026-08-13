"""
策略实时执行引擎

将策略从"回测态"升级为"实盘态"：
  数据源（DataFeed）→ 策略（StrategyBase.on_bar）→ OMS 下单 → 风控前置检查

特性:
  - 多策略实例并发（每个策略一个 asyncio.Task）
  - 事件驱动：每根新 K 线触发 on_bar 回调
  - 纸面交易模拟：启动时在最近 60 天历史数据上运行模拟，追踪 PnL/持仓/净值曲线
  - 风控前置：下单前调用 RiskEngine.pre_trade_check
  - 状态管理：RUNNING / STOPPED / ERROR
  - 错误隔离：单策略崩溃不影响其他策略

两条入口：
  - `start_strategy`           单标的、注册表里的传统策略，走 `on_bar`
  - `start_portfolio_strategy` 多标的组合策略（`FrameworkStrategy`），走 `on_bars`
    循环本身在 `app.strategy.live_runner`，上下文在 `app.strategy.live_context`
    —— 引擎只负责建档、装控制器、起 task（Wave L-d）

纸面模拟已搬到 `app.strategy.paper_sim`（含它的已知技术债说明）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

from app.data.models import Bar, Frequency, Market
from app.data.service import DataService
from app.engine.backtest.engine import _bars_to_df
from app.oms.manager import get_order_manager
from app.oms.order import LiveOrderSide, LiveOrderType
from app.risk.engine import get_risk_engine
from app.strategy.context import StrategyContext
from app.strategy.live_runner import LivePortfolioRunner, LiveStepReport
from app.strategy.paper_sim import (
    PAPER_INITIAL_CASH,
    PAPER_SIM_DAYS,
    PaperBroker,
    PaperPortfolio,
    PaperTrade,
    run_paper_simulation,
)
from app.strategy.presets import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from app.engine.controls.base import TradingControl
    from app.strategy.base import PortfolioStrategyBase

logger = logging.getLogger(__name__)

#: 纸面模拟已搬到 `app.strategy.paper_sim`，这里保留再导出以维持既有导入路径。
__all__ = [
    "PAPER_INITIAL_CASH",
    "PAPER_SIM_DAYS",
    "LiveOrderContext",
    "PaperBroker",
    "PaperPortfolio",
    "PaperTrade",
    "StrategyEngine",
    "StrategyInstance",
    "StrategyState",
    "get_strategy_engine",
    "run_paper_simulation",
]


class StrategyState(str, Enum):
    IDLE    = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR   = "error"



# ── StrategyInstance ──────────────────────────────────────────

@dataclass
class StrategyInstance:
    """运行中的策略实例元数据。"""
    instance_id: str
    strategy_name: str
    symbol: str
    market: str
    frequency: str
    params: dict
    state: StrategyState = StrategyState.IDLE
    task: asyncio.Task | None = field(default=None, repr=False)
    error: str | None = None
    bars_processed: int = 0
    orders_placed: int = 0
    started_at: str | None = None
    stopped_at: str | None = None
    # 纸面交易模拟结果
    paper: PaperPortfolio | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d: dict = {
            "instance_id": self.instance_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "market": self.market,
            "frequency": self.frequency,
            "params": self.params,
            "state": self.state.value,
            "error": self.error,
            "bars_processed": self.bars_processed,
            "orders_placed": self.orders_placed,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "paper": self.paper.to_dict() if self.paper else None,
        }
        return d


# ── 实盘下单上下文 ────────────────────────────────────────────

class LiveOrderContext:
    def __init__(self, instance: StrategyInstance, current_bar: Bar) -> None:
        self._instance = instance
        self._bar = current_bar
        self._pending_orders: list[dict] = []

    def buy(self, qty: int, order_type: str = "MARKET", limit_price: float | None = None) -> None:
        self._pending_orders.append({"side": "BUY", "qty": qty, "order_type": order_type, "limit_price": limit_price})

    def sell(self, qty: int, order_type: str = "MARKET", limit_price: float | None = None) -> None:
        self._pending_orders.append({"side": "SELL", "qty": qty, "order_type": order_type, "limit_price": limit_price})

    def pending_orders(self) -> list[dict]:
        return list(self._pending_orders)


# ── StrategyEngine ────────────────────────────────────────────

class StrategyEngine:
    """实盘策略引擎（单例）。"""

    _instance: StrategyEngine | None = None

    def __init__(self) -> None:
        self._instances: dict[str, StrategyInstance] = {}

    @classmethod
    def instance(cls) -> StrategyEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start_strategy(
        self,
        instance_id: str,
        strategy_name: str,
        symbol: str,
        market: str,
        frequency: str,
        params: dict,
        data_service: DataService,
        warmup_days: int = 120,
        sim_days: int = PAPER_SIM_DAYS,
    ) -> StrategyInstance:
        """
        启动策略实例。

        流程：
        1. 验证策略名称
        2. 加载 warmup_days 天历史数据
        3. 在最近 PAPER_SIM_DAYS 天运行纸面交易模拟（立即可见结果）
        4. 启动实时 K 线循环（等待新数据推送）
        """
        if instance_id in self._instances:
            inst = self._instances[instance_id]
            if inst.state == StrategyState.RUNNING:
                raise ValueError(f"Strategy instance '{instance_id}' is already running")

        if strategy_name not in STRATEGY_REGISTRY:
            raise ValueError(
                f"Unknown strategy '{strategy_name}'. "
                f"Available: {list(STRATEGY_REGISTRY.keys())}"
            )

        try:
            market_enum = Market(market.upper())
            freq_enum = Frequency(frequency)
        except ValueError as e:
            raise ValueError(str(e)) from e

        end = date.today()
        start = end - timedelta(days=max(warmup_days, sim_days + 60))
        bars = await data_service.get_bars(
            symbol=symbol, market=market_enum, frequency=freq_enum, start=start, end=end,
        )

        strategy_cls = STRATEGY_REGISTRY[strategy_name]

        inst = StrategyInstance(
            instance_id=instance_id,
            strategy_name=strategy_name,
            symbol=symbol,
            market=market,
            frequency=frequency,
            params=params,
            state=StrategyState.RUNNING,
            started_at=datetime.now(UTC).isoformat(),
        )
        self._instances[instance_id] = inst

        # ── 纸面交易模拟（立即运行） ───────────────────────────
        if bars:
            try:
                inst.paper = run_paper_simulation(
                    strategy_cls=strategy_cls,
                    params=params,
                    all_bars=bars,
                    sim_days=sim_days,
                )
                logger.info(
                    "Paper simulation done: %s — %d trades, return=%.1f%%",
                    instance_id,
                    inst.paper.total_trades,
                    inst.paper.total_return_pct,
                )
            except Exception:
                logger.exception("Paper simulation failed for %s", instance_id)

        # ── 实时循环（等待真实推送） ────────────────────────────
        strategy_obj = strategy_cls(params=params)
        if bars:
            full_df = _bars_to_df(bars)
            init_ctx = StrategyContext(bar=bars[-1], history=full_df, broker=None)
            try:
                strategy_obj.on_start(init_ctx)
            except Exception:
                logger.exception("Strategy on_start failed: %s", instance_id)

        task = asyncio.create_task(
            self._run_loop(inst, strategy_obj, market_enum, freq_enum, data_service, bars),
            name=f"strategy:{instance_id}",
        )
        inst.task = task

        logger.info(
            "Strategy started: %s (%s %s %s) — %d warmup bars",
            instance_id, strategy_name, symbol, frequency, len(bars),
        )
        return inst

    # ── 组合策略实盘入口（Wave L-d）────────────────────────────

    async def start_portfolio_strategy(
        self,
        instance_id: str,
        strategy: PortfolioStrategyBase,
        symbols: Sequence[str],
        market: str,
        frequency: str,
        data_service: DataService,
        warmup_days: int = 120,
        controls: Sequence[TradingControl] | None = None,
        cash_per_position: float | None = None,
    ) -> StrategyInstance:
        """
        启动一个**多标的组合策略**实例（`FrameworkStrategy` 走这条路）。

        与 `start_strategy` 的差别：接收的是已装配好的策略对象而不是注册表里的
        名字（框架策略由四个模型组装而成，没法用一个名字表达），回调是
        `on_bars` 而不是 `on_bar`，且**不跑纸面模拟** —— 那套简化实现与真实
        回测不可比（见 `PaperBroker` 的技术债说明），组合策略应直接跑回测引擎。

        `controls` 会**同时**装到 OMS 上（契约 §5.1）：同一份配置，
        回测拒掉的单实盘也拒。
        """
        self._ensure_not_running(instance_id)
        wanted, market_enum, freq_enum = self._resolve_target(symbols, market, frequency)
        warmup = await self._load_warmup(
            wanted, market_enum, freq_enum, data_service, warmup_days
        )
        self._install_controls(controls)

        inst = self._register_portfolio_instance(
            instance_id, strategy, wanted, market, frequency
        )
        runner = LivePortfolioRunner(
            instance_id=instance_id,
            strategy=strategy,
            symbols=wanted,
            market=market_enum,
            frequency=freq_enum,
            data_service=data_service,
            warmup_bars=warmup,
            oms_provider=get_order_manager,
            cash_per_position=cash_per_position,
            on_step=lambda report: self._record_step(inst, report),
            on_fatal=lambda message: self._record_fatal(inst, message),
        )
        inst.task = asyncio.create_task(
            runner.run(), name=f"portfolio-strategy:{instance_id}"
        )
        logger.info(
            "Portfolio strategy started: %s (%s) — %d symbols",
            instance_id, inst.strategy_name, len(wanted),
        )
        return inst

    @staticmethod
    def _resolve_target(
        symbols: Sequence[str], market: str, frequency: str
    ) -> tuple[list[str], Market, Frequency]:
        """标的去空白 + 市场/频率枚举化。非法输入直接抛错，不静默降级。"""
        wanted = [s.strip().upper() for s in symbols if s and s.strip()]
        if not wanted:
            raise ValueError("组合策略至少需要一个标的")
        try:
            return wanted, Market(market.upper()), Frequency(frequency)
        except ValueError as e:
            raise ValueError(str(e)) from e

    def _register_portfolio_instance(
        self,
        instance_id: str,
        strategy: PortfolioStrategyBase,
        symbols: Sequence[str],
        market: str,
        frequency: str,
    ) -> StrategyInstance:
        """建档并登记。多标的挤进单标的的 `symbol` 字段，以逗号分隔（不改 API 契约）。"""
        inst = StrategyInstance(
            instance_id=instance_id,
            strategy_name=getattr(strategy, "name", type(strategy).__name__),
            symbol=",".join(symbols),
            market=market,
            frequency=frequency,
            params=dict(getattr(strategy, "_params", {}) or {}),
            state=StrategyState.RUNNING,
            started_at=datetime.now(UTC).isoformat(),
        )
        self._instances[instance_id] = inst
        return inst

    def _ensure_not_running(self, instance_id: str) -> None:
        inst = self._instances.get(instance_id)
        if inst is not None and inst.state == StrategyState.RUNNING:
            raise ValueError(f"Strategy instance '{instance_id}' is already running")

    @staticmethod
    async def _load_warmup(
        symbols: Sequence[str],
        market: Market,
        frequency: Frequency,
        data_service: DataService,
        warmup_days: int,
    ) -> dict[str, list[Bar]]:
        """逐标的取预热历史。单个标的取不到不阻断整体，但必须留痕。"""
        end = date.today()
        start = end - timedelta(days=warmup_days)
        warmup: dict[str, list[Bar]] = {}
        for symbol in symbols:
            try:
                warmup[symbol] = await data_service.get_bars(
                    symbol=symbol, market=market, frequency=frequency,
                    start=start, end=end,
                )
            except Exception:
                logger.exception("预热数据加载失败，%s 以空历史启动", symbol)
                warmup[symbol] = []
        return warmup

    @staticmethod
    def _install_controls(controls: Sequence[TradingControl] | None) -> None:
        """把同一批控制器装到 OMS 上（回测侧由 BacktestConfig.controls 装）。"""
        if not controls:
            return
        try:
            get_order_manager().set_controls(controls)
        except RuntimeError:
            logger.warning("OMS 尚未初始化，交易控制器未能装载")

    @staticmethod
    def _record_step(inst: StrategyInstance, report: LiveStepReport) -> None:
        inst.bars_processed += len(report.symbols)
        inst.orders_placed += report.orders_submitted

    @staticmethod
    def _record_fatal(inst: StrategyInstance, message: str) -> None:
        inst.state = StrategyState.ERROR
        inst.error = message
        logger.error("Strategy instance %s marked ERROR: %s", inst.instance_id, message)

    async def stop_strategy(self, instance_id: str) -> StrategyInstance:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ValueError(f"Strategy instance '{instance_id}' not found")
        if inst.task and not inst.task.done():
            inst.task.cancel()
            try:
                await inst.task
            except asyncio.CancelledError:
                pass
        inst.state = StrategyState.STOPPED
        inst.stopped_at = datetime.now(UTC).isoformat()
        inst.task = None
        return inst

    def list_instances(self) -> list[dict]:
        return [inst.to_dict() for inst in self._instances.values()]

    def get_instance(self, instance_id: str) -> StrategyInstance | None:
        return self._instances.get(instance_id)

    # ── 实时 K 线循环 ─────────────────────────────────────────

    async def _run_loop(
        self,
        inst: StrategyInstance,
        strategy_obj,
        market: Market,
        frequency: Frequency,
        data_service: DataService,
        warmup_bars: list[Bar],
    ) -> None:
        history = list(warmup_bars)
        history_df = _bars_to_df(history) if history else pd.DataFrame()

        try:
            async for bar in data_service.subscribe_bars([inst.symbol], market, frequency):
                if bar.symbol.upper() != inst.symbol.upper():
                    continue
                history.append(bar)
                history_df = _bars_to_df(history)
                order_ctx = LiveOrderContext(inst, bar)
                ctx = StrategyContext(bar=bar, history=history_df, broker=None, live_order_ctx=order_ctx)
                try:
                    strategy_obj.on_bar(ctx)
                except Exception:
                    logger.exception("Strategy on_bar error: %s", inst.instance_id)
                inst.bars_processed += 1
                for order_req in order_ctx.pending_orders():
                    await self._submit_live_order(inst, bar, order_req)
        except asyncio.CancelledError:
            logger.info("Strategy loop cancelled: %s", inst.instance_id)
        except Exception as e:
            inst.state = StrategyState.ERROR
            inst.error = str(e)
            logger.exception("Strategy loop error: %s", inst.instance_id)

    async def _submit_live_order(self, inst: StrategyInstance, bar: Bar, order_req: dict) -> None:
        try:
            oms = get_order_manager()
        except RuntimeError:
            logger.warning("OMS not initialized, order dropped for %s", inst.instance_id)
            return

        symbol = inst.symbol
        market = inst.market
        qty = order_req["qty"]
        side_str = order_req["side"]
        price = bar.close

        risk = get_risk_engine()
        try:
            account = await oms.get_account(market)
            portfolio_value = account.get("portfolio_value", 0)
            positions = await oms.get_positions(market)
            current_mv = next(
                (p.get("market_value", 0) for p in positions if p.get("symbol") == symbol), 0.0,
            )
        except Exception:
            portfolio_value = 0
            current_mv = 0.0

        violations = risk.pre_trade_check(
            symbol=symbol, market=market, side=side_str, qty=qty, price=price,
            portfolio_value=portfolio_value, current_symbol_value=current_mv,
        )
        from app.risk.models import ViolationSeverity
        if any(v.severity == ViolationSeverity.BLOCK for v in violations):
            return

        try:
            await oms.submit_order(
                symbol=symbol, market=market,
                side=LiveOrderSide(side_str),
                qty=qty,
                order_type=LiveOrderType(order_req.get("order_type", "MARKET")),
                limit_price=order_req.get("limit_price"),
                strategy_id=inst.instance_id,
            )
            inst.orders_placed += 1
            risk.on_order_submitted()
        except Exception:
            logger.exception("Failed to submit live order for strategy %s", inst.instance_id)


def get_strategy_engine() -> StrategyEngine:
    return StrategyEngine.instance()
