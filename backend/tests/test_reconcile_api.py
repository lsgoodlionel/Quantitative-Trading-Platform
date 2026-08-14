"""
对账 API 端点与 Celery 任务测试（V3 Wave C-b · G6）

挂载方式与 `app/api/v1/router.py` 里的注册片段一致。
审计写入全程被替换成假件，**不连 Postgres / Redis**。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import reconcile as reconcile_ep
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.data.models import Market
from app.oms.order import LiveOrderSide
from app.oms.reconcile import ReconcileResult
from app.tasks.reconcile import _suppression_reason
from tests.test_reconcile import FakeBrokerPosition, FakeGateway, FakeOMS, _filled

BASE = "/api/v1/reconcile"


def _trader() -> UserInfo:
    return UserInfo(id="u-trader", email="trader@test.local", role="trader")


def _viewer() -> UserInfo:
    return UserInfo(id="u-viewer", email="viewer@test.local", role="viewer")


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch) -> None:
    """审计是旁路，端点测试不该因为它去连库。"""
    async def _noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr("app.api.v1.endpoints.reconcile.audit_log", _noop)


@pytest.fixture(autouse=True)
def notify_calls(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.oms.reconcile.emit_reconcile_diff",
        lambda **kw: calls.append(kw) or {"dispatched": 1},
    )
    return calls


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(reconcile_ep.router, prefix=BASE, tags=["Reconcile"])
    test_app.dependency_overrides[get_current_user] = _trader
    return test_app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _wire(monkeypatch, oms: FakeOMS, gateway: FakeGateway | None) -> None:
    """把端点用的 `get_order_manager()` 换成假 OMS；gateway=None 模拟未注册网关。"""

    def _get_gateway(market: str):
        if gateway is None:
            raise KeyError(f"no gateway for {market}")
        return gateway

    oms.get_gateway = _get_gateway  # type: ignore[attr-defined]
    monkeypatch.setattr("app.api.v1.endpoints.reconcile.get_order_manager", lambda: oms)


async def test_endpoint_reports_diffs(monkeypatch, app) -> None:
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    _wire(monkeypatch, oms, FakeGateway([FakeBrokerPosition("AAPL", 120)]))

    async with await _client(app) as client:
        resp = await client.post(f"{BASE}/US")

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_clean"] is False
    assert body["broker_reachable"] is True
    assert body["position_diffs"] == [
        {"symbol": "AAPL", "local_qty": 100, "broker_qty": 120, "delta": 20}
    ]
    assert body["notified"] is True
    assert body["scope_note"]


async def test_endpoint_clean_does_not_notify(monkeypatch, app, notify_calls) -> None:
    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    _wire(monkeypatch, oms, FakeGateway([FakeBrokerPosition("AAPL", 100)]))

    async with await _client(app) as client:
        body = (await client.post(f"{BASE}/US")).json()

    assert body["is_clean"] is True
    assert body["notified"] is False
    assert notify_calls == []


async def test_endpoint_unreachable_is_not_clean(monkeypatch, app) -> None:
    oms = FakeOMS([])
    _wire(monkeypatch, oms, FakeGateway(positions_error=ConnectionError("down")))

    async with await _client(app) as client:
        body = (await client.post(f"{BASE}/US")).json()

    assert body["broker_reachable"] is False
    assert body["is_clean"] is False
    assert body["position_diffs"] == []
    assert body["notified"] is True


async def test_missing_gateway_returns_400_not_a_clean_report(monkeypatch, app) -> None:
    """没接券商不能返回一份 200「零差异」—— 那会被读成对账通过。"""
    _wire(monkeypatch, FakeOMS([]), None)

    async with await _client(app) as client:
        resp = await client.post(f"{BASE}/US")

    assert resp.status_code == 400
    assert "不等于对账通过" in resp.json()["detail"]


async def test_viewer_is_forbidden(monkeypatch, app) -> None:
    app.dependency_overrides[get_current_user] = _viewer
    _wire(monkeypatch, FakeOMS([]), FakeGateway([]))

    async with await _client(app) as client:
        resp = await client.post(f"{BASE}/US")

    assert resp.status_code == 403


async def test_endpoint_submits_no_orders(monkeypatch, app) -> None:
    gateway = FakeGateway([FakeBrokerPosition("AAPL", 7)])
    oms = FakeOMS([])
    _wire(monkeypatch, oms, gateway)

    async with await _client(app) as client:
        await client.post(f"{BASE}/US")

    assert gateway.submit_calls == 0
    assert gateway.cancel_calls == 0
    assert oms.submit_calls == 0


# ── Celery 任务的跨进程抑制逻辑 ───────────────────────────────────


def _result(**kwargs) -> ReconcileResult:
    from datetime import UTC, datetime

    base = {
        "market": Market.US,
        "checked_at": datetime.now(UTC),
        "position_diffs": (),
        "cash_diff": None,
        "broker_reachable": True,
    }
    return ReconcileResult(**{**base, **kwargs})


def test_worker_suppresses_empty_orderbook_scope_diff() -> None:
    from app.oms.reconcile import PositionDiff

    result = _result(
        position_diffs=(PositionDiff("AAPL", 0, 100, 100),),
        local_order_count=0,
    )

    assert "范围差异" in _suppression_reason(result)


def test_worker_does_not_suppress_real_diff() -> None:
    from app.oms.reconcile import PositionDiff

    result = _result(
        position_diffs=(PositionDiff("AAPL", 80, 100, 20),),
        local_order_count=3,
    )

    assert _suppression_reason(result) == ""


def test_worker_never_suppresses_unreachable() -> None:
    result = _result(broker_reachable=False, local_order_count=0)

    assert _suppression_reason(result) == ""


# ── Celery 任务：asyncio.run() 桥接 ───────────────────────────────


def _wire_task(monkeypatch, oms: FakeOMS, gateway: FakeGateway | None) -> None:
    def _get_gateway(market: str):
        if gateway is None:
            raise KeyError(f"no gateway for {market}")
        return gateway

    oms.get_gateway = _get_gateway  # type: ignore[attr-defined]
    monkeypatch.setattr("app.oms.manager.get_order_manager", lambda: oms)


def test_task_reports_real_diff_and_notifies(monkeypatch, notify_calls) -> None:
    from app.tasks.reconcile import reconcile_market

    oms = FakeOMS([_filled("AAPL", 100, LiveOrderSide.BUY)])
    _wire_task(monkeypatch, oms, FakeGateway([FakeBrokerPosition("AAPL", 120)]))

    payload = reconcile_market("US")

    assert payload["is_clean"] is False
    assert payload["notified"] is True
    assert "suppressed_reason" not in payload
    assert len(notify_calls) == 1


def test_task_suppresses_empty_orderbook_diff(monkeypatch, notify_calls) -> None:
    """worker 里订单簿为空 → 满屏「本地 0」的范围差异，不该发通知。"""
    from app.tasks.reconcile import reconcile_market

    _wire_task(monkeypatch, FakeOMS([]), FakeGateway([FakeBrokerPosition("AAPL", 100)]))

    payload = reconcile_market("US")

    assert payload["notified"] is False
    assert "范围差异" in payload["suppressed_reason"]
    assert notify_calls == []


def test_task_notifies_when_broker_unreachable(monkeypatch, notify_calls) -> None:
    from app.tasks.reconcile import reconcile_market

    _wire_task(monkeypatch, FakeOMS([]), FakeGateway(positions_error=ConnectionError("x")))

    payload = reconcile_market("US")

    assert payload["broker_reachable"] is False
    assert payload["is_clean"] is False
    assert payload["notified"] is True


def test_task_without_gateway_is_not_clean(monkeypatch) -> None:
    from app.tasks.reconcile import reconcile_market

    _wire_task(monkeypatch, FakeOMS([]), None)

    payload = reconcile_market("US")

    assert payload["broker_reachable"] is False
    assert payload["is_clean"] is False
    assert "未注册" in payload["error"]


def test_task_exception_is_never_reported_as_clean(monkeypatch) -> None:
    """任务自身炸了也不能被读成「对账通过」。"""
    from app.tasks.reconcile import reconcile_market

    def _boom() -> None:
        raise RuntimeError("OMS 未初始化")

    monkeypatch.setattr("app.oms.manager.get_order_manager", _boom)

    payload = reconcile_market("US")

    assert payload["broker_reachable"] is False
    assert payload["is_clean"] is False
    assert "对账任务异常" in payload["error"]


def test_task_bad_market_is_not_clean(monkeypatch) -> None:
    from app.tasks.reconcile import reconcile_market

    payload = reconcile_market("MARS")

    assert payload["is_clean"] is False
    assert payload["broker_reachable"] is False


def test_reconcile_all_markets_covers_each(monkeypatch, notify_calls) -> None:
    from app.tasks.reconcile import reconcile_all_markets

    _wire_task(monkeypatch, FakeOMS([]), FakeGateway([]))

    payload = reconcile_all_markets(["US", "HK"])

    assert [r["market"] for r in payload["results"]] == ["US", "HK"]
    assert all(r["is_clean"] for r in payload["results"])
    assert notify_calls == []


def test_beat_schedule_registers_reconcile() -> None:
    """任务必须真的挂在 beat 上，否则「定时对账」只是个没人调用的函数。"""
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["reconcile-live-positions"]

    assert entry["task"] == "app.tasks.reconcile.reconcile_all_markets"
