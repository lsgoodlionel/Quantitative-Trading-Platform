"""
策略实时执行引擎

将策略从"回测态"升级为"实盘态"：
  数据源（DataFeed）→ 策略（StrategyBase.on_bar）→ OMS 下单 → 风控前置检查

设计参考:
  refs/vnpy/vnpy/trader/engine.py MainEngine + CtaEngine
  refs/freqtrade/freqtrade/worker.py Worker loop

特性:
  - 多策略实例并发（每个策略一个 asyncio.Task）
  - 事件驱动：每根新 K 线触发 on_bar 回调
  - 风控前置：下单前调用 RiskEngine.pre_trade_check
  - 状态管理：RUNNING / STOPPED / ERROR
  - 错误隔离：单策略崩溃不影响其他策略
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from app.data.models import Bar, Frequency, Market
from app.data.service import DataService
from app.engine.events.types import EventType
from app.oms.manager import get_order_manager
from app.oms.order import LiveOrderSide, LiveOrderType
from app.risk.engine import get_risk_engine
from app.strategy.context import StrategyContext
from app.strategy.presets import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


class StrategyState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


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
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    error: Optional[str] = None
    bars_processed: int = 0
    orders_placed: int = 0
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
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
        }


class LiveOrderContext:
    """
    实盘策略下单上下文。

    策略通过此上下文的 buy/sell/sell_all 发出信号，
    引擎负责经过风控后路由到 OMS。
    """

    def __init__(
        self,
        instance: StrategyInstance,
        current_bar: Bar,
    ) -> None:
        self._instance = instance
        self._bar = current_bar
        self._pending_orders: list[dict] = []

    def buy(self, qty: int, order_type: str = "MARKET", limit_price: Optional[float] = None) -> None:
        self._pending_orders.append({
            "side": "BUY",
            "qty": qty,
            "order_type": order_type,
            "limit_price": limit_price,
        })

    def sell(self, qty: int, order_type: str = "MARKET", limit_price: Optional[float] = None) -> None:
        self._pending_orders.append({
            "side": "SELL",
            "qty": qty,
            "order_type": order_type,
            "limit_price": limit_price,
        })

    def pending_orders(self) -> list[dict]:
        return list(self._pending_orders)


class StrategyEngine:
    """
    实盘策略引擎（单例）。

    管理多个策略实例的生命周期：
    - 启动：加载历史数据预热 → 订阅实时 K 线 → 循环调用 on_bar
    - 停止：取消 asyncio.Task，更新状态
    - 健康检查：定期汇报各策略状态
    """

    _instance: Optional["StrategyEngine"] = None

    def __init__(self) -> None:
        self._instances: dict[str, StrategyInstance] = {}

    @classmethod
    def instance(cls) -> "StrategyEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 策略管理 API ──────────────────────────────────────────

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
    ) -> StrategyInstance:
        """
        启动一个策略实例。

        流程:
        1. 验证策略名称
        2. 加载 warmup_days 天历史数据（用于指标预热）
        3. 创建 asyncio.Task 驱动策略循环
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

        # 加载预热历史数据
        end = date.today()
        start = end - timedelta(days=warmup_days)
        bars = await data_service.get_bars(
            symbol=symbol, market=market_enum,
            frequency=freq_enum, start=start, end=end,
        )

        strategy_cls = STRATEGY_REGISTRY[strategy_name]
        strategy_obj = strategy_cls(params=params)

        inst = StrategyInstance(
            instance_id=instance_id,
            strategy_name=strategy_name,
            symbol=symbol,
            market=market,
            frequency=frequency,
            params=params,
            state=StrategyState.RUNNING,
        )
        from datetime import datetime, timezone
        inst.started_at = datetime.now(timezone.utc).isoformat()
        self._instances[instance_id] = inst

        # 调用 on_start 进行策略初始化
        if bars:
            import pandas as pd
            from app.engine.backtest.engine import _bars_to_df
            history_df = _bars_to_df(bars)
            init_ctx = StrategyContext(bar=bars[-1], history=history_df, broker=None)
            try:
                strategy_obj.on_start(init_ctx)
            except Exception:
                logger.exception("Strategy on_start failed: %s", instance_id)

        # 启动实时 K 线驱动循环
        task = asyncio.create_task(
            self._run_loop(
                inst, strategy_obj, market_enum, freq_enum, data_service, bars
            ),
            name=f"strategy:{instance_id}",
        )
        inst.task = task

        logger.info(
            "Strategy started: %s (%s %s %s) — %d warmup bars loaded",
            instance_id, strategy_name, symbol, frequency, len(bars),
        )
        return inst

    async def stop_strategy(self, instance_id: str) -> StrategyInstance:
        """停止指定策略实例。"""
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ValueError(f"Strategy instance '{instance_id}' not found")

        if inst.task and not inst.task.done():
            inst.task.cancel()
            try:
                await inst.task
            except asyncio.CancelledError:
                pass

        from datetime import datetime, timezone
        inst.state = StrategyState.STOPPED
        inst.stopped_at = datetime.now(timezone.utc).isoformat()
        inst.task = None
        logger.info("Strategy stopped: %s", instance_id)
        return inst

    def list_instances(self) -> list[dict]:
        return [inst.to_dict() for inst in self._instances.values()]

    def get_instance(self, instance_id: str) -> Optional[StrategyInstance]:
        return self._instances.get(instance_id)

    # ── 实时循环 ──────────────────────────────────────────────

    async def _run_loop(
        self,
        inst: StrategyInstance,
        strategy_obj,
        market: Market,
        frequency: Frequency,
        data_service: DataService,
        warmup_bars: list[Bar],
    ) -> None:
        """
        实时 K 线驱动循环。

        每当订阅到新 K 线时，构建 StrategyContext 并调用 on_bar。
        策略通过 ctx.buy/sell 发出信号，本方法负责经风控后路由到 OMS。
        """
        import pandas as pd
        from app.engine.backtest.engine import _bars_to_df

        history = list(warmup_bars)
        history_df = _bars_to_df(history) if history else pd.DataFrame()

        try:
            async for bar in data_service.subscribe_bars(
                [inst.symbol], market, frequency
            ):
                if bar.symbol.upper() != inst.symbol.upper():
                    continue

                # 更新历史 DataFrame
                history.append(bar)
                history_df = _bars_to_df(history)

                # 构建执行上下文
                order_ctx = LiveOrderContext(inst, bar)
                ctx = StrategyContext(
                    bar=bar,
                    history=history_df,
                    broker=None,
                    live_order_ctx=order_ctx,
                )

                try:
                    strategy_obj.on_bar(ctx)
                except Exception:
                    logger.exception(
                        "Strategy on_bar error: instance=%s", inst.instance_id
                    )

                inst.bars_processed += 1

                # 提交待发订单
                for order_req in order_ctx.pending_orders():
                    await self._submit_live_order(inst, bar, order_req)

        except asyncio.CancelledError:
            logger.info("Strategy loop cancelled: %s", inst.instance_id)
        except Exception as e:
            inst.state = StrategyState.ERROR
            inst.error = str(e)
            logger.exception("Strategy loop error: %s", inst.instance_id)

    async def _submit_live_order(
        self,
        inst: StrategyInstance,
        bar: Bar,
        order_req: dict,
    ) -> None:
        """经过风控后将策略信号提交到 OMS。"""
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

        # 风控前置检查
        risk = get_risk_engine()
        try:
            account = await oms.get_account(market)
            portfolio_value = account.get("portfolio_value", 0)
            positions = await oms.get_positions(market)
            current_mv = next(
                (p.get("market_value", 0) for p in positions if p.get("symbol") == symbol),
                0.0,
            )
        except Exception:
            portfolio_value = 0
            current_mv = 0.0

        violations = risk.pre_trade_check(
            symbol=symbol,
            market=market,
            side=side_str,
            qty=qty,
            price=price,
            portfolio_value=portfolio_value,
            current_symbol_value=current_mv,
        )

        from app.risk.models import ViolationSeverity
        blocks = [v for v in violations if v.severity == ViolationSeverity.BLOCK]
        if blocks:
            logger.warning(
                "Order blocked by risk engine: %s — %s",
                inst.instance_id,
                [v.message for v in blocks],
            )
            return

        try:
            side = LiveOrderSide(side_str)
            order_type = LiveOrderType(order_req.get("order_type", "MARKET"))
            limit_price = order_req.get("limit_price")

            await oms.submit_order(
                symbol=symbol,
                market=market,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                strategy_id=inst.instance_id,
            )
            inst.orders_placed += 1
            risk.on_order_submitted()
            logger.info(
                "Live order submitted: %s %s %d @ %s (strategy=%s)",
                side_str, symbol, qty,
                limit_price or "MKT", inst.instance_id,
            )
        except Exception:
            logger.exception(
                "Failed to submit live order for strategy %s", inst.instance_id
            )


# ── 全局访问 ─────────────────────────────────────────────────

def get_strategy_engine() -> StrategyEngine:
    return StrategyEngine.instance()
