"""因子策略 API 端点测试（V3 Wave A-a / G1）

对应契约 docs/contracts/waveAa-factor-strategy-adapter.md §二.2 与 §四 验收 3。

这里自建 FastAPI 应用挂载路由，而不是用 `app.main.app`：`api/v1/router.py`
是主循环负责合并的共享文件，本 Wave 不改它。挂载方式与交付报告里给出的集成片段
完全一致，等于顺带把那段片段也测了。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import factor_strategy
from app.core.redis import get_redis
from app.data.models import Bar, Frequency, Market
from tests.fake_redis import FakeRedis

START = datetime(2024, 1, 2, tzinfo=UTC)
DAY = timedelta(days=1)
SYMBOLS = ["AAPL", "MSFT", "NVDA"]

BASE_SPEC = {
    "formula": "MOM20 ATR_RATIO DIV",
    "universe": SYMBOLS,
    "long_quantile": 0.4,
    "rebalance_days": 5,
    "portfolio_method": "equal_weight",
}


def _bars(symbol: str, offset: int, n: int = 150) -> list[Bar]:
    return [
        Bar(
            time=START + i * DAY,
            symbol=symbol,
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=(price := round(100.0 + 8 * math.sin((i + offset * 7) / 11.0) + i * 0.05, 4)),
            high=price * 1.001,
            low=price * 0.999,
            close=price,
            volume=500_000,
        )
        for i in range(n)
    ]


def _universe() -> dict[str, list[Bar]]:
    return {symbol: _bars(symbol, k) for k, symbol in enumerate(SYMBOLS)}


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(redis: FakeRedis) -> FastAPI:
    """与交付报告中给出的 router.py 集成片段一致的挂载方式。"""
    test_app = FastAPI()
    test_app.include_router(
        factor_strategy.router, prefix="/api/v1/factors", tags=["Factor Strategy"]
    )
    test_app.dependency_overrides[get_redis] = lambda: redis
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def stub_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """回测端点不该在单测里连数据库；行情用合成 bar。"""

    async def _fake_fetch(req):
        return _universe()

    monkeypatch.setattr(factor_strategy, "_fetch_universe", _fake_fetch)


# ── POST /factors/strategy/backtest ───────────────────────────────


class TestBacktestEndpoint:
    async def test_returns_equity_curve_and_metrics(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest", json={"spec": BASE_SPEC, "market": "US"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["spec"]["formula"] == BASE_SPEC["formula"]
        assert body["symbols"] == SYMBOLS
        assert len(body["equity_curve"]) > 0
        assert all(math.isfinite(p["value"]) for p in body["equity_curve"])
        assert body["n_fills"] > 0
        assert math.isfinite(body["metrics"]["total_return_pct"])

    async def test_unknown_token_is_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": BASE_SPEC | {"formula": "MOM20 NOPE ADD"}},
        )

        assert resp.status_code == 400
        assert "未知 token" in resp.json()["detail"]

    async def test_unknown_portfolio_method_is_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": BASE_SPEC | {"portfolio_method": "mystery"}},
        )

        assert resp.status_code == 400

    async def test_overlapping_quantiles_is_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": BASE_SPEC | {"long_quantile": 0.7, "short_quantile": 0.7}},
        )

        assert resp.status_code == 400

    async def test_too_small_universe_is_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": BASE_SPEC | {"universe": ["AAPL"]}},
        )

        assert resp.status_code == 422       # Pydantic min_length=2

    async def test_engine_failure_is_422(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("撮合器炸了")

        monkeypatch.setattr(factor_strategy, "_run_backtest", _boom)

        resp = await client.post("/api/v1/factors/strategy/backtest", json={"spec": BASE_SPEC})

        assert resp.status_code == 422
        assert "撮合器炸了" in resp.json()["detail"]


# ── POST /factors/strategy/promote ────────────────────────────────


class TestPromoteEndpoint:
    async def test_stores_named_strategy(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/promote",
            json={"name": "动量组合", "spec": BASE_SPEC, "note": "来自挖掘"},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "动量组合"
        assert body["spec"]["formula"] == BASE_SPEC["formula"]
        assert body["note"] == "来自挖掘"

    async def test_back_references_experiment(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        from app.quant.experiments.recorder import (
            ExperimentMetrics,
            build_record,
            get_experiment,
            save_experiment,
        )

        record = await save_experiment(
            redis,
            build_record(
                kind="genetic_mining", name="候选", market="US",
                symbols=SYMBOLS, metrics=ExperimentMetrics(fitness=0.3),
            ),
        )

        resp = await client.post(
            "/api/v1/factors/strategy/promote",
            json={"name": "提升策略", "spec": BASE_SPEC, "experiment_id": record.id},
        )

        assert resp.status_code == 201
        assert resp.json()["source_experiment_id"] == record.id
        updated = await get_experiment(redis, record.id)
        assert updated is not None
        assert updated.promoted_strategy == "提升策略"

    async def test_illegal_name_is_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/promote", json={"name": "坏:名字", "spec": BASE_SPEC}
        )

        assert resp.status_code == 400

    async def test_invalid_spec_is_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/promote",
            json={"name": "策略", "spec": BASE_SPEC | {"formula": "MOM20 MOM5"}},
        )

        assert resp.status_code == 400
        assert "不平衡" in resp.json()["detail"]

    async def test_missing_name_is_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/factors/strategy/promote", json={"spec": BASE_SPEC})

        assert resp.status_code == 422


# ── GET /factors/strategy ─────────────────────────────────────────


class TestListEndpoint:
    async def test_empty_by_default(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/factors/strategy")

        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "strategies": []}

    async def test_lists_promoted_strategies_with_full_spec(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/factors/strategy/promote", json={"name": "策略A", "spec": BASE_SPEC}
        )

        body = (await client.get("/api/v1/factors/strategy")).json()

        assert body["count"] == 1
        assert body["strategies"][0]["spec"]["universe"] == SYMBOLS

    async def test_methods_endpoint_lists_portfolio_methods(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/factors/strategy/methods")

        assert resp.status_code == 200
        assert "equal_weight" in resp.json()["methods"]


# ── DELETE /factors/strategy/{name} ───────────────────────────────


class TestDeleteEndpoint:
    async def test_deletes_existing(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/factors/strategy/promote", json={"name": "策略A", "spec": BASE_SPEC}
        )

        resp = await client.delete("/api/v1/factors/strategy/策略A")

        assert resp.status_code == 204
        assert (await client.get("/api/v1/factors/strategy")).json()["count"] == 0

    async def test_unknown_name_is_404(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/v1/factors/strategy/不存在")

        assert resp.status_code == 404

    async def test_illegal_name_is_400(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/v1/factors/strategy/坏*名")

        assert resp.status_code == 400


# ── 因子库条目直接建策略（无需 expr → RPN 转译）────────────────────


class TestLibraryFactors:
    @pytest.mark.asyncio
    async def test_lists_factors_with_groups(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/factors/library")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["count"] > 0
        assert payload["groups"]
        # expr 是展示用标注，前端要显示；name 才是 library_factor 该填的值
        assert {"name", "label", "group", "expr"} <= set(payload["factors"][0])

    @pytest.mark.asyncio
    async def test_filters_by_group(self, client: AsyncClient) -> None:
        group = (await client.get("/api/v1/factors/library")).json()["groups"][0]

        resp = await client.get("/api/v1/factors/library", params={"group": group})

        assert resp.status_code == 200
        assert {f["group"] for f in resp.json()["factors"]} == {group}

    @pytest.mark.asyncio
    async def test_library_factor_backtest_runs(self, client: AsyncClient) -> None:
        spec = {k: v for k, v in BASE_SPEC.items() if k != "formula"}
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": spec | {"library_factor": "KMID"}, "market": "US"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["spec"]["library_factor"] == "KMID"
        assert body["equity_curve"]

    @pytest.mark.asyncio
    async def test_both_formula_and_library_factor_is_400(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": BASE_SPEC | {"library_factor": "KMID"}, "market": "US"},
        )

        assert resp.status_code == 400
        assert "二选一" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_neither_formula_nor_library_factor_is_400(
        self, client: AsyncClient
    ) -> None:
        spec = {k: v for k, v in BASE_SPEC.items() if k != "formula"}
        resp = await client.post(
            "/api/v1/factors/strategy/backtest", json={"spec": spec, "market": "US"}
        )

        assert resp.status_code == 400
        assert "二选一" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_library_factor_suggests_similar_names(
        self, client: AsyncClient
    ) -> None:
        spec = {k: v for k, v in BASE_SPEC.items() if k != "formula"}
        resp = await client.post(
            "/api/v1/factors/strategy/backtest",
            json={"spec": spec | {"library_factor": "KMIDX"}, "market": "US"},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "未知因子库条目" in detail
        # 因子库有数百条，全列出来没帮助；给同前缀的候选才有用
        assert "KMID" in detail
