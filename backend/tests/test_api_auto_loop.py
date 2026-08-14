"""自动因子循环 API 端点测试（V3 · I2）

覆盖：提交 → 轮询 → 列表 → 删除，以及提交期的同步入参校验。
Celery 与 Redis 全部替身，一次真实派发都不发。

挂载方式与 `app/api/v1/router.py` 里的正式注册完全一致（`prefix="/lab"`）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import lab_auto_loop
from app.core.redis import get_redis
from app.quant.lab.loop_store import (
    STATUS_DONE,
    STATUS_QUEUED,
    get_round,
    list_rounds,
    mark_status,
)
from tests.fake_redis import FakeRedis

BASE_REQUEST = {
    "universe": ["AAPL", "MSFT", "NVDA"],
    "is_end": "2024-06-30",
    "market": "US",
    "start": "2023-01-01",
    "end": "2024-12-31",
    "generations": 2,
    "population": 8,
}


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(redis: FakeRedis) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(
        lab_auto_loop.router, prefix="/api/v1/lab", tags=["Auto Factor Loop"]
    )
    test_app.dependency_overrides[get_redis] = lambda: redis
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def stub_celery(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """拦下派发，记录 payload —— 单测不该真的往 broker 里塞任务。"""
    dispatched: list[dict] = []

    class _FakeTask:
        @staticmethod
        def delay(payload: dict):
            dispatched.append(payload)
            return type("R", (), {"id": "celery-task-1"})()

    import app.tasks.auto_loop as task_module

    monkeypatch.setattr(task_module, "run_auto_factor_loop_task", _FakeTask)
    return dispatched


class TestSubmit:
    async def test_returns_round_id_immediately_and_dispatches(
        self, client: AsyncClient, redis: FakeRedis, stub_celery: list[dict]
    ):
        resp = await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == STATUS_QUEUED
        assert body["round_id"]

        assert len(stub_celery) == 1
        assert stub_celery[0]["round_id"] == body["round_id"]

        record = await get_round(redis, body["round_id"])
        assert record is not None
        assert record.status == STATUS_QUEUED

    async def test_is_end_after_data_end_is_rejected_at_submit_time(
        self, client: AsyncClient
    ):
        """样本外为空的配置必须当场拒绝 —— 轮询几分钟才发现是纯粹的浪费。"""
        payload = {**BASE_REQUEST, "is_end": "2025-06-30"}

        resp = await client.post("/api/v1/lab/auto-loop", json=payload)

        assert resp.status_code == 400
        assert "样本外" in resp.json()["detail"]

    async def test_is_end_before_data_start_is_rejected(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/lab/auto-loop", json={**BASE_REQUEST, "is_end": "2022-01-01"}
        )
        assert resp.status_code == 400

    async def test_universe_below_the_cross_section_minimum_is_422(
        self, client: AsyncClient
    ):
        resp = await client.post(
            "/api/v1/lab/auto-loop", json={**BASE_REQUEST, "universe": ["AAPL"]}
        )
        assert resp.status_code == 422

    async def test_broker_failure_leaves_no_orphan_queued_record(
        self, client: AsyncClient, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ):
        """派发失败必须把 queued 记录清掉 —— 永远不会执行的排队项最误导人。"""
        import app.tasks.auto_loop as task_module

        class _Broken:
            @staticmethod
            def delay(_payload):
                raise RuntimeError("broker down")

        monkeypatch.setattr(task_module, "run_auto_factor_loop_task", _Broken)

        resp = await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)

        assert resp.status_code == 503
        assert await list_rounds(redis) == []


class TestPoll:
    async def test_unknown_round_is_404(self, client: AsyncClient):
        resp = await client.get("/api/v1/lab/auto-loop/nope")
        assert resp.status_code == 404

    async def test_done_round_returns_the_result_payload(
        self, client: AsyncClient, redis: FakeRedis
    ):
        submit = await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)
        round_id = submit.json()["round_id"]

        await mark_status(
            redis,
            round_id,
            STATUS_DONE,
            result={"hypotheses_tested": 1234, "survivors": [], "truncated": False},
        )

        resp = await client.get(f"/api/v1/lab/auto-loop/{round_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == STATUS_DONE
        assert body["result"]["hypotheses_tested"] == 1234

    async def test_list_is_newest_first(self, client: AsyncClient):
        first = (await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)).json()
        second = (await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)).json()

        resp = await client.get("/api/v1/lab/auto-loop?limit=10")

        assert resp.status_code == 200
        ids = [item["round_id"] for item in resp.json()["items"]]
        assert set(ids) == {first["round_id"], second["round_id"]}


class TestDelete:
    async def test_delete_removes_the_record(self, client: AsyncClient):
        round_id = (await client.post("/api/v1/lab/auto-loop", json=BASE_REQUEST)).json()[
            "round_id"
        ]

        assert (await client.delete(f"/api/v1/lab/auto-loop/{round_id}")).status_code == 200
        assert (await client.get(f"/api/v1/lab/auto-loop/{round_id}")).status_code == 404

    async def test_delete_unknown_is_404(self, client: AsyncClient):
        assert (await client.delete("/api/v1/lab/auto-loop/nope")).status_code == 404


class TestNoPromotionEndpoint:
    def test_router_exposes_no_promote_or_deploy_path(self):
        """契约 §2.3：循环只入库。这里没有任何一条通往策略/实盘的路由。"""
        paths = {route.path for route in lab_auto_loop.router.routes}
        assert paths == {"/auto-loop", "/auto-loop/{round_id}"}
        for path in paths:
            assert "promote" not in path
            assert "strategy" not in path
            assert "live" not in path
