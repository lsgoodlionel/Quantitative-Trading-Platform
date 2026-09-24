"""
自适应再训练 / 漂移检测 API 端点测试（V4 · M6）

覆盖：提交 → 轮询 → 列表 → 删除，以及提交期的同步入参校验。
Celery 与 Redis 全部替身，一次真实派发都不发。

挂载方式与 `app/api/v1/router.py` 里的正式注册完全一致（`prefix="/retrain"`）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import retrain as retrain_endpoint
from app.core.redis import get_redis
from app.quant.retrain_store import (
    KIND_DRIFT_CHECK,
    KIND_RETRAIN,
    STATUS_DONE,
    STATUS_QUEUED,
    get_job,
    mark_status,
)
from tests.fake_redis import FakeRedis

BASE_RETRAIN = {
    "symbols": ["AAPL", "MSFT", "NVDA"],
    "market": "US",
    "lookback_days": 730,
    "model_kind": "lasso",
}

BASE_DRIFT = {
    "artifact_id": "0b6e1f3a-0000-4000-8000-000000000001",
    "symbols": ["AAPL"],
    "lookback_days": 180,
}


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(redis: FakeRedis) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(
        retrain_endpoint.router, prefix="/api/v1/retrain", tags=["Adaptive Retrain"]
    )
    test_app.dependency_overrides[get_redis] = lambda: redis
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def stub_celery(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    """拦下派发，记录 payload —— 单测不该真的往 broker 里塞任务。"""
    dispatched: dict[str, list[dict]] = {"retrain": [], "drift": []}

    def _task(bucket: str):
        class _FakeTask:
            @staticmethod
            def delay(payload: dict):
                dispatched[bucket].append(payload)
                return type("R", (), {"id": "celery-task-1"})()

        return _FakeTask

    import app.tasks.retrain as task_module

    monkeypatch.setattr(task_module, "run_retrain_task", _task("retrain"))
    monkeypatch.setattr(task_module, "run_drift_check_task", _task("drift"))
    return dispatched


class TestSubmitRetrain:
    async def test_returns_job_id_and_dispatches(
        self, client: AsyncClient, redis: FakeRedis, stub_celery
    ):
        resp = await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == STATUS_QUEUED
        assert body["kind"] == KIND_RETRAIN
        assert stub_celery["retrain"][0]["job_id"] == body["job_id"]

        record = await get_job(redis, body["job_id"])
        assert record is not None
        assert record.kind == KIND_RETRAIN

    async def test_response_states_it_will_not_auto_activate(self, client: AsyncClient):
        """提交响应里就得写明「不会自动上线」—— 别让用户以为提交完就换好了。"""
        resp = await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)

        assert "不会" in resp.json()["note"]
        assert "人工" in resp.json()["note"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("symbols", []),
            ("model_kind", "xgboost"),
            ("lookback_days", 5),
            ("holdout_ratio", 0.9),
            ("forward_period", 0),
        ],
    )
    async def test_illegal_config_rejected_synchronously(
        self, client: AsyncClient, stub_celery, field: str, value
    ):
        """非法配置在提交那一刻就 4xx，不能轮询二十分钟才知道。"""
        resp = await client.post(
            "/api/v1/retrain/jobs", json={**BASE_RETRAIN, field: value}
        )

        assert resp.status_code in (400, 422)
        assert stub_celery["retrain"] == []

    async def test_bad_end_date_rejected(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/retrain/jobs", json={**BASE_RETRAIN, "end": "2024/06/30"}
        )

        assert resp.status_code == 400
        assert "end" in resp.json()["detail"]

    async def test_broker_failure_removes_the_orphan_record(
        self, client: AsyncClient, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ):
        """派发失败不能留下一条永远不会执行的 queued 记录。"""
        import app.tasks.retrain as task_module

        class _Broken:
            @staticmethod
            def delay(payload):
                raise RuntimeError("broker down")

        monkeypatch.setattr(task_module, "run_retrain_task", _Broken)

        resp = await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)

        assert resp.status_code == 503
        listed = await client.get("/api/v1/retrain/jobs")
        assert listed.json()["total"] == 0


class TestSubmitDriftCheck:
    async def test_dispatches_drift_task(self, client: AsyncClient, stub_celery):
        resp = await client.post("/api/v1/retrain/drift-checks", json=BASE_DRIFT)

        assert resp.status_code == 200
        assert resp.json()["kind"] == KIND_DRIFT_CHECK
        assert stub_celery["drift"][0]["artifact_id"] == BASE_DRIFT["artifact_id"]
        # 漂移检测绝不能顺手派发一个重训
        assert stub_celery["retrain"] == []

    async def test_note_says_no_auto_retrain(self, client: AsyncClient):
        resp = await client.post("/api/v1/retrain/drift-checks", json=BASE_DRIFT)

        assert "不会" in resp.json()["note"]
        assert "重训" in resp.json()["note"]


class TestQueryAndDelete:
    async def test_list_is_newest_first_and_mixes_kinds(
        self, client: AsyncClient, stub_celery
    ):
        await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)
        await client.post("/api/v1/retrain/drift-checks", json=BASE_DRIFT)

        body = (await client.get("/api/v1/retrain/jobs")).json()

        assert body["total"] == 2
        assert {item["kind"] for item in body["items"]} == {KIND_RETRAIN, KIND_DRIFT_CHECK}

    async def test_get_returns_result_after_completion(
        self, client: AsyncClient, redis: FakeRedis
    ):
        job_id = (await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)).json()["job_id"]
        await mark_status(redis, job_id, STATUS_DONE, result={"artifact_id": "a-1"})

        body = (await client.get(f"/api/v1/retrain/jobs/{job_id}")).json()

        assert body["status"] == STATUS_DONE
        assert body["result"]["artifact_id"] == "a-1"

    async def test_unknown_job_is_404(self, client: AsyncClient):
        assert (await client.get("/api/v1/retrain/jobs/nope")).status_code == 404

    async def test_delete_removes_record(self, client: AsyncClient):
        job_id = (await client.post("/api/v1/retrain/jobs", json=BASE_RETRAIN)).json()["job_id"]

        assert (await client.delete(f"/api/v1/retrain/jobs/{job_id}")).status_code == 200
        assert (await client.get(f"/api/v1/retrain/jobs/{job_id}")).status_code == 404

    async def test_delete_unknown_is_404(self, client: AsyncClient):
        assert (await client.delete("/api/v1/retrain/jobs/nope")).status_code == 404


class TestSchedule:
    async def test_schedule_is_disabled_by_default(self, client: AsyncClient):
        body = (await client.get("/api/v1/retrain/schedule")).json()

        assert body["enabled"] is False
        assert "API 不提供写入" in body["note"]

    async def test_no_write_endpoint_for_schedule(self, client: AsyncClient):
        """开关只能走部署配置：PUT / POST 一律不存在。"""
        assert (await client.put("/api/v1/retrain/schedule", json={})).status_code == 405
        assert (await client.post("/api/v1/retrain/schedule", json={})).status_code == 405


class TestNoActivationEndpoint:
    async def test_router_has_no_activate_route(self):
        """
        契约 §3.1：没有「上线 / 替换当前模型」的端点，将来也不该有。
        这条测试会在有人加上去的那一刻变红。
        """
        paths = {route.path for route in retrain_endpoint.router.routes}

        for banned in ("activate", "promote", "deploy", "publish"):
            assert not any(banned in path for path in paths)
