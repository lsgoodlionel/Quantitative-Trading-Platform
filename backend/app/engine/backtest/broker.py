"""
模拟撮合引擎

回测中模拟真实券商的订单处理流程。订单在下一根 K 线开盘价成交
（next-bar fill），避免偷看当根 K 线数据（look-ahead bias）。

参考: refs/backtrader/backtrader/broker.py 的 BackBroker 设计。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from app.data.models import Bar, Market
from app.engine.backtest.broker_adjust import PositionAdjustMixin
from app.engine.backtest.broker_exits import RiskExitMixin
from app.engine.backtest.broker_order_hooks import OrderHookMixin
from app.engine.backtest.commission import CommissionModel, get_commission_model
from app.engine.backtest.order_types import (
    MatchResult,
    OrderType,
    TimeInForce,
    match_order,
    seed_trailing_extreme,
    trailing_trigger_price,
    update_trailing_extreme,
    validate_order_spec,
)
from app.engine.backtest.position import PortfolioPositions
from app.engine.backtest.slippage import SlippageModel, get_slippage_model
from app.engine.controls.base import (
    AccountControl,
    ControlContext,
    ControlViolation,
    TradingControl,
    run_controls,
)

logger = logging.getLogger(__name__)

#: 融券费按自然日计提
DAYS_PER_YEAR = 365.0


class OrderStatus(str, Enum):
    PENDING = "pending"     # 等待下一根 bar 撮合
    PARTIAL = "partial"     # 部分成交，残量仍挂单（命名对齐 OMS 的 LiveOrderStatus.PARTIAL）
    FILLED = "filled"       # 已成交
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderSide(str, Enum):
    """
    方向仍只有两值；多空由持仓状态推导（契约 §3.1）：
    - 持仓为 0 或正 → SELL 超出持仓的部分即为开空（需 allow_short）
    - 持仓为负 → BUY 为平空，超出部分转为开多
    """

    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    symbol: str
    market: Market
    side: OrderSide
    qty: int
    order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    filled_price: float | None = None
    filled_at: datetime | None = None
    commission: float = 0.0
    reject_reason: str | None = None
    # K6 部分成交：累计已成交数量 + 本次撮合被成交量约束截断的残量
    filled_qty: int = 0
    remaining_qty: int = 0
    # 开仓标签（策略自定义分组用；平仓单可留空）
    entry_tag: str | None = None
    # 平仓原因（如 'stop'/'take_profit'/'signal'；开仓单为 None）
    exit_reason: str | None = None
    # ── K3 订单类型（全部带默认值，向后兼容）────────────────
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    trigger_price: float | None = None     # LIMIT_IF_TOUCHED 的触发价
    trailing_pct: float | None = None      # 0.05 = 从极值回撤 5% 触发
    time_in_force: TimeInForce = TimeInForce.GTC
    good_till_date: datetime | None = None
    # ── 运行期状态（不由调用方设置）──────────────────────────
    _triggered: bool = False               # STOP 类是否已触发
    _extreme_price: float | None = None    # TRAILING_STOP 跟踪的极值
    _submit_date: date | None = None       # 挂单时所处的 bar 日期（TIF 用）
    #: L5 分钟级挂单超时的到期时刻（None = 不超时）。与 TIF 并存，先到者生效
    _expire_at: datetime | None = None


@dataclass
class Fill:
    order_id: str
    symbol: str
    market: Market
    side: OrderSide
    qty: int
    price: float
    commission: float
    filled_at: datetime
    realized_pnl: float = 0.0
    # C7 回合分析所需的分组维度（向后兼容，默认无标签）
    entry_tag: str | None = None
    exit_reason: str | None = None
    direction: str = "long"
    #: 本笔成交是否触发了平仓（metrics.total_trades 的计数口径）
    is_close: bool = False


class SimulatedBroker(RiskExitMixin, PositionAdjustMixin, OrderHookMixin):
    """
    模拟券商，维护现金、持仓、挂单队列。

    K4 的本笔交易跟踪与风险闸门（`check_exit_conditions`）在 `RiskExitMixin`，
    见 `broker_exits.py` —— 撮合与风险闸门是两件事，不该挤在同一个文件里。
    同理 L4 的仓位调整在 `broker_adjust.py`、L5 的下单钩子在 `broker_order_hooks.py`，
    两者默认关闭时都是零开销路径。

    撮合规则:
    - 市价单在下一根 bar 开盘价成交（next-bar open fill）
    - 成交价经过滑点模型调整
    - 成交产生 Fill 事件，更新持仓和现金

    A股特规 (参考 refs/rqalpha/):
    - T+1: 当日买入的股票当日不可卖出
    - 涨跌停: ±10% 限制，超过限价的订单拒绝成交

    做空 (allow_short=True) 的已知建模简化，见契约 §6「本期不做」:
    1. **没有保证金/买入力约束**：开空只按成交金额贷记现金，不校验权益倍数，
       因此理论上可以无限杠杆。保证金模型排在 A7（Lean BuyingPowerModel）。
    2. 融券费按**开仓名义金额**而非盯市市值计提，价格远离开仓价时会有偏差。
    3. 反手单（一张单同时平旧仓 + 开新仓）只产出一笔 Fill，新开仓那一腿的
       entry_tag 会丢失（回合分析回退成 untagged），盈亏金额本身不受影响。
    A股不开放做空（融券不在本期范围），构造时即拒绝。
    """

    # A股涨跌停比率（普通股票）
    A_PRICE_LIMIT_PCT = 0.10

    def __init__(
        self,
        initial_cash: float,
        market: Market,
        commission_model: CommissionModel | None = None,
        slippage_model: SlippageModel | None = None,
        allow_short: bool = False,
        short_borrow_rate: float = 0.0,
        controls: Sequence[TradingControl] | None = None,
        account_controls: Sequence[AccountControl] | None = None,
    ) -> None:
        if allow_short and market == Market.A:
            raise ValueError(
                "A股市场不支持做空：融券不在本期范围，请将 allow_short 设为 False"
            )
        if short_borrow_rate < 0:
            raise ValueError("short_borrow_rate 不能为负")

        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._market = market
        self._commission = commission_model or get_commission_model(market)
        self._slippage = slippage_model or get_slippage_model(market)
        self._positions = PortfolioPositions()
        self._pending: list[Order] = []
        self._fills: list[Fill] = []
        self._is_a_share = market == Market.A
        self._allow_short = allow_short
        self._short_borrow_rate = short_borrow_rate
        # 非成交现金流累计（分红入账为正、融券费为负）。日度盈亏拆解要用它才能
        # 把「净值变动 == net_pnl」的恒等式对上，否则分红/融券费会凭空消失。
        self._external_cash_flow = 0.0
        # 最近一根已处理 bar 的时间：TIF 过期判定与融券费计提的时间锚点
        self._last_bar_time: datetime | None = None
        # 各标的最后一次见到的收盘价：控制器的参考价与估值都用它
        self._last_prices: dict[str, float] = {}
        self._init_risk_state()          # K4 本笔交易 + 风险闸门状态
        # ── L1 交易控制器（默认全空 = 与控制器上线前逐笔一致）────
        self._controls: tuple[TradingControl, ...] = tuple(controls or ())
        self._account_controls: tuple[AccountControl, ...] = tuple(account_controls or ())
        self._orders_today = 0
        self._orders_today_date: date | None = None
        self._account_violations: list[ControlViolation] = []
        self._init_adjust_state()        # L4 仓位调整计数
        self._init_order_hook_state()    # L5 下单钩子绑定

    @property
    def allow_short(self) -> bool:
        return self._allow_short

    @property
    def external_cash_flow(self) -> float:
        """累计非成交现金流（分红为正、融券费为负）。"""
        return self._external_cash_flow

    # ── 账户状态 ──────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> PortfolioPositions:
        return self._positions

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    def portfolio_value(self, prices: dict[str, float]) -> float:
        return self._cash + self._positions.total_market_value(prices)

    # ── 账户状态 (T+1) ────────────────────────────────────────

    def advance_day(self) -> None:
        """新交易日开始：解除 A股 T+1 限制（昨日买入今日可卖）。"""
        if self._is_a_share:
            self._positions.advance_day()

    # ── 下单 ─────────────────────────────────────────────────

    def submit_order(self, order: Order) -> Order:
        """将订单加入挂单队列，返回带 order_id 的 Order 对象。"""
        if order.qty <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "qty must be positive"
            return order

        spec_error = validate_order_spec(
            order.order_type,
            order.limit_price,
            order.stop_price,
            order.trailing_pct,
            order.trigger_price,
        )
        if spec_error is not None:
            order.status = OrderStatus.REJECTED
            order.reject_reason = spec_error
            return order

        position = self._positions.get(order.symbol)

        # allow_short=False 时，SELL 校验逻辑与做空特性上线前完全一致
        if order.side == OrderSide.SELL and not self._allow_short:
            if self._is_a_share:
                # A股 T+1: 只能卖出可卖出的股票（非当日买入部分）
                available = position.closable_qty
                if order.qty > available:
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = (
                        f"A股T+1限制: 可卖 {available} 股，请求卖 {order.qty} 股"
                    )
                    return order
            else:
                if order.qty > position.qty:
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = (
                        f"insufficient position: need {order.qty}, have {position.qty}"
                    )
                    return order

        # L5 放在券商自身校验之后：不必为一张必然被拒的单去打扰策略。
        # 未绑定钩子（默认）时只多一次 `is None` 判断。
        if self._order_hooks is not None and not self.apply_order_hooks(order):
            return order

        # L1 交易控制器：**必须排在 L5 之后**。`custom_entry_price` 会把市价单改成限价单、
        # 改掉成交价，而 MaxOrderSize/MaxPositionSize 的 max_notional 是按价格算的 ——
        # 控制器要看到的是**最终**订单，否则金额类限额会按一个已被改掉的价格判定。
        # 未配置控制器时是一次布尔判断，逐笔行为与控制器上线前完全一致。
        if self._controls:
            violation = run_controls(self._controls, self._control_context(order))
            if violation is not None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = violation.as_reject_reason()
                return order

        order._submit_date = self._last_bar_time.date() if self._last_bar_time else None
        self._count_order()
        self._pending.append(order)
        return order

    # ── L1 交易控制器 ────────────────────────────────────────

    def set_controls(self, controls: Sequence[TradingControl] | None) -> None:
        """替换交易控制器列表（与 `OrderManager.set_controls` 对称）。"""
        self._controls = tuple(controls or ())

    @property
    def account_violations(self) -> list[ControlViolation]:
        """账户级控制器累计命中的违规（on_error='log' 时不中断，但必须留痕）。"""
        return list(self._account_violations)

    def _current_order_count(self) -> int:
        """
        当日已接受的委托数，跨日惰性归零。

        归零必须发生在**读取**时而不只是写入时：否则新交易日的第一单会拿着
        昨天的计数去过 `MaxOrderCount`，上限一旦触顶就再也不会解除。
        """
        today = self._last_bar_time.date() if self._last_bar_time else None
        if today != self._orders_today_date:
            self._orders_today_date = today
            self._orders_today = 0
        return self._orders_today

    def _count_order(self) -> None:
        """接受一张委托后累加当日计数。"""
        self._orders_today = self._current_order_count() + 1

    def gross_leverage(self, prices: dict[str, float] | None = None) -> float:
        """
        总杠杆 = 多空双边名义敞口之和 / 账户权益。权益非正时返回 0（无从定义）。
        """
        marks = self._last_prices if prices is None else prices
        equity = self.portfolio_value(marks)
        if equity <= 0:
            return 0.0
        gross = 0.0
        for symbol in self._positions.open_symbols:
            pos = self._positions.get(symbol)
            gross += abs(pos.qty) * marks.get(symbol, pos.avg_cost)
        return gross / equity

    def _control_context(self, order: Order) -> ControlContext:
        """把券商内部状态摊平成回测与实盘共用的 `ControlContext`。"""
        marks = self._last_prices
        return ControlContext(
            symbol=order.symbol,
            market=order.market,
            side=order.side.value,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            price=marks.get(order.symbol),
            now=self._last_bar_time or datetime.utcnow(),
            current_qty=self._positions.get(order.symbol).qty,
            portfolio_value=self.portfolio_value(marks),
            cash=self._cash,
            orders_today=self._current_order_count(),
            leverage=self.gross_leverage(marks),
        )

    def _run_account_controls(self, now: datetime) -> None:
        """成交之后做账户级校验（杠杆类约束只有事后才判得出来）。"""
        marks = self._last_prices
        ctx = ControlContext(
            now=now,
            portfolio_value=self.portfolio_value(marks),
            cash=self._cash,
            leverage=self.gross_leverage(marks),
        )
        violation = run_controls(self._account_controls, ctx)
        if violation is not None:
            self._account_violations.append(violation)

    def buy(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        entry_tag: str | None = None,
        **order_kwargs,
    ) -> Order:
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.BUY,
            qty=qty,
            entry_tag=entry_tag,
            **order_kwargs,
        )
        return self.submit_order(order)

    def sell(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        exit_reason: str | None = None,
        **order_kwargs,
    ) -> Order:
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.SELL,
            qty=qty,
            exit_reason=exit_reason,
            **order_kwargs,
        )
        return self.submit_order(order)

    # ── 做空快捷方法（语义化包装，方向仍由持仓推导）─────────

    def short(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        entry_tag: str | None = None,
        **order_kwargs,
    ) -> Order:
        """开空：本质是一张 SELL 单，超出多头持仓的部分转为空头批次。"""
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.SELL,
            qty=qty,
            entry_tag=entry_tag,
            **order_kwargs,
        )
        return self.submit_order(order)

    def cover(
        self,
        symbol: str,
        qty: int,
        market: Market | None = None,
        exit_reason: str | None = None,
        **order_kwargs,
    ) -> Order:
        """平空：本质是一张 BUY 单，优先消耗空头批次。"""
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.BUY,
            qty=qty,
            exit_reason=exit_reason,
            **order_kwargs,
        )
        return self.submit_order(order)

    # ── 撮合 ─────────────────────────────────────────────────

    def process_bar(self, bar: Bar) -> list[Fill]:
        """
        用当根 bar 撮合上一根 bar 挂入的订单（next-bar fill）。
        返回本次产生的所有 Fill。

        顺序：计提融券费 → 撮合 → TIF 过期撤单。
        「先撮合后过期」意味着 DAY 单在跨日那根 bar 上仍有一次成交机会，
        这是 next-bar 撮合语义下 DAY 唯一说得通的解释。

        K6 部分成交：滑点模型的成交量上限截断订单时，只成交上限内的数量，
        残量以 `PARTIAL` 状态留在挂单队列，等下一根 bar 继续撮合。
        因此同一个 order_id 可能对应多笔 Fill。
        """
        self._accrue_borrow_fee(bar.time)
        self._last_bar_time = bar.time
        new_fills = self._match_symbol(bar)
        self._last_prices[bar.symbol] = bar.close
        if new_fills and self._account_controls:
            self._run_account_controls(bar.time)
        self._expire_orders(bar.time.date())
        return new_fills

    def _match_symbol(self, bar: Bar) -> list[Fill]:
        """
        用一根 bar 撮合该标的的挂单，其余标的的挂单原样留在队列里。

        从 `process_bar` 抽出，供组合券商在一个时点内逐标的撮合复用
        （见 `portfolio_broker.PortfolioBroker.process_bars`）。
        """
        new_fills: list[Fill] = []
        remaining: list[Order] = []
        # 本根 bar 已在撮合，用它的收盘价作为后续 L5 钩子的参考价不构成偷看
        self._record_hook_price(bar.symbol, bar.close)

        for order in self._pending:
            if order.symbol != bar.symbol:
                remaining.append(order)
                continue

            fill = self._try_fill(order, bar)
            if fill is None:
                if order.status is OrderStatus.REJECTED:
                    # 已拒单的不再挂回队列。否则它会在下一根 bar 被再次撮合、
                    # 甚至成交 —— 对外已报「拒绝」，账上却成交了。
                    continue
                remaining.append(order)
                continue

            new_fills.append(fill)
            self._fills.append(fill)
            self._track_trade(fill)
            order.filled_price = fill.price
            order.filled_at = fill.filled_at
            order.commission += fill.commission

            if order.remaining_qty > 0:
                # 仍有残量：把订单量重置为残量后重新挂回队列
                order.qty = order.remaining_qty
                order.remaining_qty = 0
                order.status = OrderStatus.PARTIAL
                remaining.append(order)
            else:
                order.status = OrderStatus.FILLED

        self._pending = remaining
        return new_fills

    def add_cash(self, amount: float, reason: str = "") -> float:
        """
        直接增减现金（K8 分红现金流入 / 其他非成交现金事件）。
        返回调整后的现金余额。
        """
        if amount < 0 and -amount > self._cash:
            raise ValueError(
                f"现金不足以扣减 {abs(amount):.2f}（当前 {self._cash:.2f}）{reason}"
            )
        self._cash += amount
        self._external_cash_flow += amount
        return self._cash

    # ── TimeInForce ──────────────────────────────────────────

    def _expire_orders(self, bar_date: date) -> None:
        """按 TimeInForce 与 L5 分钟级超时撤销过期挂单（撤单不产生 Fill）。"""
        if all(
            o.time_in_force is TimeInForce.GTC and o._expire_at is None
            for o in self._pending
        ):
            return   # 默认全 GTC 且无超时：零开销快速路径

        remaining: list[Order] = []
        for order in self._pending:
            if order._submit_date is None:
                # 在任何 bar 之前挂出的订单：以首次见到的 bar 日期为起算日
                order._submit_date = bar_date
            reason = self._expiry_reason(order, bar_date)
            if reason is not None:
                order.status = OrderStatus.CANCELLED
                order.reject_reason = reason
            else:
                remaining.append(order)
        self._pending = remaining

    def _expiry_reason(self, order: Order, bar_date: date) -> str | None:
        """
        订单是否已过期，过期则返回撤单原因文案。

        TimeInForce（按自然日）与 L5 的 `*_timeout_minutes`（按分钟）是**并存**关系，
        两者都检查，**先到者生效** —— 等价于「同时配置时取更早者」。
        """
        if self._is_timed_out(order):
            return f"挂单超时（{order._expire_at:%Y-%m-%d %H:%M}）未成交，已撤单"
        if self._is_expired(order, bar_date):
            return f"{order.time_in_force.value} 订单过期未成交，已撤单"
        return None

    def _is_timed_out(self, order: Order) -> bool:
        """分钟级超时判定，时间锚点是最近一根已处理 bar。"""
        if order._expire_at is None or self._last_bar_time is None:
            return False
        return self._last_bar_time >= order._expire_at

    @staticmethod
    def _is_expired(order: Order, bar_date: date) -> bool:
        if order.time_in_force is TimeInForce.DAY:
            return order._submit_date is not None and bar_date > order._submit_date
        if order.time_in_force is TimeInForce.GTD and order.good_till_date is not None:
            return bar_date > order.good_till_date.date()
        return False

    # ── 融券费 ───────────────────────────────────────────────

    def _accrue_borrow_fee(self, ts: datetime) -> None:
        """按自然日计提融券费（默认关闭：allow_short=False 或费率为 0 时零开销）。"""
        if not self._allow_short or self._short_borrow_rate <= 0:
            return
        if self._last_bar_time is None:
            return
        elapsed_days = (ts - self._last_bar_time).total_seconds() / 86400.0
        if elapsed_days <= 0:
            return
        notional = self._positions.total_short_notional()
        if notional <= 0:
            return
        fee = notional * self._short_borrow_rate * elapsed_days / DAYS_PER_YEAR
        self._cash -= fee
        self._external_cash_flow -= fee

    def _check_price_limit(self, bar: Bar) -> tuple[float | None, float | None]:
        """
        A股涨跌停限制：根据前收价计算涨停价和跌停价。
        返回 (limit_up, limit_down) 或 (None, None) 若无法判断。

        简化版本：使用 bar.prev_close 字段（若有），否则跳过检查。
        参考: refs/rqalpha/ 价格限制逻辑
        """
        if not self._is_a_share or not hasattr(bar, "prev_close") or bar.prev_close is None:
            return None, None
        prev_close = bar.prev_close
        limit_up   = round(prev_close * (1 + self.A_PRICE_LIMIT_PCT), 2)
        limit_down = round(prev_close * (1 - self.A_PRICE_LIMIT_PCT), 2)
        return limit_up, limit_down

    def _resolve_match(self, order: Order, bar: Bar) -> MatchResult:
        """按订单类型判定本根 bar 是否成交，并维护 TRAILING_STOP 的跟踪极值。"""
        is_buy = order.side == OrderSide.BUY
        stop_price = order.stop_price

        if order.order_type is OrderType.TRAILING_STOP:
            # 用开盘价播种极值：开盘价在 bar 开始时已知，不构成偷看
            order._extreme_price = seed_trailing_extreme(order._extreme_price, bar.open)
            stop_price = trailing_trigger_price(
                order._extreme_price, order.trailing_pct or 0.0, is_buy
            )

        result = match_order(
            order.order_type,
            is_buy=is_buy,
            bar=bar,
            limit_price=order.limit_price,
            stop_price=stop_price,
            trigger_price=order.trigger_price,
            triggered=order._triggered,
        )
        order._triggered = result.triggered

        if not result.filled and order.order_type is OrderType.TRAILING_STOP:
            # 判定完成之后才用本根 bar 的极值刷新跟踪点
            order._extreme_price = update_trailing_extreme(
                order._extreme_price or bar.open, bar, is_buy
            )
        return result

    def _reject_on_price_limit(self, order: Order, bar: Bar, fill_price: float) -> bool:
        """A股涨跌停检查：成交价超出涨跌停则拒单。返回 True 表示已拒。"""
        limit_up, limit_down = self._check_price_limit(bar)
        if limit_up is None or limit_down is None:
            return False
        if fill_price > limit_up + 0.001:
            order.status = OrderStatus.REJECTED
            order.reject_reason = f"涨停无法成交: 开盘 {fill_price:.2f} > 涨停 {limit_up:.2f}"
            return True
        if fill_price < limit_down - 0.001:
            order.status = OrderStatus.REJECTED
            order.reject_reason = f"跌停无法成交: 开盘 {fill_price:.2f} < 跌停 {limit_down:.2f}"
            return True
        return False

    def _volume_capped_qty(self, order: Order, bar: Bar) -> int | None:
        """
        应用滑点模型的成交量上限，返回本根 bar 可成交数量。
        返回 None 表示本 bar 无可成交量（订单保持挂单，等下一根 bar）。

        默认滑点模型不限量 → 恒等于 `order.qty`，既有行为不受影响。
        """
        limit = self._slippage.fill_limit(order.qty, bar)
        capped = min(order.qty, max(limit.max_qty, 0))
        if capped <= 0:
            order.reject_reason = limit.reason or "成交量约束: 本根 bar 无可成交量"
            return None
        return capped

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        # 顺序：① 按订单类型判定是否成交并定出价格基准（K3）
        #      ② 按成交量上限定量（K6）
        #      ③ 按实际成交量算市场冲击滑点
        match = self._resolve_match(order, bar)
        if not match.filled or match.price is None:
            return None

        requested_qty = order.qty          # 本次撮合前的委托量，用于算残量
        capped_qty = self._volume_capped_qty(order, bar)
        if capped_qty is None:
            return None
        volume_shortfall = requested_qty - capped_qty

        # 必须用 apply_qty：apply() 不知道成交量，只能按 volume_limit 上限估冲击，
        # 小单会被严重高估（占比平方形式下，1% 的单按 2.5% 上限算要高估 6.25 倍）。
        fill_price = self._slippage.apply_qty(
            match.price, order.side.value, bar, capped_qty
        )

        if self._is_a_share and self._reject_on_price_limit(order, bar, fill_price):
            order.qty = requested_qty
            return None

        # 落账辅助以 order.qty 为准，先把它压到本根 bar 的可成交量
        order.qty = capped_qty
        total_cost = self._commission.calculate(fill_price, order.qty, order.side.value).total

        if order.side == OrderSide.BUY:
            settled = self._settle_buy_cash(order, fill_price, total_cost)
            if settled is None:
                order.qty = requested_qty
                return None
            total_cost = settled
            realized_pnl, direction, is_close = self._apply_buy(order, fill_price, total_cost)
        else:
            # 部分成交的残单可能撞上持仓已被别处消耗。仅在禁止做空时守卫：
            # 允许做空时超出持仓的部分是「开空」，由 _apply_sell 正常处理。
            if not self._allow_short:
                held = self._positions.get(order.symbol).qty
                if order.qty > held:
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = f"持仓不足: 需卖 {order.qty}，实际持有 {held}"
                    order.qty = requested_qty
                    return None
            self._cash += fill_price * order.qty - total_cost
            realized_pnl, direction, is_close = self._apply_sell(order, fill_price, total_cost)

        # 记账。注意 `_settle_buy_cash` 在现金不足时会把 order.qty 再压低一档，
        # 所以残量必须用**最终**成交量算，否则被现金砍掉的股数会既不成交、
        # 也不重新挂单、也不拒单，凭空消失。
        cash_shortfall = capped_qty - order.qty
        order.filled_qty += order.qty
        order.remaining_qty = volume_shortfall
        if cash_shortfall > 0:
            # 现金不足的部分按既有语义作废（不留残单），但必须留痕而非静默丢弃
            order.reject_reason = (
                f"现金不足，{cash_shortfall} 股未成交且不重新挂单"
                f"（委托 {requested_qty}，成交 {order.qty}，残单 {volume_shortfall}）"
            )

        # 平仓成交标注退出原因（信息不足时统一记 'signal'），开仓成交带上标签。
        # 做空前「SELL 必为平仓、BUY 必为开仓」，这里的写法对纯多头逐笔等价。
        exit_reason = (order.exit_reason or "signal") if is_close else None
        entry_tag = None if is_close else order.entry_tag

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            market=order.market,
            side=order.side,
            qty=order.qty,
            price=fill_price,
            commission=total_cost,
            filled_at=bar.time,
            realized_pnl=realized_pnl,
            entry_tag=entry_tag,
            exit_reason=exit_reason,
            direction=direction,
            is_close=is_close,
        )

    def _settle_buy_cash(
        self, order: Order, fill_price: float, total_cost: float
    ) -> float | None:
        """扣减买入现金；现金不足时缩量。返回最终费用，None 表示拒单。"""
        total_outflow = fill_price * order.qty + total_cost
        if total_outflow > self._cash:
            # 现金不足：调整股数
            max_qty = int(self._cash / (fill_price * (1 + 0.005)))  # 留 0.5% 余量
            if max_qty <= 0:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "insufficient cash"
                return None
            order.qty = max_qty
            total_cost = self._commission.calculate(fill_price, order.qty, order.side.value).total
            total_outflow = fill_price * order.qty + total_cost

        self._cash -= total_outflow
        return total_cost

    def _apply_buy(
        self, order: Order, fill_price: float, total_cost: float
    ) -> tuple[float, str, bool]:
        """BUY 落账：优先平空，剩余部分开多。返回 (已实现盈亏, 方向, 是否平仓)。"""
        if not self._allow_short:
            # A股买入标记为 T+1 不可当日卖出
            self._positions.buy(
                order.symbol, order.qty, fill_price, total_cost,
                t_plus=self._is_a_share,
            )
            return 0.0, "long", False

        cover_qty = min(order.qty, self._positions.get(order.symbol).short_qty)
        open_qty = order.qty - cover_qty
        cover_cost = total_cost if open_qty == 0 else total_cost * cover_qty / order.qty

        realized_pnl = 0.0
        if cover_qty > 0:
            realized_pnl = self._positions.cover(
                order.symbol, cover_qty, fill_price, cover_cost
            )
        if open_qty > 0:
            self._positions.buy(
                order.symbol, open_qty, fill_price, total_cost - cover_cost,
                t_plus=self._is_a_share,
            )
        return realized_pnl, ("short" if cover_qty > 0 else "long"), cover_qty > 0

    def _apply_sell(
        self, order: Order, fill_price: float, total_cost: float
    ) -> tuple[float, str, bool]:
        """SELL 落账：优先平多，剩余部分开空。返回 (已实现盈亏, 方向, 是否平仓)。"""
        if not self._allow_short:
            # 未开放做空时保持原语义：卖超持仓直接抛错，绝不静默转成空头
            realized = self._positions.sell(order.symbol, order.qty, fill_price, total_cost)
            return realized, "long", True

        close_qty = min(order.qty, self._positions.get(order.symbol).long_qty)
        open_qty = order.qty - close_qty
        close_cost = total_cost if open_qty == 0 else total_cost * close_qty / order.qty

        realized_pnl = 0.0
        if close_qty > 0:
            realized_pnl = self._positions.sell(
                order.symbol, close_qty, fill_price, close_cost
            )
        if open_qty > 0:
            self._positions.short(
                order.symbol, open_qty, fill_price, total_cost - close_cost
            )
        return realized_pnl, ("long" if close_qty > 0 else "short"), close_qty > 0

    def cancel_all_pending(self) -> int:
        count = len(self._pending)
        for order in self._pending:
            order.status = OrderStatus.CANCELLED
        self._pending = []
        return count

    def snapshot(self, prices: dict[str, float]) -> dict:
        return {
            "cash": round(self._cash, 2),
            "portfolio_value": round(self.portfolio_value(prices), 2),
            "total_return_pct": round(
                (self.portfolio_value(prices) - self._initial_cash) / self._initial_cash * 100, 4
            ),
            "positions": self._positions.snapshot(prices),
            "pending_orders": len(self._pending),
            "total_fills": len(self._fills),
        }
