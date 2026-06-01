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
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import pandas as pd

from app.data.models import Bar, Frequency, Market
from app.data.service import DataService
from app.engine.backtest.engine import _bars_to_df
from app.oms.manager import get_order_manager
from app.oms.order import LiveOrderSide, LiveOrderType
from app.risk.engine import get_risk_engine
from app.strategy.context import StrategyContext
from app.strategy.presets import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

PAPER_SIM_DAYS = 60          # 模拟最近 N 天历史数据
PAPER_INITIAL_CASH = 100_000.0


class StrategyState(str, Enum):
    IDLE    = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR   = "error"


# ── 纸面交易数据结构 ───────────────────────────────────────────

@dataclass
class PaperTrade:
    timestamp: str
    side: str          # BUY / SELL
    price: float
    qty: int
    value: float       # price * qty
    realized_pnl: float = 0.0
    signal_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "side": self.side,
            "price": self.price,
            "qty": self.qty,
            "value": self.value,
            "realized_pnl": self.realized_pnl,
            "signal_reason": self.signal_reason,
        }


@dataclass
class PaperPortfolio:
    """纸面交易组合状态。"""
    initial_cash: float = PAPER_INITIAL_CASH
    cash: float = PAPER_INITIAL_CASH
    position: int = 0
    avg_cost: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)   # [{time, value, pnl_pct}]
    trades: list[PaperTrade] = field(default_factory=list)

    # 汇总指标（模拟完成后计算）
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    buy_hold_return_pct: float = 0.0
    sim_start: str = ""
    sim_end: str = ""
    sim_days: int = PAPER_SIM_DAYS   # 本次模拟使用的天数

    def current_equity(self, price: float) -> float:
        return self.cash + self.position * price

    def buy(self, price: float, qty: int, timestamp: str) -> None:
        cost = price * qty
        if cost > self.cash:
            qty = int(self.cash / price)
            cost = price * qty
        if qty <= 0:
            return
        # 更新平均成本
        total_qty = self.position + qty
        self.avg_cost = (self.avg_cost * self.position + price * qty) / total_qty if total_qty else price
        self.cash -= cost
        self.position += qty
        self.trades.append(PaperTrade(
            timestamp=timestamp, side="BUY", price=price, qty=qty,
            value=round(cost, 2), signal_reason="策略买入信号",
        ))

    def sell(self, price: float, qty: int, timestamp: str) -> None:
        qty = min(qty, self.position)
        if qty <= 0:
            return
        realized_pnl = (price - self.avg_cost) * qty
        self.cash += price * qty
        self.position -= qty
        if self.position == 0:
            self.avg_cost = 0.0
        self.trades.append(PaperTrade(
            timestamp=timestamp, side="SELL", price=price, qty=qty,
            value=round(price * qty, 2), realized_pnl=round(realized_pnl, 2),
            signal_reason="策略卖出信号",
        ))

    def sell_all(self, price: float, timestamp: str) -> None:
        self.sell(price, self.position, timestamp)

    def snapshot(self, timestamp: str, price: float) -> None:
        equity = self.current_equity(price)
        pnl_pct = (equity - self.initial_cash) / self.initial_cash * 100
        self.equity_curve.append({
            "time": timestamp,
            "value": round(equity, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    def compute_metrics(self, initial_price: float, final_price: float) -> None:
        """计算汇总绩效指标。"""
        curve = self.equity_curve
        if not curve:
            return

        values = [p["value"] for p in curve]
        final_equity = values[-1]
        self.total_return_pct = round((final_equity - self.initial_cash) / self.initial_cash * 100, 2)
        self.buy_hold_return_pct = round((final_price - initial_price) / initial_price * 100, 2)
        self.total_trades = len(self.trades)

        # Sharpe（简化日收益率）
        if len(values) > 1:
            rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
            mean_r = sum(rets) / len(rets)
            std_r = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
            self.sharpe_ratio = round((mean_r / std_r * (252 ** 0.5)) if std_r > 1e-10 else 0.0, 3)

        # 最大回撤
        peak = self.initial_cash
        max_dd = 0.0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
        self.max_drawdown_pct = round(-max_dd * 100, 2)

        # 胜率 & 盈亏比
        sell_trades = [t for t in self.trades if t.side == "SELL"]
        if sell_trades:
            wins = [t for t in sell_trades if t.realized_pnl > 0]
            losses = [t for t in sell_trades if t.realized_pnl <= 0]
            self.win_rate_pct = round(len(wins) / len(sell_trades) * 100, 1)
            total_win = sum(t.realized_pnl for t in wins)
            total_loss = abs(sum(t.realized_pnl for t in losses))
            self.profit_factor = round(total_win / total_loss, 2) if total_loss > 1e-6 else (
                99.0 if total_win > 0 else 0.0
            )

    def to_dict(self) -> dict:
        return {
            "initial_cash": self.initial_cash,
            "cash": round(self.cash, 2),
            "position": self.position,
            "avg_cost": round(self.avg_cost, 2),
            "equity_curve": self.equity_curve,
            "trades": [t.to_dict() for t in self.trades],
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "buy_hold_return_pct": self.buy_hold_return_pct,
            "sim_start": self.sim_start,
            "sim_end": self.sim_end,
            "sim_days": self.sim_days,
        }


# ── 纸面交易代理 Broker（供 StrategyContext 使用） ─────────────────

class _PaperPosition:
    """SimulatedBroker.positions.get() 返回的 Position 兼容对象。"""

    def __init__(self, qty: int, avg_cost: float) -> None:
        self.qty = qty
        self.avg_cost = avg_cost
        self.market_value = 0.0  # 兼容字段


class _PaperPositions:
    """StrategyContext 要求 broker.positions.get(sym) → Position-like."""

    def __init__(self, portfolio: "PaperPortfolio") -> None:
        self._p = portfolio

    def get(self, symbol: str) -> Optional[_PaperPosition]:
        if self._p.position <= 0:
            return None
        return _PaperPosition(qty=self._p.position, avg_cost=self._p.avg_cost)


class PaperBroker:
    """
    SimulatedBroker 接口兼容的纸面 Broker。

    StrategyContext 期望：
      - broker.cash          → float
      - broker.positions.get(sym) → Position-like with .qty
      - broker.portfolio_value(prices) → float
      - broker.buy(symbol, qty, market=None)
      - broker.sell(symbol, qty, market=None)
    """

    def __init__(self, portfolio: PaperPortfolio) -> None:
        self._p = portfolio
        self._current_bar: Optional[Bar] = None
        self.positions = _PaperPositions(portfolio)

    def set_bar(self, bar: Bar) -> None:
        self._current_bar = bar

    @property
    def cash(self) -> float:
        return self._p.cash

    def portfolio_value(self, prices: dict) -> float:
        if self._current_bar:
            price = prices.get(self._current_bar.symbol, self._current_bar.close)
        else:
            price = 0.0
        return self._p.cash + self._p.position * price

    def _ts(self) -> str:
        if self._current_bar:
            t = self._current_bar.time
            return t.isoformat() if hasattr(t, "isoformat") else str(t)
        return ""

    def buy(self, symbol: str, qty: int, market=None) -> None:  # noqa: ARG002
        if self._current_bar and qty > 0:
            self._p.buy(self._current_bar.close, qty, self._ts())

    def sell(self, symbol: str, qty: int, market=None) -> None:  # noqa: ARG002
        if self._current_bar and qty > 0:
            self._p.sell(self._current_bar.close, qty, self._ts())


# ── 纸面交易模拟函数 ───────────────────────────────────────────

def run_paper_simulation(
    strategy_cls,
    params: dict,
    all_bars: list[Bar],
    sim_days: int = PAPER_SIM_DAYS,
) -> PaperPortfolio:
    """
    在最近 sim_days 天的历史数据上运行策略模拟。

    前面的数据用作指标预热，最后 sim_days 天计入 PnL 和净值曲线。
    返回填充好的 PaperPortfolio。
    """
    if not all_bars:
        return PaperPortfolio()

    portfolio = PaperPortfolio()
    broker = PaperBroker(portfolio)

    strategy_obj = strategy_cls(params=params)

    # 确定回测窗口：最后 sim_days 天
    last_bar_time = all_bars[-1].time
    sim_cutoff = last_bar_time.date() - timedelta(days=sim_days) if hasattr(last_bar_time, "date") else date.today() - timedelta(days=sim_days)
    sim_bars = [b for b in all_bars if (b.time.date() if hasattr(b.time, "date") else b.time) >= sim_cutoff]

    if not sim_bars:
        sim_bars = all_bars[-min(30, len(all_bars)):]

    portfolio.sim_start = str(sim_bars[0].time)[:10]
    portfolio.sim_end   = str(sim_bars[-1].time)[:10]
    portfolio.sim_days  = sim_days

    # 初始化策略（用全部历史做 on_start）
    full_df = _bars_to_df(all_bars)
    init_ctx = StrategyContext(bar=all_bars[-1], history=full_df, broker=None)
    try:
        strategy_obj.on_start(init_ctx)
    except Exception:
        pass

    initial_price = sim_bars[0].close

    # 逐 bar 运行策略
    history = list(all_bars)
    sim_start_idx = len(all_bars) - len(sim_bars)

    for i, bar in enumerate(sim_bars):
        global_idx = sim_start_idx + i
        history_slice = all_bars[:global_idx + 1]
        history_df = _bars_to_df(history_slice)

        broker.set_bar(bar)
        ctx = StrategyContext(
            bar=bar,
            history=history_df,
            broker=broker,    # 使用纸面 broker
        )
        try:
            strategy_obj.on_bar(ctx)
        except Exception as e:
            logger.debug("Paper sim on_bar error: %s", e)

        ts = str(bar.time)[:10]
        portfolio.snapshot(ts, bar.close)

    # 期末平仓（记录未实现盈亏）
    if portfolio.position > 0 and sim_bars:
        final_price = sim_bars[-1].close
        ts = str(sim_bars[-1].time)[:10]
        portfolio.sell_all(final_price, ts)

    portfolio.compute_metrics(initial_price, sim_bars[-1].close if sim_bars else initial_price)
    return portfolio


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
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    error: Optional[str] = None
    bars_processed: int = 0
    orders_placed: int = 0
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    # 纸面交易模拟结果
    paper: Optional[PaperPortfolio] = field(default=None, repr=False)

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

    def buy(self, qty: int, order_type: str = "MARKET", limit_price: Optional[float] = None) -> None:
        self._pending_orders.append({"side": "BUY", "qty": qty, "order_type": order_type, "limit_price": limit_price})

    def sell(self, qty: int, order_type: str = "MARKET", limit_price: Optional[float] = None) -> None:
        self._pending_orders.append({"side": "SELL", "qty": qty, "order_type": order_type, "limit_price": limit_price})

    def pending_orders(self) -> list[dict]:
        return list(self._pending_orders)


# ── StrategyEngine ────────────────────────────────────────────

class StrategyEngine:
    """实盘策略引擎（单例）。"""

    _instance: Optional["StrategyEngine"] = None

    def __init__(self) -> None:
        self._instances: dict[str, StrategyInstance] = {}

    @classmethod
    def instance(cls) -> "StrategyEngine":
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
            started_at=datetime.now(timezone.utc).isoformat(),
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
        inst.stopped_at = datetime.now(timezone.utc).isoformat()
        inst.task = None
        return inst

    def list_instances(self) -> list[dict]:
        return [inst.to_dict() for inst in self._instances.values()]

    def get_instance(self, instance_id: str) -> Optional[StrategyInstance]:
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
