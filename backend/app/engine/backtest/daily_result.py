"""逐合约 / 组合日度盈亏拆解（Wave K-c / K1）

设计移植自 vnpy（MIT License, Copyright (c) vnpy.com）
`vnpy/alpha/strategy/backtesting.py` 的 ContractDailyResult / PortfolioDailyResult，
把 polars 表达式改写为原生 dataclass + 纯 Python 累加。

拆解口径（对每个标的、每个会话日）::

    holding_pnl = 昨收持仓 × (今收 - 昨收)
    trading_pnl = Σ 当日每笔成交 (仓位变动 × (今收 - 成交价))
    total_pnl   = holding_pnl + trading_pnl
    net_pnl     = total_pnl - commission - slippage + other_cash_flow

`other_cash_flow` 是**非成交现金流**（K8 除权分红入账、K2 融券费计提）。
少了它，恒等式在开启分红或做空时就会悄悄对不上 —— 净值涨了但日结不认账。
它只在组合层记账：融券费按整个账户的空头名义金额计提，无法可靠拆到单个标的。

补上之后，下面这条恒等式在任何配置下都精确成立::

    净值变动 = 现金变动 + 持仓市值变动 = net_pnl

因此它直接支撑 V4 的 N2 换手率与 N4 进出场归因。

**滑点口径说明**：本引擎的滑点被折进成交价（`SlippageModel.apply`）而非单独记账，
无法从 Fill 反解出「无滑点基准价」，故 `slippage` 恒为 0.0，滑点成本已体现在
`trading_pnl` 中。字段保留是为了与 vnpy 的拆解结构对齐，便于后续接入显式滑点记账。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from app.engine.backtest.broker import Fill, OrderSide

#: 股票 1 手 = 1 股；期货合约乘数留待 A 期扩展，这里显式命名避免魔法数字
CONTRACT_SIZE = 1.0


@dataclass(frozen=True)
class ContractDailyResult:
    """单标的单日盈亏拆解。"""

    date: date
    symbol: str
    close_price: float
    pre_close: float
    trades: tuple[Fill, ...]
    trade_count: int
    start_pos: int
    end_pos: int
    turnover: float
    commission: float
    slippage: float
    trading_pnl: float
    holding_pnl: float
    total_pnl: float
    net_pnl: float


@dataclass(frozen=True)
class PortfolioDailyResult:
    """组合单日盈亏 = 当日各 ContractDailyResult 的加总 + 组合级非成交现金流。"""

    date: date
    trade_count: int
    turnover: float
    commission: float
    slippage: float
    trading_pnl: float
    holding_pnl: float
    total_pnl: float
    net_pnl: float
    #: 当日非成交现金流（分红为正、融券费为负），只在组合层记账
    other_cash_flow: float = 0.0
    contracts: Mapping[str, ContractDailyResult] = field(default_factory=dict)


def position_change(fill: Fill) -> int:
    """成交引起的带符号仓位变动：BUY 为正，SELL 为负。"""
    return fill.qty if fill.side == OrderSide.BUY else -fill.qty


def build_contract_daily_result(
    symbol: str,
    day: date,
    close_price: float,
    pre_close: float,
    start_pos: int,
    fills: Iterable[Fill],
    size: float = CONTRACT_SIZE,
) -> ContractDailyResult:
    """按 vnpy 口径拆解单标的单日盈亏。"""
    trades = tuple(fills)

    holding_pnl = start_pos * (close_price - pre_close) * size

    trading_pnl = 0.0
    turnover = 0.0
    commission = 0.0
    net_change = 0
    for fill in trades:
        change = position_change(fill)
        trading_pnl += change * (close_price - fill.price) * size
        turnover += fill.price * fill.qty * size
        commission += fill.commission
        net_change += change

    total_pnl = trading_pnl + holding_pnl
    slippage = 0.0   # 见模块 docstring：滑点已折进成交价

    return ContractDailyResult(
        date=day,
        symbol=symbol,
        close_price=close_price,
        pre_close=pre_close,
        trades=trades,
        trade_count=len(trades),
        start_pos=start_pos,
        end_pos=start_pos + net_change,
        turnover=turnover,
        commission=commission,
        slippage=slippage,
        trading_pnl=trading_pnl,
        holding_pnl=holding_pnl,
        total_pnl=total_pnl,
        net_pnl=total_pnl - commission - slippage,
    )


def build_portfolio_daily_result(
    day: date,
    contracts: Iterable[ContractDailyResult],
    other_cash_flow: float = 0.0,
) -> PortfolioDailyResult:
    """把当日各标的的拆解结果加总为组合日结，并计入非成交现金流。"""
    by_symbol = {c.symbol: c for c in contracts}
    values = by_symbol.values()

    trading_pnl = sum(c.trading_pnl for c in values)
    holding_pnl = sum(c.holding_pnl for c in values)
    commission = sum(c.commission for c in values)
    slippage = sum(c.slippage for c in values)
    total_pnl = trading_pnl + holding_pnl

    return PortfolioDailyResult(
        date=day,
        trade_count=sum(c.trade_count for c in values),
        turnover=sum(c.turnover for c in values),
        commission=commission,
        slippage=slippage,
        trading_pnl=trading_pnl,
        holding_pnl=holding_pnl,
        total_pnl=total_pnl,
        net_pnl=total_pnl - commission - slippage + other_cash_flow,
        other_cash_flow=other_cash_flow,
        contracts=by_symbol,
    )


class DailyPnlTracker:
    """
    按会话日累计逐合约盈亏。

    引擎每个时点的调用顺序必须是::

        tracker.begin_day(ts, 撮合前的持仓快照)   # 仅跨日时真正生效
        fills = broker.process_bars(...)
        tracker.record(fills, 最后已知价, 本时点非成交现金流)

    `begin_day` 只在跨日那一次物化持仓快照，因此 50 标的 × 750 时点也只有 750 次。
    """

    def __init__(self) -> None:
        self._pre_close: dict[str, float] = {}
        self._close: dict[str, float] = {}
        self._day: date | None = None
        self._start_pos: dict[str, int] = {}
        self._day_fills: dict[str, list[Fill]] = {}
        self._day_cash_flow: float = 0.0
        self._results: list[PortfolioDailyResult] = []

    def begin_day(self, ts: datetime, positions: Mapping[str, int]) -> bool:
        """
        推进到 ts 所在会话日；跨日时结算上一日。
        positions 必须是**本时点撮合之前**的带符号持仓。返回是否开启了新的一天。
        """
        day = ts.date()
        if self._day == day:
            return False
        if self._day is not None:
            self._flush()
        self._day = day
        self._start_pos = dict(positions)
        self._day_fills = {}
        self._day_cash_flow = 0.0
        return True

    def record(
        self,
        fills: Iterable[Fill],
        closes: Mapping[str, float],
        cash_flow: float = 0.0,
    ) -> None:
        """
        登记本时点的成交、收盘价与非成交现金流。

        closes 应为**最后已知价**（停牌标的沿用旧价）；
        cash_flow 是本时点的分红入账 / 融券费计提净额。
        """
        if self._day is None:
            raise RuntimeError("record() 必须在 begin_day() 之后调用")
        for fill in fills:
            self._day_fills.setdefault(fill.symbol, []).append(fill)
        self._close.update(closes)
        self._day_cash_flow += cash_flow

    def finish(self) -> list[PortfolioDailyResult]:
        """结束累计并返回全部日结（可重复调用）。"""
        if self._day is not None:
            self._flush()
            self._day = None
        return self._results

    # ── 内部 ─────────────────────────────────────────────────

    def _flush(self) -> None:
        """把当前累计的一天结算成 PortfolioDailyResult。"""
        assert self._day is not None
        active = {
            symbol
            for symbol in set(self._start_pos) | set(self._day_fills)
            if self._start_pos.get(symbol, 0) != 0 or self._day_fills.get(symbol)
        }

        contracts = [
            build_contract_daily_result(
                symbol=symbol,
                day=self._day,
                close_price=self._close.get(symbol, 0.0),
                pre_close=self._pre_close.get(symbol, self._close.get(symbol, 0.0)),
                start_pos=self._start_pos.get(symbol, 0),
                fills=self._day_fills.get(symbol, []),
            )
            for symbol in sorted(active)
        ]
        self._results.append(
            build_portfolio_daily_result(self._day, contracts, self._day_cash_flow)
        )
        self._pre_close = dict(self._close)
