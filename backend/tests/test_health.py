import asyncio
import time
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import health as health_probes
from app.core.health import CheckResult
from app.core.version import APP_VERSION
from app.main import app


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


async def get_json(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


def stub_check(result: CheckResult, calls: list[str] | None = None, name: str = ""):
    async def _stub(**_kwargs: object) -> CheckResult:
        if calls is not None:
            calls.append(name)
        return result

    return _stub


OK = CheckResult(ok=True, latency_ms=1.0)
DEAD = CheckResult(ok=False, error="Connection refused")


@pytest.mark.asyncio
async def test_health_check() -> None:
    response = await get_json("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


class TestLiveness:
    """存活探针：进程活着就 200，不查依赖。

    一旦它去查 Postgres/Redis，一次依赖抖动就会被编排系统翻译成「杀掉容器」，
    而重启修不好外部依赖，只会让集群在滚动重启里反复横跳。
    """

    async def test_does_not_touch_any_dependency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(OK, calls, "postgres"))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(OK, calls, "redis"))

        response = await get_json("/health")

        assert response.status_code == 200
        assert calls == []

    async def test_stays_200_when_every_dependency_is_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(DEAD))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(DEAD))

        response = await get_json("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReadiness:
    async def test_all_dependencies_up_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(OK))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(OK))

        response = await get_json("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["checks"]["postgres"]["ok"] is True
        assert body["checks"]["postgres"]["latency_ms"] == 1.0

    async def test_postgres_down_is_down_and_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 核心数据没了 —— 必须让负载均衡器把流量摘走。
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(DEAD))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(OK))

        response = await get_json("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "down"
        assert body["checks"]["postgres"]["error"] == "Connection refused"

    async def test_only_redis_down_is_degraded_and_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 缓存/事件降级，主要功能仍在。全判成 down 会让一次缓存抖动摘掉整个集群。
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(OK))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(DEAD))

        response = await get_json("/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    async def test_both_down_is_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(DEAD))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(DEAD))

        response = await get_json("/health/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "down"

    async def test_endpoint_survives_a_hanging_dependency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def never_returns() -> None:
            await asyncio.sleep(30)

        async def hanging_postgres(**_kwargs: object) -> CheckResult:
            return await health_probes.run_probe(never_returns, timeout=0.05)

        monkeypatch.setattr(health_probes, "check_postgres", hanging_postgres)
        monkeypatch.setattr(health_probes, "check_redis", stub_check(OK))

        started = time.perf_counter()
        response = await get_json("/health/ready")
        elapsed = time.perf_counter() - started

        assert elapsed < 5.0, "探测超时没有被兜住，端点被挂住了"
        assert response.status_code == 503
        assert "timed out" in response.json()["checks"]["postgres"]["error"]


class TestProbeMechanics:
    async def test_timeout_is_reported_not_raised(self) -> None:
        async def never_returns() -> None:
            await asyncio.sleep(30)

        started = time.perf_counter()
        result = await health_probes.run_probe(never_returns, timeout=0.05)
        elapsed = time.perf_counter() - started

        assert result.ok is False
        assert "timed out" in (result.error or "")
        assert elapsed < 2.0

    async def test_probe_failure_becomes_a_result(self) -> None:
        async def boom() -> None:
            raise RuntimeError("Connection refused")

        result = await health_probes.run_probe(boom)

        assert result.ok is False
        assert result.error == "Connection refused"

    async def test_success_records_latency(self) -> None:
        async def fine() -> None:
            return None

        result = await health_probes.run_probe(fine)

        assert result.ok is True
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    def test_connection_string_credentials_are_redacted(self) -> None:
        # /health/ready 是未鉴权端点，错误信息直接吐给调用方。
        exc = OSError(
            "could not connect to postgresql+asyncpg://quantbot:hunter2@db:5432/quantbot"
        )

        message = health_probes.sanitize_error(exc)

        assert "hunter2" not in message
        assert "***@" in message

    def test_long_errors_are_truncated(self) -> None:
        message = health_probes.sanitize_error(RuntimeError("x" * 5000))

        assert len(message) < 300


class TestVersionHasOneSource:
    async def test_both_health_endpoints_report_the_same_version(self) -> None:
        root = await get_json("/health")
        versioned = await get_json("/api/v1/health")

        assert root.json()["version"] == versioned.json()["version"] == APP_VERSION

    async def test_readiness_reports_the_same_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(health_probes, "check_postgres", stub_check(OK))
        monkeypatch.setattr(health_probes, "check_redis", stub_check(OK))

        response = await get_json("/health/ready")

        assert response.json()["version"] == APP_VERSION

    def test_pyproject_version_matches_the_constant(self) -> None:
        # 两处各留一份版本号，发版时必然漏改一处。
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]

        assert declared == APP_VERSION


@pytest.mark.asyncio
async def test_bars_endpoint_returns_empty() -> None:
    from app.api.v1.endpoints.bars import get_service
    mock_svc = MagicMock()
    mock_svc.get_bars = AsyncMock(return_value=[])
    app.dependency_overrides[get_service] = lambda: mock_svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US&frequency=1d")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "AAPL"
    assert isinstance(data["bars"], list)


@pytest.mark.asyncio
async def test_strategy_presets_returns_list() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/strategies/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) >= 8   # 已从 8 扩展至 16 种策略
    preset_ids = {p["name"] for p in presets}   # 预设以 name 为标识
    assert "double_ma" in preset_ids
    assert "bollinger" in preset_ids
    assert "multi_factor" in preset_ids


@pytest.mark.asyncio
async def test_risk_config_returns_default() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/risk")
    assert response.status_code == 200
    config = response.json()
    assert len(config["rules"]) > 0
    rule_types = {r["rule_type"] for r in config["rules"]}
    assert "max_drawdown" in rule_types
    assert "daily_loss_limit" in rule_types
