"""TradingControl 控制族单元测试 — Wave L-a / L1

覆盖 docs/contracts/waveLa-trading-controls.md §六 验收 2：
8 个控制器各自的放行/拦截边界、on_error 语义、多控制器组合短路、
LongOnly 与 allow_short 的正交性。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderStatus, SimulatedBroker
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.order_types import OrderType
from app.engine.backtest.slippage import NoSlippage
from app.engine.controls import (
    AssetDateBounds,
    ControlContext,
    LongOnly,
    MaxLeverage,
    MaxOrderCount,
    MaxOrderSize,
    MaxPositionSize,
    MinLeverage,
    RestrictedList,
    TradingControlViolationError,
    run_controls,
)

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)


def _ctx(**overrides) -> ControlContext:
    """构造一个「一切正常」的基线 context，测试只覆写自己关心的字段。"""
    defaults = {
        "symbol": "AAPL",
        "market": Market.US,
        "side": "BUY",
        "qty": 100,
        "order_type": OrderType.MARKET,
        "price": 100.0,
        "now": BASE_TIME,
        "current_qty": 0,
        "portfolio_value": 100_000.0,
        "cash": 100_000.0,
        "orders_today": 0,
        "leverage": 0.0,
    }
    return ControlContext(**{**defaults, **overrides})


def _bar(
    open_: float = 100.0,
    high: float = 110.0,
    low: float = 90.0,
    close: float = 100.0,
    symbol: str = "AAPL",
    day_offset: int = 0,
) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day_offset),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
    )


def _broker(**kwargs) -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=1_000_000.0,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=NoSlippage(),
        **kwargs,
    )


# ── MaxOrderCount ────────────────────────────────────────────────


def test_max_order_count_allows_below_limit():
    # Arrange
    control = MaxOrderCount(max_count=3)

    # Act
    result = control.validate(_ctx(orders_today=2))

    # Assert
    assert result is None


def test_max_order_count_blocks_at_limit():
    control = MaxOrderCount(max_count=3)

    result = control.validate(_ctx(orders_today=3))

    assert result is not None
    assert result.control == "MaxOrderCount"
    assert "3" in result.reason


def test_max_order_count_rejects_negative_max():
    with pytest.raises(ValueError, match="max_count"):
        MaxOrderCount(max_count=-1)


# ── MaxOrderSize ─────────────────────────────────────────────────


def test_max_order_size_allows_exactly_at_share_limit():
    control = MaxOrderSize(max_shares=100)

    assert control.validate(_ctx(qty=100)) is None


def test_max_order_size_blocks_above_share_limit():
    control = MaxOrderSize(max_shares=100)

    result = control.validate(_ctx(qty=101))

    assert result is not None
    assert result.control == "MaxOrderSize"


def test_max_order_size_blocks_above_notional_limit():
    control = MaxOrderSize(max_notional=10_000.0)

    # 101 股 × 100 元 = 10100 > 10000
    assert control.validate(_ctx(qty=100, price=100.0)) is None
    assert control.validate(_ctx(qty=101, price=100.0)) is not None


def test_max_order_size_falls_back_to_limit_price_when_no_quote():
    """无行情参考价时用委托价校验金额，而不是放弃校验。"""
    control = MaxOrderSize(max_notional=10_000.0)

    result = control.validate(
        _ctx(qty=200, price=None, limit_price=100.0, order_type=OrderType.LIMIT)
    )

    assert result is not None


def test_max_order_size_rejects_when_notional_unverifiable():
    """配了金额上限却完全拿不到价格 → 拒单，绝不静默放行。"""
    control = MaxOrderSize(max_notional=10_000.0)

    result = control.validate(_ctx(qty=1, price=None, limit_price=None))

    assert result is not None
    assert "缺少参考价" in result.reason


def test_max_order_size_requires_at_least_one_limit():
    with pytest.raises(ValueError, match="至少要给一个"):
        MaxOrderSize()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_shares": -1}, "max_shares 不能为负"),
        ({"max_notional": -1.0}, "max_notional 不能为负"),
    ],
)
def test_size_limits_reject_negative_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        MaxOrderSize(**kwargs)
    with pytest.raises(ValueError, match=message):
        MaxPositionSize(**kwargs)


def test_control_repr_shows_on_error_mode():
    assert repr(LongOnly(on_error="fail")) == "LongOnly(on_error='fail')"


# ── MaxPositionSize ──────────────────────────────────────────────


def test_max_position_size_counts_post_trade_position():
    control = MaxPositionSize(max_shares=500)

    assert control.validate(_ctx(qty=100, current_qty=400)) is None
    assert control.validate(_ctx(qty=101, current_qty=400)) is not None


def test_max_position_size_judges_result_not_direction():
    """
    口径是「成交后持仓」而非委托方向：减仓到限额以内放行，
    减仓后仍超限则照样拦（与 zipline 一致）。
    """
    control = MaxPositionSize(max_shares=100)

    assert control.validate(_ctx(side="SELL", qty=100, current_qty=150)) is None
    assert control.validate(_ctx(side="SELL", qty=100, current_qty=1000)) is not None


def test_max_position_size_uses_absolute_value_for_shorts():
    """空头持仓同样受上限约束（取绝对值）。"""
    control = MaxPositionSize(max_shares=100)

    result = control.validate(_ctx(side="SELL", qty=200, current_qty=0))

    assert result is not None


def test_max_position_size_notional_limit():
    control = MaxPositionSize(max_notional=50_000.0)

    assert control.validate(_ctx(qty=100, current_qty=400, price=100.0)) is None
    assert control.validate(_ctx(qty=101, current_qty=400, price=100.0)) is not None


# ── LongOnly ─────────────────────────────────────────────────────


def test_long_only_allows_buy_and_flat_close():
    control = LongOnly()

    assert control.validate(_ctx(side="BUY", qty=100, current_qty=0)) is None
    assert control.validate(_ctx(side="SELL", qty=100, current_qty=100)) is None


def test_long_only_blocks_short():
    control = LongOnly()

    result = control.validate(_ctx(side="SELL", qty=101, current_qty=100))

    assert result is not None
    assert result.control == "LongOnly"


# ── RestrictedList ───────────────────────────────────────────────


def test_restricted_list_blocks_listed_symbol():
    control = RestrictedList({"TSLA", "GME"})

    assert control.validate(_ctx(symbol="AAPL")) is None
    assert control.validate(_ctx(symbol="GME")) is not None


def test_restricted_list_copies_input_set():
    """外部事后改动传入集合不得影响控制器行为。"""
    mutable = {"TSLA"}
    control = RestrictedList(mutable)

    mutable.add("AAPL")

    assert control.validate(_ctx(symbol="AAPL")) is None


# ── AssetDateBounds ──────────────────────────────────────────────


def test_asset_date_bounds_blocks_outside_window():
    control = AssetDateBounds({"AAPL": (date(2024, 1, 1), date(2024, 12, 31))})

    assert control.validate(_ctx(now=datetime(2024, 6, 1, tzinfo=UTC))) is None

    before = control.validate(_ctx(now=datetime(2023, 12, 31, tzinfo=UTC)))
    after = control.validate(_ctx(now=datetime(2025, 1, 1, tzinfo=UTC)))

    assert before is not None
    assert "上市" in before.reason
    assert after is not None
    assert "退市" in after.reason


def test_asset_date_bounds_ignores_unregistered_symbol():
    control = AssetDateBounds({"AAPL": (date(2024, 1, 1), date(2024, 12, 31))})

    assert control.validate(_ctx(symbol="MSFT", now=BASE_TIME)) is None


def test_asset_date_bounds_rejects_inverted_window():
    with pytest.raises(ValueError, match="晚于"):
        AssetDateBounds({"AAPL": (date(2024, 12, 31), date(2024, 1, 1))})


# ── MaxLeverage / MinLeverage ────────────────────────────────────


def test_max_leverage_boundary():
    control = MaxLeverage(max_leverage=2.0)

    assert control.validate(_ctx(leverage=2.0)) is None
    assert control.validate(_ctx(leverage=2.01)) is not None


def test_min_leverage_only_applies_after_deadline():
    deadline = datetime(2024, 6, 1, tzinfo=UTC)
    control = MinLeverage(min_leverage=1.0, deadline=deadline)

    before = control.validate(_ctx(now=datetime(2024, 5, 1, tzinfo=UTC), leverage=0.1))
    after = control.validate(_ctx(now=datetime(2024, 7, 1, tzinfo=UTC), leverage=0.1))

    assert before is None
    assert after is not None


def test_min_leverage_allows_sufficient_leverage_after_deadline():
    control = MinLeverage(min_leverage=1.0, deadline=datetime(2024, 6, 1, tzinfo=UTC))

    result = control.validate(_ctx(now=datetime(2024, 7, 1, tzinfo=UTC), leverage=1.5))

    assert result is None


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: MaxLeverage(max_leverage=-1.0), "max_leverage 不能为负"),
        (
            lambda: MinLeverage(min_leverage=-1.0, deadline=BASE_TIME),
            "min_leverage 不能为负",
        ),
    ],
)
def test_leverage_controls_reject_negative_bounds(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def test_max_position_size_rejects_when_notional_unverifiable():
    """与 MaxOrderSize 同理：配了金额上限却无价可依时拒单。"""
    control = MaxPositionSize(max_notional=10_000.0)

    result = control.validate(_ctx(qty=1, price=None, limit_price=None))

    assert result is not None
    assert "缺少参考价" in result.reason


def test_min_leverage_tolerates_naive_deadline():
    """裸 datetime 的 deadline 与带时区的 bar 时间比较不得炸掉回测。"""
    control = MinLeverage(min_leverage=1.0, deadline=datetime(2024, 6, 1))

    result = control.validate(_ctx(now=datetime(2024, 7, 1, tzinfo=UTC), leverage=0.1))

    assert result is not None


# ── on_error 语义 ────────────────────────────────────────────────


def test_on_error_fail_raises():
    control = MaxOrderSize(max_shares=10, on_error="fail")

    with pytest.raises(TradingControlViolationError) as exc:
        run_controls([control], _ctx(qty=100))

    assert exc.value.violation.control == "MaxOrderSize"


def test_on_error_log_returns_violation():
    control = MaxOrderSize(max_shares=10, on_error="log")

    violation = run_controls([control], _ctx(qty=100))

    assert violation is not None
    assert violation.as_reject_reason().startswith("[MaxOrderSize]")


def test_invalid_on_error_rejected_at_construction():
    with pytest.raises(ValueError, match="on_error"):
        LongOnly(on_error="explode")


# ── 多控制器组合 ─────────────────────────────────────────────────


def test_controls_short_circuit_in_order():
    """首个命中即返回；后面的控制器不再执行。"""
    first = RestrictedList({"AAPL"})
    second = MaxOrderSize(max_shares=1)

    violation = run_controls([first, second], _ctx(symbol="AAPL", qty=100))

    assert violation is not None
    assert violation.control == "RestrictedList"


def test_controls_pass_through_when_all_allow():
    controls = [MaxOrderCount(max_count=10), MaxOrderSize(max_shares=1000), LongOnly()]

    assert run_controls(controls, _ctx()) is None


# ── 回测券商接入 ─────────────────────────────────────────────────


def test_broker_without_controls_behaves_as_before():
    broker = _broker()
    broker.process_bar(_bar())

    order = broker.buy("AAPL", 100)

    assert order.status is OrderStatus.PENDING
    assert order.reject_reason is None


def test_broker_rejects_order_hitting_control():
    broker = _broker(controls=[MaxOrderSize(max_shares=50)])
    broker.process_bar(_bar())

    order = broker.buy("AAPL", 100)

    assert order.status is OrderStatus.REJECTED
    assert order.reject_reason is not None
    assert order.reject_reason.startswith("[MaxOrderSize]")


def test_broker_control_fail_mode_aborts_backtest():
    broker = _broker(controls=[MaxOrderSize(max_shares=50, on_error="fail")])
    broker.process_bar(_bar())

    with pytest.raises(TradingControlViolationError):
        broker.buy("AAPL", 100)


def test_broker_max_order_count_resets_next_day():
    broker = _broker(controls=[MaxOrderCount(max_count=1)])
    broker.process_bar(_bar(day_offset=0))

    first = broker.buy("AAPL", 10)
    second = broker.buy("AAPL", 10)

    assert first.status is OrderStatus.PENDING
    assert second.status is OrderStatus.REJECTED

    broker.process_bar(_bar(day_offset=1))
    third = broker.buy("AAPL", 10)

    assert third.status is OrderStatus.PENDING


def test_broker_rejected_order_is_not_queued():
    """被控制器拒掉的单绝不能留在挂单队列里等下一根 bar 成交。"""
    broker = _broker(controls=[RestrictedList({"AAPL"})])
    broker.process_bar(_bar())

    broker.buy("AAPL", 100)
    fills = broker.process_bar(_bar(day_offset=1))

    assert fills == []


def test_long_only_is_orthogonal_to_allow_short():
    """allow_short=True 保留做空能力，LongOnly 仍能拦住开空 —— 两者正交。"""
    broker = _broker(allow_short=True, controls=[LongOnly()])
    broker.process_bar(_bar())

    opening_short = broker.short("AAPL", 100)

    assert opening_short.status is OrderStatus.REJECTED
    assert opening_short.reject_reason is not None
    assert opening_short.reject_reason.startswith("[LongOnly]")


def test_allow_short_without_long_only_still_permits_shorting():
    broker = _broker(allow_short=True)
    broker.process_bar(_bar())

    assert broker.short("AAPL", 100).status is OrderStatus.PENDING


# ── 账户级控制器 ─────────────────────────────────────────────────


def test_account_control_runs_after_fill():
    broker = _broker(account_controls=[MaxLeverage(max_leverage=0.005)])
    broker.process_bar(_bar(day_offset=0))
    broker.buy("AAPL", 100)

    broker.process_bar(_bar(day_offset=1))

    assert len(broker.account_violations) == 1
    assert broker.account_violations[0].control == "MaxLeverage"


def test_account_control_not_triggered_without_fill():
    broker = _broker(account_controls=[MaxLeverage(max_leverage=0.005)])

    broker.process_bar(_bar(day_offset=0))

    assert broker.account_violations == []


def test_account_control_fail_mode_raises_on_fill():
    broker = _broker(
        account_controls=[MaxLeverage(max_leverage=0.005, on_error="fail")]
    )
    broker.process_bar(_bar(day_offset=0))
    broker.buy("AAPL", 100)

    with pytest.raises(TradingControlViolationError):
        broker.process_bar(_bar(day_offset=1))


def test_gross_leverage_reflects_position_exposure():
    broker = _broker()
    broker.process_bar(_bar(day_offset=0))
    broker.buy("AAPL", 100)
    broker.process_bar(_bar(day_offset=1))

    # 100 股 × 100 元 = 10000 名义敞口，权益约 100 万 → 杠杆约 0.01
    assert broker.gross_leverage() == pytest.approx(0.01, rel=0.05)


def test_gross_leverage_is_zero_without_positions():
    broker = _broker()
    broker.process_bar(_bar())

    assert broker.gross_leverage() == 0.0


# ── 引擎配置默认值 ───────────────────────────────────────────────


def test_backtest_config_defaults_to_no_controls():
    from app.engine.backtest.engine import BacktestConfig

    cfg = BacktestConfig()

    assert cfg.controls == []
    assert cfg.account_controls == []


def test_backtest_config_controls_are_not_shared_between_instances():
    from app.engine.backtest.engine import BacktestConfig

    first = BacktestConfig()
    first.controls.append(LongOnly())

    assert BacktestConfig().controls == []


def test_engine_passes_controls_to_broker():
    from app.engine.backtest.portfolio_engine import (
        PortfolioBacktestConfig,
        PortfolioBacktestEngine,
    )
    from app.strategy.base import PortfolioStrategyBase

    class _BuyOnce(PortfolioStrategyBase):
        name = "buy-once"

        def on_bars(self, ctx) -> None:
            if not ctx.broker.fills and "AAPL" in ctx.bars:
                ctx.broker.buy("AAPL", 100)

    cfg = PortfolioBacktestConfig(
        initial_cash=1_000_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        controls=[MaxOrderSize(max_shares=10)],
    )
    bars = [_bar(day_offset=i) for i in range(4)]

    result = PortfolioBacktestEngine(cfg).run(_BuyOnce(), {"AAPL": bars})

    assert result.fills == []
