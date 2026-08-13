"""实盘多标的循环 —— 把组合策略接到行情与 OMS 上（Wave L-d / §三、§五）

一个时点的完整节拍：

    bar 到齐（或聚合窗口超时）
      → ★ await 拉账户快照     失败则**跳过本时点**，绝不用 0 顶上
      → strategy.on_bars(ctx)  同步，与回测同一份代码
      → ★ await 冲刷订单       统一走 OrderManager.submit_order

两条必须守住的红线：

1. **不无限等 bar**。某标的停牌或数据源掉线会让整个策略永久卡住，
   所以桶不齐时也要在窗口到期后用已有的 bar 触发，缺失标的沿用最后已知价。
2. **不静默用 0 净值**。账户拉取失败在实盘是常态（限流、断连），
   而 `portfolio_value=0` 会让 `target_weight` 把全部目标算成 0 股 = 全仓清空。

本模块**不 import `app.strategy.engine`**：engine 单向 import 本模块，
实例状态的变更通过 `on_fatal` / `on_step` 回调回传，避免把导入环重新引进来。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide
from app.engine.backtest.engine import _bars_to_df
from app.engine.backtest.order_types import OrderType
from app.oms.manager import get_order_manager
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderType
from app.risk.engine import get_risk_engine
from app.risk.models import ViolationSeverity
from app.strategy.live_context import AccountSnapshot, LivePortfolioContext

logger = logging.getLogger(__name__)

#: 同一时点的 bar 聚合等待窗口（秒）。超时后用已收到的 bar 触发一次 on_bars，
#: 缺失标的按「停牌」处理（复用回测的最后已知价估值语义）。
BAR_AGGREGATION_WINDOW_SECONDS = 5.0

#: 连续多少次账户快照失败后把实例判为 ERROR。单次失败只跳过本时点、下一时点重试。
MAX_CONSECUTIVE_SNAPSHOT_FAILURES = 3

#: 本地订单类型 → 实盘订单类型。实盘网关只认市价/限价，其余类型必须显式报错。
_LIVE_ORDER_TYPES: dict[OrderType, LiveOrderType] = {
    OrderType.MARKET: LiveOrderType.MARKET,
    OrderType.MARKET_ON_OPEN: LiveOrderType.MARKET,
    OrderType.MARKET_ON_CLOSE: LiveOrderType.MARKET,
    OrderType.LIMIT: LiveOrderType.LIMIT,
}

#: 分钟级频率的秒数。日线以上单独处理（要落到自然日/自然周边界）。
_INTRADAY_SECONDS: dict[Frequency, int] = {
    Frequency.MIN_1: 60,
    Frequency.MIN_5: 300,
    Frequency.MIN_15: 900,
    Frequency.MIN_30: 1800,
    Frequency.HOUR_1: 3600,
    Frequency.HOUR_4: 14400,
}

_STREAM_END = object()

__all__ = [
    "BAR_AGGREGATION_WINDOW_SECONDS",
    "MAX_CONSECUTIVE_SNAPSHOT_FAILURES",
    "AccountSnapshotError",
    "BarAggregator",
    "FlushedOrder",
    "LivePortfolioRunner",
    "LiveStepReport",
    "fetch_account_snapshot",
    "floor_bar_time",
    "flush_orders",
]


class AccountSnapshotError(RuntimeError):
    """账户/持仓快照不可用。调用方必须跳过本时点，**不得**退化成零净值。"""


class _LiveRunAbortedError(RuntimeError):
    """实盘循环需要终止（例如连续快照失败超限）。"""


# ── bar 聚合 ──────────────────────────────────────────────────


def floor_bar_time(time: datetime, frequency: Frequency) -> datetime:
    """把 bar 时间归一到频率边界，作为「同一时点」的桶键。"""
    step = _INTRADAY_SECONDS.get(frequency)
    midnight = time.replace(hour=0, minute=0, second=0, microsecond=0)
    if step is not None:
        elapsed = int((time - midnight).total_seconds())
        return midnight + timedelta(seconds=elapsed - elapsed % step)
    if frequency is Frequency.WEEK_1:
        return midnight - timedelta(days=midnight.weekday())
    return midnight


class BarAggregator:
    """
    把异步到达的多标的 bar 汇成「一个时点一桶」。

    规则（契约 §三）：
    1. 收到某标的 bar → 记入当前时点桶（按 `bar.time` 归一到频率边界）；
    2. 桶内标的集合 == 订阅集合 → **立即**触发；
    3. 否则等到窗口超时 → 用已有的触发，缺失标的沿用最后已知价。
    """

    def __init__(
        self,
        symbols: Iterable[str],
        frequency: Frequency,
        window_seconds: float = BAR_AGGREGATION_WINDOW_SECONDS,
    ) -> None:
        self._expected = frozenset(symbols)
        self._frequency = frequency
        self._window = float(window_seconds)
        self._bars: dict[str, Bar] = {}
        self._key: datetime | None = None
        self._opened_at: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self._bars

    @property
    def is_complete(self) -> bool:
        return bool(self._bars) and self._expected.issubset(self._bars)

    def add(self, bar: Bar, now: float) -> Bar | None:
        """
        收下一根 bar。

        返回值不为 None 表示这根 bar 属于**下一个**时点：调用方必须先冲刷
        当前桶，再把它重新 `add` 进来（否则新旧时点会被搅在一起）。
        """
        key = floor_bar_time(bar.time, self._frequency)
        if self._bars and key != self._key:
            return bar
        if not self._bars:
            self._key = key
            self._opened_at = now
        self._bars[bar.symbol] = bar
        return None

    def remaining(self, now: float) -> float | None:
        """距窗口到期还有多少秒。桶为空时返回 None（此时没有任何截止时间）。"""
        if not self._bars:
            return None
        return max(0.0, self._opened_at + self._window - now)

    def flush(self) -> tuple[datetime, dict[str, Bar]] | None:
        """取出并清空当前桶。桶为空时返回 None。"""
        if not self._bars or self._key is None:
            return None
        bucket = (self._key, self._bars)
        self._bars = {}
        self._key = None
        return bucket


# ── 账户快照 ──────────────────────────────────────────────────


async def fetch_account_snapshot(
    oms,
    market: str,
    symbols: Sequence[str],
    last_prices: Mapping[str, float],
) -> AccountSnapshot:
    """
    异步拉账户与持仓，冻结成同步可读的 `AccountSnapshot`。

    任何一步不可用都抛 `AccountSnapshotError` —— **不允许**用 0 兜底：
    零净值会让 `target_weight` 把所有目标算成 0 股，等于一次全仓清空。
    """
    try:
        account = await oms.get_account(market)
        positions = await oms.get_positions(market)
    except Exception as exc:                       # 限流/断连在实盘是常态
        raise AccountSnapshotError(f"账户快照拉取失败（{market}）: {exc}") from exc

    quantities, avg_costs = {}, {}
    prices = dict(last_prices)
    for row in positions or ():
        symbol = row.get("symbol")
        if not symbol:
            continue
        quantities[symbol] = int(row.get("qty") or 0)
        avg_costs[symbol] = float(row.get("avg_cost") or 0.0)
        current = row.get("current_price")
        if current:
            prices[symbol] = float(current)

    try:
        return AccountSnapshot(
            cash=float(account.get("cash") or 0.0),
            portfolio_value=float(account.get("portfolio_value") or 0.0),
            positions=quantities,
            prices=prices,
            avg_costs=avg_costs,
            taken_at=datetime.now(UTC),
        )
    except (TypeError, ValueError) as exc:
        raise AccountSnapshotError(f"账户快照非法（{market}）: {exc}") from exc


# ── 订单冲刷 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class FlushedOrder:
    """一张本地订单的冲刷结果。`live_order is None` 且 `error` 非空即为失败。"""

    local_order: Order
    live_order: LiveOrder | None = None
    error: str | None = None


async def flush_orders(
    oms,
    orders: Sequence[Order],
    *,
    strategy_id: str | None = None,
    snapshot: AccountSnapshot | None = None,
) -> list[FlushedOrder]:
    """
    把本地订单逐个送进 OMS。

    **必须走 `OrderManager.submit_order`**，不得自己直连 gateway —— 否则会绕过
    `_pre_trade_risk_check`、L-a 接进 OMS 的 `TradingControl`、以及动态防护。

    给了 `snapshot` 时还会先过一遍 `RiskEngine.pre_trade_check`（敞口/集中度
    类限额），与单标的路径 `_submit_live_order` 的闸门保持一致 —— 组合策略
    不该比单标的策略少一道风控。

    单张失败不影响后续订单：失败原因记进 `FlushedOrder.error` 并打日志，
    不静默丢弃。
    """
    results: list[FlushedOrder] = []
    for order in orders:
        live_type = _LIVE_ORDER_TYPES.get(order.order_type)
        if live_type is None:
            reason = (
                f"实盘不支持的订单类型 {order.order_type.value}"
                "（券商网关只接受 MARKET / LIMIT）"
            )
            logger.error("%s 的订单未提交：%s", order.symbol, reason)
            results.append(FlushedOrder(local_order=order, error=reason))
            continue
        blocked = _risk_block_reason(order, snapshot)
        if blocked is not None:
            logger.warning("%s 的订单被风控拦下：%s", order.symbol, blocked)
            results.append(FlushedOrder(local_order=order, error=blocked))
            continue
        result = await _submit_one(oms, order, live_type, strategy_id)
        if result.error is None:
            get_risk_engine().on_order_submitted()
        results.append(result)
    return results


def _risk_block_reason(order: Order, snapshot: AccountSnapshot | None) -> str | None:
    """`RiskEngine` 的 BLOCK 级违规原因；无快照或无违规时返回 None。"""
    if snapshot is None:
        return None
    price = snapshot.prices.get(order.symbol)
    if price is None:
        return None                                # 无参考价时交给 OMS 侧的闸门
    held = snapshot.positions.get(order.symbol, 0)
    violations = get_risk_engine().pre_trade_check(
        symbol=order.symbol,
        market=order.market.value if isinstance(order.market, Market) else str(order.market),
        side=order.side.value,
        qty=order.qty,
        price=price,
        portfolio_value=snapshot.portfolio_value,
        current_symbol_value=abs(held) * price,
    )
    blocking = [v for v in violations if v.severity == ViolationSeverity.BLOCK]
    if not blocking:
        return None
    return "; ".join(v.message for v in blocking)


async def _submit_one(
    oms, order: Order, live_type: LiveOrderType, strategy_id: str | None
) -> FlushedOrder:
    """提交单张订单，异常收敛成 `FlushedOrder.error`。"""
    try:
        live = await oms.submit_order(
            symbol=order.symbol,
            market=order.market.value if isinstance(order.market, Market) else str(order.market),
            side=LiveOrderSide.BUY if order.side is OrderSide.BUY else LiveOrderSide.SELL,
            qty=order.qty,
            order_type=live_type,
            limit_price=order.limit_price,
            strategy_id=strategy_id,
        )
    except Exception as exc:
        logger.exception("订单提交失败: %s %s x%d", order.side.value, order.symbol, order.qty)
        return FlushedOrder(local_order=order, error=str(exc))
    return FlushedOrder(local_order=order, live_order=live)


# ── 滚动历史 ──────────────────────────────────────────────────


class _LiveHistories(Mapping):
    """各标的截至当前时点的历史，**按需物化**成 DataFrame 并在本时点内缓存。"""

    __slots__ = ("_bars", "_cache")

    def __init__(self, bars: Mapping[str, list[Bar]]) -> None:
        self._bars = bars
        self._cache: dict[str, pd.DataFrame] = {}

    def __getitem__(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._cache:
            series = self._bars[symbol]        # KeyError 语义即「不在订阅标的内」
            self._cache[symbol] = (
                _bars_to_df(series) if series else _empty_history()
            )
        return self._cache[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self._bars)

    def __len__(self) -> int:
        return len(self._bars)


def _empty_history() -> pd.DataFrame:
    """没有任何历史时的空表：列齐全，避免策略在 `df["close"]` 上炸掉。"""
    frame = pd.DataFrame(
        columns=["time", "open", "high", "low", "close", "volume", "vwap"]
    )
    return frame.set_index("time")


# ── 实盘循环 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class LiveStepReport:
    """一个时点跑完后的产出，交给调用方更新实例统计。"""

    time: datetime
    symbols: tuple[str, ...]
    orders_submitted: int
    orders_failed: int


class LivePortfolioRunner:
    """
    组合策略的实盘循环。与回测引擎的 `_step` 一一对应，差别只在

    - 撮合发生在券商侧（这里只有账户快照）；
    - bar 需要先聚合成一个时点。

    调用方通过 `on_step` / `on_fatal` 回调感知进度与致命错误 —— 本类刻意不认识
    `StrategyInstance`，以免与 `app.strategy.engine` 成环。
    """

    def __init__(
        self,
        *,
        instance_id: str,
        strategy,
        symbols: Sequence[str],
        market: Market,
        frequency: Frequency,
        data_service,
        warmup_bars: Mapping[str, list[Bar]] | None = None,
        oms_provider: Callable[[], object] = get_order_manager,
        window_seconds: float = BAR_AGGREGATION_WINDOW_SECONDS,
        max_snapshot_failures: int = MAX_CONSECUTIVE_SNAPSHOT_FAILURES,
        cash_per_position: float | None = None,
        allow_short: bool = False,
        on_step: Callable[[LiveStepReport], None] | None = None,
        on_fatal: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._instance_id = instance_id
        self._strategy = strategy
        self._symbols = list(symbols)
        self._market = market
        self._frequency = frequency
        self._data_service = data_service
        self._oms_provider = oms_provider
        self._max_failures = max(1, int(max_snapshot_failures))
        self._cash_per_position = cash_per_position
        self._allow_short = allow_short
        self._on_step = on_step
        self._on_fatal = on_fatal
        self._clock = clock or time.monotonic

        self._aggregator = BarAggregator(self._symbols, frequency, window_seconds)
        self._history: dict[str, list[Bar]] = {
            symbol: list((warmup_bars or {}).get(symbol, ())) for symbol in self._symbols
        }
        self._last_prices: dict[str, float] = {
            s: bars[-1].close for s, bars in self._history.items() if bars
        }
        self._started = False

        self.bars_processed = 0
        self.orders_placed = 0
        self.snapshot_failures = 0
        self.error: str | None = None

    # ── 入口 ─────────────────────────────────────────────────

    async def run(self) -> None:
        """跑到行情流结束、被取消、或出现致命错误为止。"""
        queue: asyncio.Queue = asyncio.Queue()
        feeder = asyncio.create_task(
            self._feed(queue), name=f"live-feed:{self._instance_id}"
        )
        try:
            await self._consume(queue)
        except asyncio.CancelledError:
            logger.info("实盘循环被取消: %s", self._instance_id)
            raise
        except _LiveRunAbortedError as exc:
            self._fail(str(exc))
        except Exception as exc:
            logger.exception("实盘循环异常退出: %s", self._instance_id)
            self._fail(str(exc))
        finally:
            feeder.cancel()
            with suppress(asyncio.CancelledError):
                await feeder

    async def _feed(self, queue: asyncio.Queue) -> None:
        """把行情推进队列。用队列而不是直接 `async for`，是为了能给等待加超时。"""
        try:
            async for bar in self._data_service.subscribe_bars(
                self._symbols, self._market, self._frequency
            ):
                await queue.put(bar)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(exc)
            return
        await queue.put(_STREAM_END)

    async def _consume(self, queue: asyncio.Queue) -> None:
        while True:
            item = await self._next(queue)
            if item is _STREAM_END:
                await self._fire()
                return
            if item is None:                       # 聚合窗口超时
                await self._fire()
                continue
            if isinstance(item, BaseException):
                raise item
            deferred = self._aggregator.add(item, self._clock())
            if deferred is not None:               # 已经是下一个时点的 bar
                await self._fire()
                self._aggregator.add(deferred, self._clock())
            if self._aggregator.is_complete:
                await self._fire()

    async def _next(self, queue: asyncio.Queue):
        """取下一个事件；聚合窗口到期时返回 None。"""
        timeout = self._aggregator.remaining(self._clock())
        if timeout is None:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), timeout)
        except TimeoutError:
            return None

    # ── 一个时点 ─────────────────────────────────────────────

    async def _fire(self) -> None:
        """同步采集 → 同步跑策略 → 异步冲刷。"""
        bucket = self._aggregator.flush()
        if bucket is None:
            return
        timestamp, bars = bucket
        self._absorb(bars)

        snapshot = await self._take_snapshot()
        if snapshot is None:
            return                                 # 跳过本时点，下一时点重试

        ctx = self._build_context(timestamp, bars, snapshot)
        self._invoke_strategy(ctx)
        results = await self._flush(ctx.pending_orders(), snapshot)

        failed = sum(1 for r in results if r.error is not None)
        self.bars_processed += len(bars)
        self.orders_placed += len(results) - failed
        if self._on_step is not None:
            self._on_step(
                LiveStepReport(
                    time=timestamp,
                    symbols=tuple(sorted(bars)),
                    orders_submitted=len(results) - failed,
                    orders_failed=failed,
                )
            )

    def _absorb(self, bars: Mapping[str, Bar]) -> None:
        """把本时点的 bar 并入滚动历史与最后已知价。"""
        for symbol, bar in bars.items():
            self._history.setdefault(symbol, []).append(bar)
            self._last_prices[symbol] = bar.close

    async def _take_snapshot(self) -> AccountSnapshot | None:
        """拉快照。失败返回 None（跳过本时点）；连续失败超限抛 `_LiveRunAbortedError`。"""
        try:
            oms = self._oms_provider()
            snapshot = await fetch_account_snapshot(
                oms, self._market.value, self._symbols, self._last_prices
            )
        except Exception as exc:
            self.snapshot_failures += 1
            logger.error(
                "账户快照失败（第 %d/%d 次），跳过本时点: %s — %s",
                self.snapshot_failures, self._max_failures, self._instance_id, exc,
            )
            if self.snapshot_failures >= self._max_failures:
                raise _LiveRunAbortedError(
                    f"连续 {self.snapshot_failures} 次账户快照失败，停止策略: {exc}"
                ) from exc
            return None
        self.snapshot_failures = 0
        return snapshot

    def _build_context(
        self, timestamp: datetime, bars: Mapping[str, Bar], snapshot: AccountSnapshot
    ) -> LivePortfolioContext:
        return LivePortfolioContext(
            snapshot=snapshot,
            time=timestamp,
            bars=bars,
            symbols=list(self._symbols),
            histories=_LiveHistories(self._history),
            market=self._market,
            cash_per_position=self._cash_per_position,
            allow_short=self._allow_short,
        )

    def _invoke_strategy(self, ctx: LivePortfolioContext) -> None:
        """
        跑策略。异常只记录不上抛 —— 与回测引擎 `_step` 的处理一致：
        一根 bar 上的策略错误不该让整个实例停摆。抛错前已下达的订单仍会冲刷，
        这同样与回测一致（那边订单已经进了券商挂单队列）。
        """
        if not self._started:
            self._started = True
            try:
                self._strategy.on_start(ctx)
            except Exception:
                logger.exception("策略 on_start 失败: %s", self._instance_id)
        try:
            self._strategy.on_bars(ctx)
        except Exception:
            logger.exception("策略 on_bars 失败: %s @ %s", self._instance_id, ctx.time)

    async def _flush(
        self, orders: Sequence[Order], snapshot: AccountSnapshot
    ) -> list[FlushedOrder]:
        if not orders:
            return []
        try:
            oms = self._oms_provider()
        except Exception as exc:
            logger.error("OMS 不可用，%d 张订单未提交: %s", len(orders), exc)
            return [FlushedOrder(local_order=o, error=str(exc)) for o in orders]
        return await flush_orders(
            oms, orders, strategy_id=self._instance_id, snapshot=snapshot
        )

    def _fail(self, message: str) -> None:
        self.error = message
        if self._on_fatal is not None:
            self._on_fatal(message)
