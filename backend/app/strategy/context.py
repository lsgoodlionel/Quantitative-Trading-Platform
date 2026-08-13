"""
策略上下文

每次 on_bar() 调用时，引擎将当前状态封装为 StrategyContext 传入策略。
策略通过 context 下单、查询持仓、获取历史数据。

支持两种模式:
- 回测模式: broker = SimulatedBroker（内存模拟）
- 实盘模式: broker = None, live_order_ctx = LiveOrderContext（路由到 OMS）
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from app.data.models import Bar, Market
from app.engine.backtest.broker import Order, SimulatedBroker
from app.engine.backtest.order_types import OrderType
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.position import Position
from app.engine.portfolio.rebalance import plan_rebalance

if TYPE_CHECKING:
    from app.engine.backtest.trade import Trade
    from app.strategy.engine import LiveOrderContext

logger = logging.getLogger(__name__)

#: 目标权重总敞口的容差（浮点求和误差 + 手续费预留）
_WEIGHT_TOLERANCE = 1e-6


def _backtest_order_kwargs(order_type: str, limit_price: float | None) -> dict:
    """
    把策略侧的字符串 order_type 翻译成回测券商的 Order 字段。

    默认 "MARKET" 时返回空 dict —— 走的仍是券商的默认市价路径，
    与订单类型体系上线前逐笔一致。未知类型直接抛错，不静默降级成市价单。
    """
    if order_type == OrderType.MARKET.value:
        return {}
    try:
        resolved = OrderType(order_type)
    except ValueError as exc:
        raise ValueError(f"未知的订单类型: {order_type}") from exc
    kwargs: dict = {"order_type": resolved}
    if limit_price is not None:
        kwargs["limit_price"] = limit_price
    return kwargs


@dataclass
class StrategyContext:
    """
    传给 on_bar() 的上下文快照。

    bar            — 当前这根 K 线数据
    history        — 包含当前 bar 在内的所有历史 bar（DataFrame）
    broker         — 回测模式：模拟券商；实盘模式：None
    live_order_ctx — 实盘模式：LiveOrderContext，负责将信号路由到 OMS
    """

    bar: Bar
    history: pd.DataFrame
    broker: SimulatedBroker | None
    live_order_ctx: LiveOrderContext | None = field(default=None)

    # ── 模式判断 ─────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        """True = 实盘/模拟盘模式，False = 回测模式。"""
        return self.broker is None

    # ── 账户状态（快捷访问）─────────────────────────────────

    @property
    def cash(self) -> float:
        if self.broker is None:
            return float("inf")   # 实盘模式不限制（由 OMS 负责）
        return self.broker.cash

    @property
    def current_prices(self) -> dict[str, float]:
        return {self.bar.symbol: self.bar.close}

    @property
    def portfolio_value(self) -> float:
        if self.broker is None:
            return 0.0
        return self.broker.portfolio_value(self.current_prices)

    def position(self, symbol: str | None = None) -> Position | None:
        """返回指定标的的持仓，实盘模式返回 None（由 OMS 管理）。"""
        if self.broker is None:
            return None
        sym = symbol or self.bar.symbol
        return self.broker.positions.get(sym)

    @property
    def qty(self) -> int:
        """当前 bar 标的的持仓数量（快捷方式）。实盘模式返回 0。"""
        pos = self.position()
        return pos.qty if pos else 0

    # ── 下单接口 ─────────────────────────────────────────────

    def buy(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> Order | None:
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.buy(qty, order_type=order_type, limit_price=limit_price)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        kwargs = _backtest_order_kwargs(order_type, limit_price)
        return self.broker.buy(sym, qty, mkt, **kwargs)  # type: ignore[union-attr]

    def sell(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        exit_reason: str | None = None,
    ) -> Order | None:
        """
        卖出。`exit_reason` 用于给平仓 Fill 打分组标签（默认 None = 券商记 'signal'），
        策略自己做分批止盈时可传 `partial_exit` 与 L4 的调整循环对齐口径。
        """
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.sell(qty, order_type=order_type, limit_price=limit_price)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        kwargs = _backtest_order_kwargs(order_type, limit_price)
        return self.broker.sell(  # type: ignore[union-attr]
            sym, qty, mkt, exit_reason=exit_reason, **kwargs
        )

    def buy_value(self, value: float, symbol: str | None = None) -> Order | None:
        """按金额买入，自动计算股数（向下取整）。"""
        price = self.bar.close
        if price <= 0:
            return None
        qty = int(value / price)
        if qty <= 0:
            return None
        return self.buy(qty, symbol)

    def sell_all(self, symbol: str | None = None) -> Order | None:
        """清空指定标的的全部**多头**持仓（语义保持不变，不触碰空头）。"""
        if self.is_live:
            # 实盘模式：发出足够大的卖出信号，由 OMS 处理数量
            if self.live_order_ctx:
                self.live_order_ctx.sell(9999)
            return None
        sym = symbol or self.bar.symbol
        pos = self.broker.positions.get(sym)  # type: ignore[union-attr]
        if not pos or pos.qty <= 0:
            return None
        return self.sell(pos.qty, sym)

    # ── 做空接口 (K2) ────────────────────────────────────────

    def short(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
    ) -> Order | None:
        """开空：需要回测配置 allow_short=True，否则券商会拒单。"""
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.sell(qty)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.short(sym, qty, mkt)  # type: ignore[union-attr]

    def cover(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
        exit_reason: str | None = None,
    ) -> Order | None:
        """平空：买回股票归还券源。"""
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.buy(qty)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.cover(sym, qty, mkt, exit_reason=exit_reason)  # type: ignore[union-attr]

    def close_all(self, symbol: str | None = None) -> Order | None:
        """平掉任意方向的持仓（sell_all 的双向版）。"""
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.sell(9999)
            return None
        sym = symbol or self.bar.symbol
        pos = self.broker.positions.get(sym)  # type: ignore[union-attr]
        if not pos or pos.qty == 0:
            return None
        if pos.qty > 0:
            return self.sell(pos.qty, sym)
        return self.cover(-pos.qty, sym)

    # ── 历史数据访问 ─────────────────────────────────────────

    def close_series(self, n: int | None = None) -> pd.Series:
        """返回收盘价序列，n 为取最近 n 根（默认全部）。"""
        closes = self.history["close"]
        return closes.iloc[-n:] if n else closes

    def volume_series(self, n: int | None = None) -> pd.Series:
        volumes = self.history["volume"]
        return volumes.iloc[-n:] if n else volumes


@dataclass
class PortfolioContext:
    """
    传给 `PortfolioStrategyBase.on_bars()` 的组合上下文（Wave K-c / K1）。

    与 `StrategyContext` 的关键差异：
    - `bars` 是**当前时点有行情的标的**，停牌标的不在其中（但持仓仍然保留并估值）
    - `histories` 是惰性视图：只有策略真的取某个标的的历史时才物化 DataFrame
    - 所有下单接口的第一个参数都是 symbol（组合下没有「当前标的」这一概念）
    """

    time: datetime
    bars: dict[str, Bar]
    symbols: list[str]
    broker: PortfolioBroker
    histories: Mapping[str, pd.DataFrame]
    market: Market
    #: 每仓固定金额（PortfolioBacktestConfig.cash_per_position），None = 由策略自行决定
    cash_per_position: float | None = None

    # ── 账户状态 ─────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self.broker.cash

    @property
    def current_prices(self) -> dict[str, float]:
        """全体标的的估值价（停牌标的沿用最后已知价）。"""
        return self.broker.mark_prices()

    @property
    def portfolio_value(self) -> float:
        return self.broker.portfolio_value()

    def position(self, symbol: str) -> Position | None:
        pos = self.broker.positions.get(symbol)
        return None if pos.is_empty else pos

    def qty(self, symbol: str) -> int:
        """带符号净持仓（无持仓为 0）。"""
        return self.broker.positions.get(symbol).qty

    @property
    def open_symbols(self) -> list[str]:
        """
        当前有持仓（多或空）的标的。

        框架层（组合构建、风控、执行）要「我现在持有哪些标的」一律走这里，
        不要下探 `ctx.broker.positions` —— 上下文是回测/实盘唯一的公共接口，
        实盘装的是账户快照的投影而不是回测券商，穿透 broker 的代码在实盘会
        撞上一条 AttributeError。
        """
        return self.broker.positions.open_symbols

    @property
    def open_trades(self) -> dict[str, Trade]:
        """
        各标的的「本笔交易」（由 K4 的 `RiskExitMixin` 在回测券商上维护）。

        券商不提供该能力时（例如实盘的账户快照）返回空表，让基于 `Trade` 的
        消费方退化为 no-op —— 而不是拿一个错误的基准去平仓。
        """
        trades = getattr(self.broker, "open_trades", None)
        if trades is None:
            logger.debug("券商未提供 open_trades，基于 Trade 的模型本次为 no-op")
            return {}
        return trades

    def bar(self, symbol: str) -> Bar | None:
        return self.bars.get(symbol)

    def price(self, symbol: str) -> float | None:
        """估值价：本时点有 bar 用收盘价，否则用最后已知价。"""
        bar = self.bars.get(symbol)
        if bar is not None:
            return bar.close
        return self.broker.last_known_price(symbol)

    # ── 下单接口 ─────────────────────────────────────────────

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
        kwargs = _backtest_order_kwargs(order_type, limit_price)
        return self.broker.buy(
            symbol, qty, self._market_of(symbol, market), entry_tag=entry_tag, **kwargs
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
        kwargs = _backtest_order_kwargs(order_type, limit_price)
        return self.broker.sell(
            symbol, qty, self._market_of(symbol, market), exit_reason=exit_reason, **kwargs
        )

    def short(self, symbol: str, qty: int, market: Market | None = None) -> Order | None:
        if qty <= 0:
            return None
        return self.broker.short(symbol, qty, self._market_of(symbol, market))

    def cover(
        self, symbol: str, qty: int, market: Market | None = None,
        exit_reason: str | None = None,
    ) -> Order | None:
        if qty <= 0:
            return None
        return self.broker.cover(
            symbol, qty, self._market_of(symbol, market), exit_reason=exit_reason
        )

    def submit(self, order: Order) -> Order:
        """
        直接提交一张已构造好的订单（K5 执行模型的出口）。

        与 `buy/sell` 的分工：那两个是给策略手写用的便捷入口，这个是给
        `ExecutionModel` 用的 —— 执行模型已经算好了方向与数量，不该再被
        便捷入口的语义（比如 `qty<=0` 就返回 None）二次加工。
        实盘下由 `StrategyEngine` 提供等价实现，路由到 OMS。
        """
        return self.broker.submit_order(order)

    def market_of(self, symbol: str) -> Market:
        """标的所属市场：优先取本时点 bar 的市场，其次回退到回测配置。"""
        return self._market_of(symbol, None)

    def buy_value(self, symbol: str, value: float | None = None) -> Order | None:
        """按金额买入（向下取整到整股）。value 缺省时用配置的 cash_per_position。"""
        budget = value if value is not None else self.cash_per_position
        if budget is None:
            raise ValueError(
                "buy_value 需要显式金额，或在 PortfolioBacktestConfig 中配置 cash_per_position"
            )
        price = self.price(symbol)
        if price is None or price <= 0:
            return None
        return self.buy(symbol, int(budget / price))

    def close_all(self, symbol: str, exit_reason: str | None = None) -> Order | None:
        """平掉任意方向的持仓。"""
        held = self.qty(symbol)
        if held == 0:
            return None
        if held > 0:
            return self.sell(symbol, held, exit_reason=exit_reason)
        return self.cover(symbol, -held, exit_reason=exit_reason)

    # ── 目标权重调仓 (K1) ────────────────────────────────────

    def target_weight(self, weights: dict[str, float]) -> list[Order]:
        """
        目标权重 → **增量 diff 订单**。

        目标股数 = 组合净值 × 权重 / 估值价（向零取整），委托量 = 目标 - 当前持仓。
        已有 100 股、目标 150 股 ⇒ 买 50 股，而不是买 150 股。

        权重可为负（做空），但需要 `allow_short=True`，否则直接抛错而非静默截断。
        产出顺序固定为**先卖后买**，让释放的现金能被同一时点的买单用上。

        算法本体在 `app.engine.portfolio.rebalance.plan_rebalance` —— 回测与实盘
        再平衡 API 共用同一份取整与排序规则，避免两条路径各自漂移（V3 A-b）。
        """
        self._validate_weights(weights)

        legs = plan_rebalance(
            target_weights=weights,
            current_qty={symbol: self.qty(symbol) for symbol in weights},
            prices=self._price_snapshot(weights),
            portfolio_value=self.portfolio_value,
        )

        orders = [
            self.buy(leg.symbol, leg.delta_qty)
            if leg.delta_qty > 0
            else self.sell(leg.symbol, -leg.delta_qty)
            for leg in legs
        ]
        return [o for o in orders if o is not None]

    def _price_snapshot(self, symbols: Mapping[str, float]) -> dict[str, float]:
        """本时点各标的估值价；无价的标的不入字典，由 plan_rebalance 跳过。"""
        snapshot: dict[str, float] = {}
        for symbol in symbols:
            price = self.price(symbol)
            if price is not None:
                snapshot[symbol] = price
        return snapshot

    def _validate_weights(self, weights: dict[str, float]) -> None:
        """边界校验：未知标的、无授权的负权重、超杠杆。"""
        unknown = sorted(set(weights) - set(self.symbols))
        if unknown:
            raise ValueError(f"target_weight 收到未纳入回测的标的: {unknown}")

        if not self.broker.allow_short:
            negative = sorted(s for s, w in weights.items() if w < 0)
            if negative:
                raise ValueError(
                    f"target_weight 出现负权重 {negative}，但回测未开启 allow_short"
                )

        gross = sum(abs(w) for w in weights.values())
        if gross > 1.0 + _WEIGHT_TOLERANCE:
            logger.warning(
                "target_weight 总敞口 %.4f > 1，无保证金模型时超出部分会因现金不足被缩量",
                gross,
            )

    def _market_of(self, symbol: str, market: Market | None) -> Market:
        if market is not None:
            return market
        bar = self.bars.get(symbol)
        return bar.market if bar is not None else self.market

    # ── 历史数据访问 ─────────────────────────────────────────

    def history(self, symbol: str) -> pd.DataFrame:
        """截至当前时点的历史（含当前 bar）。惰性物化。"""
        return self.histories[symbol]

    def close_series(self, symbol: str, n: int | None = None) -> pd.Series:
        closes = self.histories[symbol]["close"]
        return closes.iloc[-n:] if n else closes

    def volume_series(self, symbol: str, n: int | None = None) -> pd.Series:
        volumes = self.histories[symbol]["volume"]
        return volumes.iloc[-n:] if n else volumes
