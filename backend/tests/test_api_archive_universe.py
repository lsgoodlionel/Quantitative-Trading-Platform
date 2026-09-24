"""归档 / 宇宙 API 测试（Wave M-a）

这两个路由尚未在 `app/api/v1/router.py` 注册（并行开发期该文件禁改），
测试自建一个 FastAPI 应用按**预期前缀**挂载 —— 同时也是给主循环的集成说明书。

覆盖：
- 归档列表 / 删除 / 删除不存在 → 404
- 路径穿越 symbol → 400（而不是 500 或真的写到别处）
- 下载任务提交：参数非法 → 400；broker 不可用 → 503；正常 → task_id
- 市值宇宙返回体带 as_of
- 指数成分股取历史日期 → 422（不静默返回最新）
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from importlib import import_module

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import data_archive, universe
from app.data.archive import ArchiveKey, create_archive
from app.data.models import Bar, Frequency, Market
from app.data.screener import Candidate
from app.data.universe.index_components import ComponentSnapshot, IndexSpec

_INDEX_MOD = import_module("app.data.universe.index_components")
_MCAP_MOD = import_module("app.data.universe.market_cap")


# ── 应用装配（= 主循环需要在 router.py 里补的两行）────────────
def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(data_archive.router, prefix="/api/v1/data", tags=["Data Archive"])
    app.include_router(universe.router, prefix="/api/v1/universe", tags=["Universe"])
    return app


@pytest.fixture
def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_build_app()), base_url="http://test")


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "archive_root", str(tmp_path / "arc"))
    from app.data import archive as archive_pkg

    archive_pkg.reset_archive_cache()
    yield tmp_path / "arc"
    archive_pkg.reset_archive_cache()


def _bar(day: int) -> Bar:
    return Bar(
        time=datetime(2024, 3, day, 20, 0, tzinfo=UTC), symbol="AAPL", market=Market.US,
        frequency=Frequency.DAY_1, open=99, high=102, low=97, close=100, volume=1_000,
    )


# ── 归档列表 / 删除 ───────────────────────────────────────────
class TestArchiveListing:
    @pytest.mark.asyncio
    async def test_empty_archive_lists_nothing(self, client, archive_root) -> None:
        async with client as http:
            resp = await http.get("/api/v1/data/archive")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["enabled"] is False   # 默认关闭

    @pytest.mark.asyncio
    async def test_lists_written_entry_with_coverage(self, client, archive_root) -> None:
        create_archive().write(
            ArchiveKey("AAPL", Market.US, Frequency.DAY_1), [_bar(1), _bar(5)]
        )

        async with client as http:
            resp = await http.get("/api/v1/data/archive")

        entry = resp.json()["entries"][0]
        assert entry == {
            "symbol": "AAPL", "market": "US", "frequency": "1d",
            "start": "2024-03-01", "end": "2024-03-05",
        }

    @pytest.mark.asyncio
    async def test_delete_existing_then_missing(self, client, archive_root) -> None:
        create_archive().write(ArchiveKey("AAPL", Market.US, Frequency.DAY_1), [_bar(1)])

        async with client as http:
            first = await http.delete("/api/v1/data/archive/US/1d/AAPL")
            second = await http.delete("/api/v1/data/archive/US/1d/AAPL")

        assert first.status_code == 204
        assert second.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_rejects_path_traversal_symbol(self, client, archive_root) -> None:
        async with client as http:
            resp = await http.delete("/api/v1/data/archive/US/1d/..%2F..%2Fetc")

        # 400（非法代码）或 404（路由不匹配）都可接受；绝不能是 5xx 或 204
        assert resp.status_code in (400, 404)


# ── 下载任务提交 ──────────────────────────────────────────────
class TestArchiveDownload:
    @pytest.mark.asyncio
    async def test_rejects_inverted_window(self, client, archive_root) -> None:
        async with client as http:
            resp = await http.post(
                "/api/v1/data/archive/download",
                json={"symbols": ["AAPL"], "market": "US", "frequency": "1d",
                      "start": "2024-03-05", "end": "2024-03-01"},
            )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_traversal_symbol(self, client, archive_root) -> None:
        async with client as http:
            resp = await http.post(
                "/api/v1/data/archive/download",
                json={"symbols": ["../../etc/passwd"], "market": "US"},
            )

        assert resp.status_code == 400
        assert "非法路径字符" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_broker_unavailable_returns_503(self, client, archive_root, monkeypatch) -> None:
        from app.tasks import archive as task_mod

        def _boom(**kwargs):
            raise ConnectionError("broker down")

        monkeypatch.setattr(task_mod.download_archive, "delay", _boom)

        async with client as http:
            resp = await http.post(
                "/api/v1/data/archive/download", json={"symbols": ["AAPL"], "market": "US"}
            )

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_returns_task_id(self, client, archive_root, monkeypatch) -> None:
        from app.tasks import archive as task_mod

        class _Task:
            id = "task-123"

        monkeypatch.setattr(task_mod.download_archive, "delay", lambda **kw: _Task())

        async with client as http:
            resp = await http.post(
                "/api/v1/data/archive/download", json={"symbols": ["AAPL", "MSFT"]}
            )

        assert resp.json() == {"task_id": "task-123", "symbols": 2}


# ── 宇宙 API ──────────────────────────────────────────────────
class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def read(self, key: str):
        raw = self.store.get(key)
        return json.loads(raw) if raw else None

    def write(self, key: str, value, ttl: int) -> None:
        self.store[key] = json.dumps(value)


@pytest.fixture
def universe_cache(monkeypatch) -> _MemoryCache:
    memory = _MemoryCache()
    for module in (_MCAP_MOD, _INDEX_MOD):
        monkeypatch.setattr(module, "read_json", memory.read)
        monkeypatch.setattr(module, "write_json", memory.write)
    return memory


@pytest.fixture
def fake_snapshot(monkeypatch):
    async def _snapshot(market: Market) -> list[Candidate]:
        return [
            Candidate(symbol="AAPL", market="US", name="Apple", market_cap=3.0e12),
            Candidate(symbol="MSFT", market="US", name="Microsoft", market_cap=2.8e12),
        ]

    monkeypatch.setattr("app.data.screener.get_snapshot", _snapshot)


class TestUniverseApi:
    @pytest.mark.asyncio
    async def test_market_cap_response_carries_as_of(
        self, client, universe_cache, fake_snapshot
    ) -> None:
        async with client as http:
            resp = await http.get("/api/v1/universe/market-cap?market=US&top_n=2")

        body = resp.json()
        assert resp.status_code == 200
        assert body["symbols"] == ["AAPL", "MSFT"]
        assert body["as_of"]
        assert body["items"][0]["market_cap_yi"] == 30000.0

    @pytest.mark.asyncio
    async def test_exclude_query_is_applied(
        self, client, universe_cache, fake_snapshot
    ) -> None:
        async with client as http:
            resp = await http.get("/api/v1/universe/market-cap?top_n=2&exclude=AAPL")

        assert resp.json()["symbols"] == ["MSFT"]

    @pytest.mark.asyncio
    async def test_supported_indexes_listed(self, client) -> None:
        async with client as http:
            resp = await http.get("/api/v1/universe/indexes")

        codes = [item["code"] for item in resp.json()]
        assert "000300" in codes

    @pytest.mark.asyncio
    async def test_unknown_index_returns_404(self, client) -> None:
        async with client as http:
            resp = await http.get("/api/v1/universe/index/SPX")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_latest_components_ok(self, client, monkeypatch) -> None:
        _patch_components(monkeypatch, ["600519", "000001"], date(2024, 6, 30))

        async with client as http:
            resp = await http.get("/api/v1/universe/index/HS300")

        assert resp.json()["symbols"] == ["600519", "000001"]
        assert resp.json()["as_of"] == "2024-06-30"

    @pytest.mark.asyncio
    async def test_historical_date_returns_422(self, client, monkeypatch) -> None:
        _patch_components(monkeypatch, ["600519"], date(2024, 6, 30))

        async with client as http:
            resp = await http.get("/api/v1/universe/index/000300?on=2020-01-01")

        assert resp.status_code == 422
        assert "幸存者偏差" in resp.json()["detail"]


def _patch_components(monkeypatch, symbols: list[str], as_of: date | None) -> None:
    async def _fake_load(spec: IndexSpec) -> ComponentSnapshot:
        return ComponentSnapshot(spec.code, spec.name, tuple(symbols), as_of)

    monkeypatch.setattr(_INDEX_MOD, "_load_snapshot", _fake_load)
