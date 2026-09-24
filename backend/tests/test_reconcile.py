"""
实盘对账测试（V3 Wave C-b · G6）

对应契约 docs/contracts/waveCb-reconcile-audit.md §三 验收 2：

1. 有差异 → 发 RECONCILE_DIFF 且 diff 内容正确
2. 无差异 → **不发通知**
3. 券商不可达 → `broker_reachable=False` 且**发通知**，
   且 `position_diffs` 为空**不**被解读为「对账通过」
4. 对账不产生任何订单（断言 `submit_order` / `cancel_order` 零调用）

券商网关与 OMS 全部用假件，**不连任何外部服务、不依赖 Postgres/Redis**。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data.models import Market
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus
from app.oms.reconcile import (
    CASH_TOLERANCE,
    PositionDiff,
    ReconcileResult,
    diff_positions,
    local_net_positions,
    notify_reconcile,
    reconcile,
    reconcile_and_notify,
)

# ── 假件 ─────────────────────────────────────────────────────────


class FakeBrokerPosition:
    def __init__(self, symbol: str, qty: int, market: str = "US") -> None:
        self.symbol = symbol
        self.qty = qty
        self.market = market


class FakeAccount:
    def __init__(self, cash: float) -> None:
        self.cash = cash


class FakeGateway:
    """只读网关假件。`submit_order` / `cancel_order` 一旦被调用即测试失败。"""

    def __init__(
        self,
        positions: list[FakeBrokerPosition] | None = None,
        cash: float = 0.0,
        *,
        positions_error: Exception | None = None,
        account_error: Exception | None = None,
    ) -> None:
        self._positions = positions or []
        self._cash = cash
        self._positions_error = positions_error
        self._account_error = account_error
        self.submit_calls = 0
        self.cancel_calls = 0

    async def get_positions(self) -> list[FakeBrokerPosition]:
        if self._positions_error:
            raise self._positions_error
        return list(self._positions)

    async def get_account(self) -> FakeAccount:
        if self._account_error:
            raise self._account_error
        return FakeAccount(self._cash)

    async def submit_order(self, order: LiveOrder) -> str:
        self.submit_calls += 1
        raise AssertionError("对账绝不能下单")

    async def cancel_order(self, broker_order_id: str) -> None:
        self.cancel_calls += 1
        raise AssertionError("对账绝不能撤单")


class FakeOMS:
    """只暴露 `list_orders` 的 OMS 假件（`PositionSource` 协议）。"""

    def __init__(self, orders: list[LiveOrder] | None = None) -> None:
        self._orders = orders or []
        self.submit_calls = 0

    def list_orders(self, strategy_id=None, status=None, limit=100) -> list[LiveOrder]:
        return list(self._orders[:limit])

    async def submit_order(self, *args, **kwargs):
        self.submit_calls += 1
        raise AssertionError("对账绝不能下单")


def _filled(symbol: str, qty: int, side: LiveOrderSide, market: str = "US") -> LiveOrder:
    return LiveOrder(
        symbol=symbol,
        market=market,
        side=side,
        qty=qty,
        filled_qty=qty,
        status=LiveOrderStatus.FILLED,
    )


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """拦截 `emit_reconcile_diff`，记录每次发射的参数。"""
    calls: list[dict] = []

    def _fake_emit(**kwargs) -> dict:
        calls.append(kwargs)
        return {"dispatched": 1}

    monkeypatch.setattr("app.oms.reconcile.emit_reconcile_diff", _fake_emit)
    return calls


# ── 1. 有差异 → 发通知且 diff 内容正确 ────────────────────────────


async def test_position_diff_is_detected_and_notified(captured) -> None:
    # Arrange：本地记 100 股 AAPL，券商侧有 120 股；MSFT 只在券商侧
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    gateway = FakeGateway([
        FakeBrokerPosition("AAPL", 120),
        FakeBrokerPosition("MSFT", 50),
    ])

    # Act
    result = await reconcile_and_notify(Market.US, oms, gateway)

    # Assert
    assert result.broker_reachable is True
    assert result.is_clean is False
    assert result.position_diffs == (
        PositionDiff(symbol="AAPL", local_qty=100, broker_qty=120, delta=20),
        PositionDiff(symbol="MSFT", local_qty=0, broker_qty=50, delta=50),
    )
    assert result.diff_count == 2

    assert len(captured) == 1
    assert captured[0]["market"] == "US"
    assert captured[0]["diff_count"] == 2
    assert captured[0]["broker_reachable"] is True
    assert "AAPL" in captured[0]["detail"]


async def test_sell_orders_reduce_local_net_position() -> None:
    oms = FakeOMS([
        _filled("AAPL", 100, LiveOrderSide.BUY),
        _filled("AAPL", 40, LiveOrderSide.SELL),
    ])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 60)])

    result = await reconcile(Market.US, oms, gateway)

    assert result.is_clean is True
    assert result.position_diffs == ()


async def test_unfilled_orders_do_not_count_as_local_position() -> None:
    pending = LiveOrder(
        symbol="AAPL", market="US", side=LiveOrderSide.BUY, qty=100, filled_qty=0
    )
    oms = FakeOMS([pending])
    gateway = FakeGateway([])

    result = await reconcile(Market.US, oms, gateway)

    assert result.is_clean is True


async def test_other_market_orders_are_excluded() -> None:
    oms = FakeOMS([
        _filled("AAPL", 100, LiveOrderSide.BUY, market="US"),
        _filled("00700", 200, LiveOrderSide.BUY, market="HK"),
    ])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 100, market="US")])

    result = await reconcile(Market.US, oms, gateway)

    assert result.is_clean is True


async def test_broker_position_without_market_field_is_kept() -> None:
    """券商不填 market 时按本次查询的市场处理 —— 丢掉它会得出一份假的零差异。"""
    oms = FakeOMS([])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 10, market="")])

    result = await reconcile(Market.US, oms, gateway)

    assert result.is_clean is False
    assert result.position_diffs[0].broker_qty == 10


# ── 2. 无差异 → 不发通知 ──────────────────────────────────────────


async def test_clean_reconcile_sends_no_notification(captured) -> None:
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 100)])

    result = await reconcile_and_notify(Market.US, oms, gateway)

    assert result.is_clean is True
    assert result.diff_count == 0
    assert captured == [], "一致时不该发通知：噪音会让用户学会忽略这个通知"


async def test_both_sides_empty_is_clean_and_silent(captured) -> None:
    result = await reconcile_and_notify(Market.US, FakeOMS([]), FakeGateway([]))

    assert result.is_clean is True
    assert captured == []


# ── 3. 券商不可达 ≠ 对账通过 ──────────────────────────────────────


async def test_broker_unreachable_is_not_clean_and_notifies(captured) -> None:
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    gateway = FakeGateway(positions_error=ConnectionError("券商 API 超时"))

    result = await reconcile_and_notify(Market.US, oms, gateway)

    # 空差异列表，但**绝不能**被解读为对账通过
    assert result.position_diffs == ()
    assert result.diff_count == 0
    assert result.broker_reachable is False
    assert result.is_clean is False, "券商不可达时 is_clean 必须为 False"
    assert result.needs_attention is True
    assert "券商 API 超时" in result.error
    assert "券商不可达" in result.summary()

    # 且必须发通知（需要人介入）
    assert len(captured) == 1
    assert captured[0]["broker_reachable"] is False


async def test_cash_query_failure_also_marks_unreachable(captured) -> None:
    """持仓拿到了、资金没拿到 —— 仍然不算对完账。"""
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    gateway = FakeGateway(
        [FakeBrokerPosition("AAPL", 100)],
        account_error=TimeoutError("资金查询超时"),
    )

    result = await reconcile_and_notify(Market.US, oms, gateway, local_cash=1000.0)

    assert result.broker_reachable is False
    assert result.is_clean is False
    assert result.position_diffs == ()
    assert len(captured) == 1


async def test_unreachable_result_dict_flags_not_clean() -> None:
    """序列化后的结果体也必须带 is_clean=False，前端不该自己数 position_diffs。"""
    gateway = FakeGateway(positions_error=ConnectionError("down"))

    payload = (await reconcile(Market.US, FakeOMS([]), gateway)).to_dict()

    assert payload["broker_reachable"] is False
    assert payload["is_clean"] is False
    assert payload["position_diffs"] == []
    assert payload["scope_note"]


def test_empty_diffs_alone_never_means_clean() -> None:
    """直接构造一个「零差异 + 不可达」的结果，确认 is_clean 不会被骗过去。"""
    result = ReconcileResult(
        market=Market.US,
        checked_at=datetime.now(UTC),
        position_diffs=(),
        cash_diff=None,
        broker_reachable=False,
    )

    assert result.diff_count == 0
    assert result.is_clean is False
    assert result.needs_attention is True


def test_notify_reconcile_skips_only_when_clean(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.oms.reconcile.emit_reconcile_diff",
        lambda **kw: calls.append(kw) or {"dispatched": 1},
    )

    clean = ReconcileResult(
        market=Market.US,
        checked_at=datetime.now(UTC),
        position_diffs=(),
        cash_diff=None,
        broker_reachable=True,
    )
    assert notify_reconcile(clean) == {"dispatched": 0, "skipped": True}
    assert calls == []


# ── 4. 对账不产生任何订单 ─────────────────────────────────────────


async def test_reconcile_never_submits_orders() -> None:
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 5)])

    result = await reconcile_and_notify(Market.US, oms, gateway)

    assert result.has_position_diff is True, "前提：这次确实有差异"
    assert gateway.submit_calls == 0
    assert gateway.cancel_calls == 0
    assert oms.submit_calls == 0


async def test_reconcile_does_not_mutate_local_orders() -> None:
    order = _filled("AAPL", 100, LiveOrderSide.BUY)
    oms = FakeOMS([order])
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 5)])

    await reconcile(Market.US, oms, gateway)

    assert order.filled_qty == 100
    assert order.status is LiveOrderStatus.FILLED


# ── 资金对账 ──────────────────────────────────────────────────────


async def test_cash_not_reconciled_when_local_cash_absent() -> None:
    """不传 local_cash 时 cash_diff 为 None —— 表示「本期未对资金」而非「资金一致」。"""
    gateway = FakeGateway([], cash=12345.0)

    result = await reconcile(Market.US, FakeOMS([]), gateway)

    assert result.cash_diff is None
    assert result.has_cash_diff is False


async def test_cash_diff_detected(captured) -> None:
    gateway = FakeGateway([], cash=9000.0)

    result = await reconcile_and_notify(Market.US, FakeOMS([]), gateway, local_cash=10000.0)

    assert result.cash_diff == pytest.approx(-1000.0)
    assert result.has_cash_diff is True
    assert result.is_clean is False
    assert result.diff_count == 1
    assert len(captured) == 1


async def test_cash_diff_within_tolerance_is_clean(captured) -> None:
    gateway = FakeGateway([], cash=10000.0 + CASH_TOLERANCE / 2)

    result = await reconcile_and_notify(Market.US, FakeOMS([]), gateway, local_cash=10000.0)

    assert result.has_cash_diff is False
    assert result.is_clean is True
    assert captured == []


# ── 纯函数 ────────────────────────────────────────────────────────


def test_local_net_positions_drops_flat_symbols() -> None:
    orders = [
        _filled("AAPL", 100, LiveOrderSide.BUY),
        _filled("AAPL", 100, LiveOrderSide.SELL),
        _filled("MSFT", 30, LiveOrderSide.BUY),
    ]

    net = local_net_positions(orders, Market.US)

    assert net == {"MSFT": 30}


def test_diff_positions_is_sorted_and_only_mismatches() -> None:
    diffs = diff_positions({"B": 1, "A": 5, "C": 2}, {"B": 1, "A": 3})

    assert [d.symbol for d in diffs] == ["A", "C"]
    assert diffs[0].delta == -2
    assert diffs[1].delta == -2


def test_market_accepts_plain_string() -> None:
    assert local_net_positions([_filled("AAPL", 1, LiveOrderSide.BUY)], Market("US"))


# ── 通知发射器：不可达时的文案 ────────────────────────────────────


def test_emit_reconcile_diff_unreachable_uses_distinct_title(monkeypatch) -> None:
    """0 条差异 + 「存在差异」标题会被读成「通过」—— 不可达必须换文案。"""
    sent: list = []
    monkeypatch.setattr(
        "app.notify.dispatcher.dispatch_event",
        lambda event: sent.append(event) or {"dispatched": 1},
    )
    from app.notify.emit import emit_reconcile_diff

    emit_reconcile_diff(market="US", diff_count=0, detail="超时", broker_reachable=False)

    assert len(sent) == 1
    assert "券商不可达" in sent[0].title
    assert "不代表账目一致" in sent[0].payload["提示"]


def test_emit_reconcile_diff_keeps_legacy_signature(monkeypatch) -> None:
    """老调用方（Wave A-c 的签名）不传 broker_reachable 时行为不变。"""
    sent: list = []
    monkeypatch.setattr(
        "app.notify.dispatcher.dispatch_event",
        lambda event: sent.append(event) or {"dispatched": 1},
    )
    from app.notify.emit import emit_reconcile_diff

    emit_reconcile_diff(market="US", diff_count=2, detail="持仓不一致")

    assert sent[0].title == "实盘对账存在差异"
    assert sent[0].payload["差异条数"] == 2
