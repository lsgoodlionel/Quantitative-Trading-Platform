"""交叉归因 + 拒绝信号测试（Wave N-a / N4）

验收要点（契约 waveNa §五.4）：
- 交叉表行数 = 实际出现过的 (entry_tag, exit_reason) 组合数（空组合不占行）
- 拒绝信号归一化：同类不同数值的 reject_reason 归到同一类
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import (
    Order,
    OrderSide,
    OrderStatus,
    SimulatedBroker,
)
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.reject_reasons import (
    REJECT_INSUFFICIENT_CASH,
    REJECT_INSUFFICIENT_POSITION,
    REJECT_MAX_OPEN_POSITIONS,
    REJECT_OTHER,
    REJECT_T_PLUS_1,
    REJECT_TRADING_CONTROL,
    build_rejected_signal_section,
    classify_reject_reason,
    rejected_signal_summary,
)
from app.engine.backtest.roundtrips import RoundTrip
from app.engine.backtest.slippage import NoSlippage
from app.engine.backtest.tag_metrics import compute_tag_metrics, cross_tag_metrics


def _trip(trip_id: int, entry_tag: str, exit_reason: str, pnl: float) -> RoundTrip:
    return RoundTrip(
        trip_id=trip_id,
        entry_time=datetime(2024, 1, 2, tzinfo=UTC),
        exit_time=datetime(2024, 1, 5, tzinfo=UTC),
        direction="long",
        entry_tag=entry_tag,
        exit_reason=exit_reason,
        qty=100.0,
        entry_price=10.0,
        exit_price=10.0 + pnl / 100.0,
        pnl=pnl,
        commission=0.0,
        holding_bars=3,
        holding_days=3.0,
    )


def _us_bar(symbol: str = "AAPL") -> Bar:
    return Bar(
        time=datetime(2024, 1, 2, tzinfo=UTC),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=100.0,
        high=106.0,
        low=99.0,
        close=105.0,
        volume=10_000,
    )


def _rejected(
    reason: str,
    *,
    symbol: str = "AAPL",
    at: datetime | None = None,
    qty: int = 100,
) -> Order:
    order = Order(symbol=symbol, market=Market.US, side=OrderSide.SELL, qty=qty)
    order.status = OrderStatus.REJECTED
    order.reject_reason = reason
    order.rejected_at = at or datetime(2024, 1, 2, tzinfo=UTC)
    return order


# ── 交叉归因 ─────────────────────────────────────────────────────

class TestCrossTagMetrics:
    def test_row_count_equals_observed_combinations(self) -> None:
        # Arrange: 2 个 entry_tag × 2 个 exit_reason，但只出现 3 种组合
        trips = [
            _trip(1, "breakout", "take_profit", 100.0),
            _trip(2, "breakout", "stop", -50.0),
            _trip(3, "meanrev", "take_profit", 30.0),
            _trip(4, "meanrev", "take_profit", 20.0),
        ]

        # Act
        rows = cross_tag_metrics(trips, starting_balance=10_000.0)

        # Assert: (meanrev, stop) 从未出现 → 不占行
        assert len(rows) == 3
        assert all(r["trades"] > 0 for r in rows)
        combos = {(r["entry_tag"], r["exit_reason"]) for r in rows}
        assert ("meanrev", "stop") not in combos

    def test_rows_carry_both_dimensions_and_aggregate_pnl(self) -> None:
        trips = [
            _trip(1, "breakout", "take_profit", 100.0),
            _trip(2, "breakout", "take_profit", 40.0),
        ]

        rows = cross_tag_metrics(trips, starting_balance=10_000.0)

        assert len(rows) == 1
        row = rows[0]
        assert row["entry_tag"] == "breakout"
        assert row["exit_reason"] == "take_profit"
        assert row["trades"] == 2
        assert row["profit_abs"] == pytest.approx(140.0)
        assert row["key"] == "breakout → take_profit"

    def test_sorted_by_profit_desc(self) -> None:
        trips = [
            _trip(1, "a", "stop", -100.0),
            _trip(2, "b", "take_profit", 200.0),
        ]

        rows = cross_tag_metrics(trips, starting_balance=10_000.0)

        assert [r["profit_abs"] for r in rows] == [200.0, -100.0]

    def test_empty_trips_returns_empty(self) -> None:
        assert cross_tag_metrics([], starting_balance=10_000.0) == []

    def test_exposed_through_compute_tag_metrics(self) -> None:
        trips = [_trip(1, "breakout", "stop", -10.0), _trip(2, "breakout", "stop", 20.0)]
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        equity = pd.Series([100.0, 101.0, 102.0], index=index)

        result = compute_tag_metrics(trips, equity.pct_change().dropna(), equity, 10_000.0)

        assert "by_entry_exit" in result
        assert len(result["by_entry_exit"]) == 1
        # 既有分组不受影响
        assert result["by_entry_tag"]
        assert result["by_exit_reason"]


# ── 拒绝原因归一化 ───────────────────────────────────────────────

class TestClassifyRejectReason:
    def test_same_category_different_numbers_collapse(self) -> None:
        # Arrange: 同类不同数值的两条 A股 T+1 拒单文案
        a = classify_reject_reason("A股T+1限制: 可卖 0 股，请求卖 100 股")
        b = classify_reject_reason("A股T+1限制: 可卖 300 股，请求卖 1200 股")

        # Assert
        assert a.code == b.code == REJECT_T_PLUS_1

    def test_cash_variants_collapse(self) -> None:
        codes = {
            classify_reject_reason("insufficient cash").code,
            classify_reject_reason("现金不足，30 股未成交且不重新挂单（委托 100，成交 70，残单 0）").code,
        }
        assert codes == {REJECT_INSUFFICIENT_CASH}

    def test_trading_control_keeps_control_name(self) -> None:
        cat = classify_reject_reason("[MaxOrderSize] 单笔委托 5000 股超过上限 1000 股")

        assert cat.code == REJECT_TRADING_CONTROL
        assert "MaxOrderSize" in cat.label

    def test_unknown_text_is_normalized_not_exploded(self) -> None:
        # Arrange: 未登记的自由文本，只有数值不同
        a = classify_reject_reason("自定义闸门拦截 AAPL 3 次，冷却 15 分钟")
        b = classify_reject_reason("自定义闸门拦截 AAPL 9 次，冷却 60 分钟")

        # Assert: 数值被剥掉后归到同一类
        assert a.code == b.code == REJECT_OTHER
        assert a.label == b.label

    def test_blank_reason_is_handled(self) -> None:
        assert classify_reject_reason("").code == REJECT_OTHER
        assert classify_reject_reason(None).code == REJECT_OTHER   # type: ignore[arg-type]


class TestRejectedSignalSummary:
    def test_groups_by_normalized_category(self) -> None:
        # Arrange: 3 条 T+1（数值各不相同）+ 1 条现金不足
        orders = [
            _rejected("A股T+1限制: 可卖 0 股，请求卖 100 股", symbol="600000"),
            _rejected("A股T+1限制: 可卖 5 股，请求卖 200 股", symbol="600519"),
            _rejected("A股T+1限制: 可卖 50 股，请求卖 80 股", symbol="600000"),
            _rejected("insufficient cash", symbol="000001"),
        ]

        # Act
        rows = rejected_signal_summary(orders)

        # Assert
        assert len(rows) == 2
        top = rows[0]
        assert top["code"] == REJECT_T_PLUS_1
        assert top["count"] == 3
        assert top["symbol_count"] == 2

    def test_reports_first_and_last_time(self) -> None:
        orders = [
            _rejected("insufficient cash", at=datetime(2024, 3, 5, tzinfo=UTC)),
            _rejected("insufficient cash", at=datetime(2024, 1, 9, tzinfo=UTC)),
        ]

        row = rejected_signal_summary(orders)[0]

        assert row["first_time"].startswith("2024-01-09")
        assert row["last_time"].startswith("2024-03-05")

    def test_ignores_orders_without_reason(self) -> None:
        clean = Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)

        assert rejected_signal_summary([clean]) == []

    def test_empty_input(self) -> None:
        assert rejected_signal_summary([]) == []

    def test_share_pct_sums_to_100(self) -> None:
        orders = [
            _rejected("insufficient cash"),
            _rejected("A股T+1限制: 可卖 0 股，请求卖 100 股"),
        ]

        rows = rejected_signal_summary(orders)

        assert sum(r["share_pct"] for r in rows) == pytest.approx(100.0)


class TestRejectedSignalSection:
    def test_section_carries_total_and_rows(self) -> None:
        orders = [_rejected("insufficient cash"), _rejected("insufficient cash")]

        section = build_rejected_signal_section(orders)

        assert section is not None
        assert section["total_rejected"] == 2
        assert len(section["by_reason"]) == 1
        assert section["truncated"] is False

    def test_returns_none_when_nothing_rejected(self) -> None:
        assert build_rejected_signal_section([]) is None

    def test_overflow_is_disclosed(self) -> None:
        section = build_rejected_signal_section([_rejected("insufficient cash")], overflow=7)

        assert section is not None
        assert section["truncated"] is True
        assert section["dropped"] == 7
        assert section["total_rejected"] == 8
        assert section["recorded"] == 1


# ── 券商侧拒绝台账（汇总的数据来源） ─────────────────────────────

class TestBrokerRejectionLedger:
    def _broker(self, **kwargs) -> SimulatedBroker:
        return SimulatedBroker(
            initial_cash=kwargs.pop("initial_cash", 100_000.0),
            market=kwargs.pop("market", Market.US),
            commission_model=USCommissionModel(),
            slippage_model=NoSlippage(),
            **kwargs,
        )

    def test_submit_time_rejection_is_recorded(self) -> None:
        # Arrange: 无持仓时卖出 —— submit_order 阶段就被拒
        broker = self._broker()

        # Act
        broker.submit_order(
            Order(symbol="AAPL", market=Market.US, side=OrderSide.SELL, qty=100)
        )

        # Assert
        assert len(broker.rejections) == 1
        rows = rejected_signal_summary(broker.rejections)
        assert rows[0]["code"] == REJECT_INSUFFICIENT_POSITION

    def test_a_share_t_plus_1_rejection_is_recorded(self) -> None:
        broker = self._broker(market=Market.A)
        broker.submit_order(
            Order(symbol="600000", market=Market.A, side=OrderSide.SELL, qty=100)
        )

        rows = rejected_signal_summary(broker.rejections)

        assert rows[0]["code"] == REJECT_T_PLUS_1

    def test_fill_time_cash_rejection_is_recorded_with_sim_clock(self) -> None:
        # Arrange: 现金极少，买入必然在撮合阶段被拒
        broker = self._broker(initial_cash=1.0)
        broker.submit_order(
            Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=100)
        )

        # Act
        broker.process_bar(_us_bar())

        # Assert: 时间戳来自模拟时钟（bar 时间），而不是墙钟
        assert len(broker.rejections) == 1
        assert broker.rejections[0].rejected_at == _us_bar().time
        assert rejected_signal_summary(broker.rejections)[0]["code"] == (
            REJECT_INSUFFICIENT_CASH
        )

    def test_portfolio_broker_open_limit_rejection_reaches_ledger(self) -> None:
        # Arrange: 上限 1 个标的，先占满再开第二个
        broker = PortfolioBroker(
            initial_cash=100_000.0,
            market=Market.US,
            commission_model=USCommissionModel(),
            slippage_model=NoSlippage(),
            max_open_positions=1,
        )
        broker.submit_order(
            Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)
        )
        broker.process_bars(_us_bar().time, {"AAPL": _us_bar()})

        # Act: 第二个标的应被持仓上限拒掉
        broker.submit_order(
            Order(symbol="MSFT", market=Market.US, side=OrderSide.BUY, qty=10)
        )

        # Assert: 子类覆写的是 _submit_order_impl，拒单不会绕过基类的记账点
        rows = rejected_signal_summary(broker.rejections)
        assert [r["code"] for r in rows] == [REJECT_MAX_OPEN_POSITIONS]
        assert rows[0]["symbols"] == ["MSFT"]

    def test_ledger_property_returns_a_copy(self) -> None:
        broker = self._broker()
        broker.submit_order(
            Order(symbol="AAPL", market=Market.US, side=OrderSide.SELL, qty=100)
        )

        broker.rejections.clear()   # 拿到的是副本，改不动券商内部台账

        assert len(broker.rejections) == 1

    def test_clean_run_keeps_ledger_empty(self) -> None:
        broker = self._broker()
        broker.submit_order(
            Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)
        )
        broker.process_bar(_us_bar())

        assert broker.rejections == []
        assert broker.rejection_overflow == 0
