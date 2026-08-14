"""实盘组合上下文 —— 同一份策略代码跑回测与实盘（Wave L-d / §二、§四）

`PortfolioContext` 是**同步**接口，OMS 是异步的。本模块不把上下文改成 async
（那会连累回测引擎，而回测根本不需要），而是沿用「同步采集 + 异步冲刷」：

    1. ★ await 拉账户/持仓，冻结成 `AccountSnapshot`
    2. 构造 `LivePortfolioContext(快照, 本时点 bars, 历史)`
    3. strategy.on_bars(ctx)      ← 同步，与回测**完全同一份代码**
    4. ★ await 把收集到的 Order 逐个送进 OMS

因此 `ctx.buy()` 返回的是**本地 `Order` 对象**：`status=PENDING`、`order_id` 是
本地 id、`filled_price` 永远是 `None`。策略拿到订单后立刻读成交价在实盘拿不到
东西 —— 这与回测的 next-bar 成交语义一致，不是缺陷。真实的券商订单号由
`live_runner.flush_orders()` 返回的 `FlushedOrder.live_order` 承载，不回写本地
对象（本地 id 是策略侧的稳定引用，冲刷不该把它换掉）。

## 为什么覆盖的方法这么少

契约 §四 列了一张「每个方法的实盘实现」对照表，但真正需要覆盖的只有**下单**
那几个。账户状态类的读取（`cash` / `portfolio_value` / `position` / `qty` /
`price` / `current_prices`）全部**原样继承** —— 它们读的是 `self.broker`，
而实盘这里装的是 `_SnapshotBroker`：账户快照在券商只读接口上的投影。

这样 `target_weight` / `buy_value` / `close_all` / `history` 连同将来新增的
派生方法都不必再抄一遍。少一份实现就少一处漂移，这正是 K-c 当初
「不让 context 持有 broker 引用（只读快照即可满足）」那个决定的回报。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

import pandas as pd

from app.data.models import Bar, Market
from app.engine.backtest.broker import Order, OrderSide, OrderStatus
from app.engine.backtest.position import Position
from app.strategy.context import PortfolioContext, _backtest_order_kwargs
from app.strategy.precompute import IndicatorProvider

__all__ = ["AccountSnapshot", "LivePortfolioContext"]


@dataclass(frozen=True)
class AccountSnapshot:
    """
    某一时刻的账户与持仓快照，供同步的 `PortfolioContext` 接口读取。

    构造即校验：`portfolio_value` 必须是**正的有限数**。这条看似苛刻的规则是
    本契约最关键的一道闸门 —— 账户拉取失败在实盘是常态（限流、断连），而
    `portfolio_value=0` 会让 `target_weight` 把每个目标股数都算成 0，
    也就是**全部清仓**。让非法快照根本造不出来，比在下游各处防御可靠得多。
    """

    cash: float
    portfolio_value: float
    #: symbol → 带符号持仓（多头为正、空头为负）
    positions: Mapping[str, int]
    #: symbol → 最新价（含无 bar 的停牌标的的最后已知价）
    prices: Mapping[str, float]
    taken_at: datetime
    #: symbol → 平均成本。契约 §2.1 之外的补充字段：还原 `Position` 需要它
    #: （缺省时退化为用最新价当成本，只影响浮盈显示，不影响下单口径）。
    avg_costs: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.cash):
            raise ValueError(f"账户快照的 cash 非法: {self.cash}")
        if not math.isfinite(self.portfolio_value) or self.portfolio_value <= 0:
            raise ValueError(
                f"账户快照的 portfolio_value 必须为正的有限数，收到 {self.portfolio_value}"
                "（0 会让 target_weight 把全部目标算成 0 股 = 全仓清空）"
            )
        for name in ("positions", "prices", "avg_costs"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


class _SnapshotPositions:
    """
    账户快照在 `PortfolioPositions` 只读接口上的投影。

    `get()` 与回测一致：**永远返回一个 `Position`**，无持仓时返回空仓而不是
    `None` —— 上游 `PortfolioContext.position()` 会读 `.is_empty`。
    """

    __slots__ = ("_snapshot", "_cache")

    def __init__(self, snapshot: AccountSnapshot) -> None:
        self._snapshot = snapshot
        self._cache: dict[str, Position] = {}

    def get(self, symbol: str) -> Position:
        if symbol not in self._cache:
            self._cache[symbol] = self._build(symbol)
        return self._cache[symbol]

    @property
    def open_symbols(self) -> list[str]:
        return [s for s, qty in self._snapshot.positions.items() if qty != 0]

    def net_quantities(self) -> dict[str, int]:
        return {s: qty for s, qty in self._snapshot.positions.items() if qty != 0}

    def _build(self, symbol: str) -> Position:
        position = Position(symbol=symbol)
        held = int(self._snapshot.positions.get(symbol, 0))
        if held == 0:
            return position
        cost = self._snapshot.avg_costs.get(symbol) or self._snapshot.prices.get(symbol, 0.0)
        if held > 0:
            position.add(held, cost)
        else:
            position.add_short(-held, cost)
        return position


class _SnapshotBroker:
    """
    实盘没有回测券商。这个替身把 `AccountSnapshot` 投影成券商的**只读**接口，
    让 `PortfolioContext` 里所有读账户状态的方法都能原样继承。

    刻意不实现任何**写**方法（`buy` / `sell` / `submit_order` …）：实盘的订单
    必须由 `LivePortfolioContext` 收集、再由 `live_runner.flush_orders` 送进 OMS。
    误走券商写路径会撞上一条明确的 AttributeError，而不是安静地下不出单。
    """

    __slots__ = ("allow_short", "_snapshot", "positions")

    def __init__(self, snapshot: AccountSnapshot, allow_short: bool = False) -> None:
        self.allow_short = allow_short
        self._snapshot = snapshot
        self.positions = _SnapshotPositions(snapshot)

    @property
    def cash(self) -> float:
        return self._snapshot.cash

    def portfolio_value(self, prices: Mapping[str, float] | None = None) -> float:
        """快照的净值由券商给出，已含全部持仓 —— 不按 `prices` 重算。"""
        return self._snapshot.portfolio_value

    def mark_prices(self) -> dict[str, float]:
        return dict(self._snapshot.prices)

    def last_known_price(self, symbol: str) -> float | None:
        return self._snapshot.prices.get(symbol)

    def __getattr__(self, name: str):
        raise AttributeError(
            f"实盘账户快照不提供 broker.{name}：写操作必须走 LivePortfolioContext "
            f"的下单接口 + live_runner.flush_orders（Wave L-d 契约 §五）"
        )


class LivePortfolioContext(PortfolioContext):
    """
    实盘版组合上下文。与回测版**接口完全一致**，实现换成「快照 + 订单收集」。

    覆盖的只有下单接口：它们不再进券商挂单队列，而是构造一张本地 `PENDING`
    订单收进 `pending_orders()`，等本时点的 `on_bars` 跑完后由
    `live_runner` 统一冲刷进 OMS。其余方法（含 `target_weight`）一律原样继承。
    """

    def __init__(
        self,
        *,
        snapshot: AccountSnapshot,
        bars: Mapping[str, Bar],
        symbols: list[str],
        histories: Mapping[str, pd.DataFrame],
        market: Market,
        time: datetime | None = None,
        cash_per_position: float | None = None,
        allow_short: bool = False,
        indicators: IndicatorProvider | None = None,
    ) -> None:
        super().__init__(
            time=time or snapshot.taken_at,
            bars=dict(bars),
            symbols=list(symbols),
            broker=_SnapshotBroker(snapshot, allow_short),  # type: ignore[arg-type]
            histories=histories,
            market=market,
            cash_per_position=cash_per_position,
            # E-a：实盘装 `LiveIndicatorBook`（在前缀历史上现算），
            # 让 `ctx.ind(symbol)` 在回测与实盘是同一份策略代码
            indicators=indicators,
        )
        self._snapshot = snapshot
        self._pending: list[Order] = []

    # ── 快照与订单意图 ───────────────────────────────────────

    @property
    def snapshot(self) -> AccountSnapshot:
        return self._snapshot

    def pending_orders(self) -> tuple[Order, ...]:
        """本时点策略下达的全部订单意图，按下达顺序。冲刷前它们都是 PENDING。"""
        return tuple(self._pending)

    # ── 下单：只记录意图 ─────────────────────────────────────

    def buy(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        entry_tag: str | None = None,
    ) -> Order | None:
        if qty <= 0:
            return None
        return self._record(
            symbol, qty, OrderSide.BUY, market, order_type, limit_price,
            entry_tag=entry_tag,
        )

    def sell(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        exit_reason: str | None = None,
    ) -> Order | None:
        if qty <= 0:
            return None
        return self._record(
            symbol, qty, OrderSide.SELL, market, order_type, limit_price,
            exit_reason=exit_reason,
        )

    def short(self, symbol: str, qty: int, market: Market | None = None) -> Order | None:
        """
        实盘开空。保证金模型（A7）落地前**不对实盘开放**（契约 §七），
        因此未授权时显式抛错 —— 静默丢弃一张开空单会让策略以为自己有空头保护。
        """
        if not self.broker.allow_short:
            raise ValueError(
                "实盘未开放做空：保证金模型（A7）落地前 LivePortfolioContext.short 被禁用"
            )
        if qty <= 0:
            return None
        return self._record(symbol, qty, OrderSide.SELL, market, "MARKET", None)

    def cover(
        self, symbol: str, qty: int, market: Market | None = None,
        exit_reason: str | None = None,
    ) -> Order | None:
        if qty <= 0:
            return None
        return self._record(
            symbol, qty, OrderSide.BUY, market, "MARKET", None, exit_reason=exit_reason
        )

    def submit(self, order: Order) -> Order:
        """收集一张已构造好的订单（K5 执行模型的出口），原样返回不做二次加工。"""
        self._pending.append(order)
        return order

    def _record(
        self,
        symbol: str,
        qty: int,
        side: OrderSide,
        market: Market | None,
        order_type: str,
        limit_price: float | None,
        entry_tag: str | None = None,
        exit_reason: str | None = None,
    ) -> Order:
        """构造一张本地 PENDING 订单并收集。"""
        order = Order(
            symbol=symbol,
            market=self._market_of(symbol, market),
            side=side,
            qty=qty,
            status=OrderStatus.PENDING,
            entry_tag=entry_tag,
            exit_reason=exit_reason,
            **_backtest_order_kwargs(order_type, limit_price),
        )
        self._pending.append(order)
        return order
