"""
投研产物库测试（V4 · M4）

覆盖契约 §3.2 的四条验收：
1. 三类产物（dataset / model / signal）各自的 存 → 列 → 读 → 删
2. checksum 不匹配时拒绝加载（篡改内容文件后断言抛错）
3. artifact_id 由服务端生成；用户传的 name 含 "../" 不影响存储路径
4. 实验记录被淘汰后产物仍可加载 —— 这是 M4 存在的理由

元数据仓储用内存实现替换 Postgres（语义与 `PostgresArtifactMetaStore` 一致），
内容仓储用 tmp_path 下的真实文件系统 —— 篡改校验必须打到真实文件才算数。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.quant.experiments.recorder import (
    ExperimentMetrics,
    build_record,
    delete_experiment,
    get_experiment,
    save_experiment,
)
from app.quant.lab import (
    CAPACITY_POLICY,
    ArtifactChecksumError,
    ArtifactFilter,
    ArtifactKind,
    ArtifactKindMismatchError,
    ArtifactMeta,
    ArtifactNotFoundError,
    FileSystemContentStore,
    InvalidArtifactIdError,
    LabStore,
    default_lab_root,
)
from app.quant.lab.content import LAB_ROOT_ENV
from tests.fake_redis import FakeRedis

# ── 内存元数据仓储 ────────────────────────────────────────────────


class InMemoryMetaStore:
    """`ArtifactMetaStore` 的内存实现，语义与 Postgres 版一致。"""

    def __init__(self) -> None:
        self._rows: dict[str, ArtifactMeta] = {}
        self._order: list[str] = []

    async def save(self, meta: ArtifactMeta) -> ArtifactMeta:
        self._rows[meta.artifact_id] = meta
        self._order.insert(0, meta.artifact_id)      # created_at DESC
        return meta

    async def get(self, artifact_id: str) -> ArtifactMeta | None:
        return self._rows.get(artifact_id)

    async def list(self, filters: ArtifactFilter, limit: int, offset: int):
        matched = [self._rows[i] for i in self._order if _matches(self._rows[i], filters)]
        return matched[offset : offset + limit], len(matched)

    async def delete(self, artifact_id: str) -> bool:
        if artifact_id not in self._rows:
            return False
        del self._rows[artifact_id]
        self._order.remove(artifact_id)
        return True


class FailingMetaStore(InMemoryMetaStore):
    """save 必然失败 —— 用于验证内容文件的回滚。"""

    async def save(self, meta: ArtifactMeta) -> ArtifactMeta:
        raise RuntimeError("模拟落库失败")


def _matches(meta: ArtifactMeta, filters: ArtifactFilter) -> bool:
    if filters.kind is not None and meta.kind is not filters.kind:
        return False
    return not (
        filters.name_contains and filters.name_contains.lower() not in meta.name.lower()
    )


@pytest.fixture
def store(tmp_path) -> LabStore:
    return LabStore(InMemoryMetaStore(), FileSystemContentStore(tmp_path / "lab"))


@pytest.fixture
def content_store(tmp_path) -> FileSystemContentStore:
    return FileSystemContentStore(tmp_path / "lab")


def _make_dataset(n: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {"feature_a": range(n), "feature_b": [i * 0.5 for i in range(n)], "label": [i % 2 for i in range(n)]},
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


class DummyModel:
    """带 `detail()` 的最小模型产物（必须是模块级类，局部类 pickle 不了）。"""

    def detail(self) -> dict:
        return {"feature_importance": [{"name": "f0", "importance": 1.0}]}


def _make_signal(n: int = 20) -> pd.Series:
    return pd.Series(
        [i * 0.01 for i in range(n)],
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
        name="alpha",
    )


# ── 三类产物的存 → 列 → 读 → 删 ────────────────────────────────────


class TestRoundTrip:
    async def test_dataset_round_trip(self, store: LabStore) -> None:
        # Arrange
        dataset = _make_dataset()

        # Act
        meta = await store.save_dataset("训练集 2024", dataset, tags={"market": "US"})
        listed, total = await store.list(kind=ArtifactKind.DATASET)
        loaded = await store.load_dataset(meta.artifact_id)

        # Assert
        assert meta.kind is ArtifactKind.DATASET
        assert meta.size_bytes > 0
        assert meta.tags == {"market": "US"}
        assert total == 1
        assert [m.artifact_id for m in listed] == [meta.artifact_id]
        pd.testing.assert_frame_equal(loaded, dataset)

        assert await store.delete(meta.artifact_id) is True
        assert await store.get(meta.artifact_id) is None
        with pytest.raises(ArtifactNotFoundError):
            await store.load_dataset(meta.artifact_id)

    async def test_model_round_trip(self, store: LabStore) -> None:
        model = {"kind": "dummy", "weights": [1.0, 2.0, 3.0]}

        meta = await store.save_model("lasso-v1", model, tags={"trained_on": "2024"})
        loaded = await store.load_model(meta.artifact_id)

        assert meta.kind is ArtifactKind.MODEL
        assert loaded == model
        assert await store.delete(meta.artifact_id) is True

    async def test_signal_round_trip(self, store: LabStore) -> None:
        signal = _make_signal()

        meta = await store.save_signal("alpha-001", signal)
        loaded = await store.load_signal(meta.artifact_id)

        assert meta.kind is ArtifactKind.SIGNAL
        pd.testing.assert_series_equal(loaded, signal)
        assert await store.delete(meta.artifact_id) is True

    async def test_list_filters_by_kind_and_name(self, store: LabStore) -> None:
        await store.save_dataset("momentum-dataset", _make_dataset())
        await store.save_signal("momentum-signal", _make_signal())
        await store.save_signal("reversal-signal", _make_signal())

        signals, signal_total = await store.list(kind=ArtifactKind.SIGNAL)
        momentum, momentum_total = await store.list(name_contains="momentum")

        assert signal_total == 2
        assert {m.kind for m in signals} == {ArtifactKind.SIGNAL}
        assert momentum_total == 2
        assert {m.name for m in momentum} == {"momentum-dataset", "momentum-signal"}

    async def test_delete_missing_returns_false(self, store: LabStore) -> None:
        from app.quant.lab import new_artifact_id

        assert await store.delete(new_artifact_id()) is False

    async def test_delete_reports_true_for_orphaned_content(self, tmp_path) -> None:
        """元数据丢了但内容文件还在：删除确实清理了东西，就不该报 False。"""
        from app.quant.lab import new_artifact_id

        meta_store = InMemoryMetaStore()
        content = FileSystemContentStore(tmp_path / "lab")
        store = LabStore(meta_store, content)
        orphan_id = new_artifact_id()
        content.write(orphan_id, b"orphaned payload")

        assert await store.delete(orphan_id) is True
        assert content.exists(orphan_id) is False

    async def test_load_with_wrong_kind_is_rejected(self, store: LabStore) -> None:
        meta = await store.save_signal("alpha-001", _make_signal())

        with pytest.raises(ArtifactKindMismatchError):
            await store.load_model(meta.artifact_id)

    async def test_save_rejects_wrong_python_type(self, store: LabStore) -> None:
        with pytest.raises(TypeError):
            await store.save_dataset("bad", _make_signal())
        with pytest.raises(TypeError):
            await store.save_signal("bad", _make_dataset())


# ── checksum 校验 ─────────────────────────────────────────────────


class TestChecksumGuard:
    async def test_tampered_content_is_rejected(self, tmp_path) -> None:
        # Arrange：正常存一份模型
        content = FileSystemContentStore(tmp_path / "lab")
        store = LabStore(InMemoryMetaStore(), content)
        meta = await store.save_model("lasso-v1", {"weights": [1.0]})

        # Act：绕过 API 直接篡改磁盘上的内容文件
        path = content.path_for(meta.artifact_id)
        path.write_bytes(b"tampered-payload")

        # Assert：加载被拒，且错误信息带上两侧哈希便于排查
        with pytest.raises(ArtifactChecksumError) as excinfo:
            await store.load_model(meta.artifact_id)
        assert meta.checksum in str(excinfo.value)

    async def test_checksum_is_content_hash(self, store: LabStore) -> None:
        import hashlib

        from app.quant.lab.base import serialize

        payload = {"weights": [1.0, 2.0]}
        meta = await store.save_model("m", payload)

        assert meta.checksum == hashlib.sha256(serialize(payload)).hexdigest()
        assert len(meta.checksum) == 64

    async def test_missing_content_file_raises_not_found(self, tmp_path) -> None:
        content = FileSystemContentStore(tmp_path / "lab")
        store = LabStore(InMemoryMetaStore(), content)
        meta = await store.save_model("m", {"a": 1})

        content.path_for(meta.artifact_id).unlink()

        with pytest.raises(ArtifactNotFoundError):
            await store.load_model(meta.artifact_id)


# ── 路径安全 ──────────────────────────────────────────────────────


class TestPathSafety:
    async def test_artifact_id_is_server_generated_uuid(self, store: LabStore) -> None:
        import uuid

        meta = await store.save_model("whatever", {"a": 1})

        assert str(uuid.UUID(meta.artifact_id)) == meta.artifact_id

    async def test_malicious_name_never_reaches_the_path(self, tmp_path) -> None:
        # Arrange
        root = tmp_path / "lab"
        content = FileSystemContentStore(root)
        store = LabStore(InMemoryMetaStore(), content)
        evil_name = "../../../../etc/passwd"

        # Act
        meta = await store.save_model(evil_name, {"a": 1})
        path = content.path_for(meta.artifact_id)

        # Assert：name 原样保留在元数据里（只是标签），路径完全由 UUID 决定
        assert meta.name == evil_name
        assert root.resolve() in path.resolve().parents
        assert meta.artifact_id in path.name
        assert "etc" not in str(path)
        assert await store.load_model(meta.artifact_id) == {"a": 1}

    @pytest.mark.parametrize(
        "bad_id",
        ["../../etc/passwd", "not-a-uuid", "", "a" * 36, "..", "/absolute/path"],
    )
    async def test_non_uuid_ids_are_rejected(self, store: LabStore, bad_id: str) -> None:
        with pytest.raises(InvalidArtifactIdError):
            await store.load_model(bad_id)
        with pytest.raises(InvalidArtifactIdError):
            await store.delete(bad_id)

    def test_content_path_is_under_root(self, content_store: FileSystemContentStore) -> None:
        from app.quant.lab import new_artifact_id

        path = content_store.path_for(new_artifact_id())

        assert content_store.root.resolve() in path.resolve().parents

    def test_default_root_honours_env(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv(LAB_ROOT_ENV, str(tmp_path / "custom"))
        assert default_lab_root() == tmp_path / "custom"

        monkeypatch.setenv(LAB_ROOT_ENV, "")
        assert default_lab_root().name == "lab"


# ── 内容仓储自身 ──────────────────────────────────────────────────


class TestContentStore:
    def test_write_read_delete(self, content_store: FileSystemContentStore) -> None:
        from app.quant.lab import new_artifact_id

        artifact_id = new_artifact_id()

        assert content_store.exists(artifact_id) is False
        assert content_store.write(artifact_id, b"hello") == 5
        assert content_store.exists(artifact_id) is True
        assert content_store.read(artifact_id) == b"hello"
        assert content_store.delete(artifact_id) is True
        assert content_store.delete(artifact_id) is False

    def test_read_missing_raises(self, content_store: FileSystemContentStore) -> None:
        from app.quant.lab import new_artifact_id

        with pytest.raises(ArtifactNotFoundError):
            content_store.read(new_artifact_id())

    def test_no_tmp_file_left_behind(self, content_store: FileSystemContentStore) -> None:
        from app.quant.lab import new_artifact_id

        content_store.write(new_artifact_id(), b"payload")

        assert list(content_store.root.rglob("*.tmp")) == []

    async def test_meta_failure_rolls_back_content(self, tmp_path) -> None:
        content = FileSystemContentStore(tmp_path / "lab")
        store = LabStore(FailingMetaStore(), content)

        with pytest.raises(RuntimeError, match="模拟落库失败"):
            await store.save_model("m", {"a": 1})

        # 落库失败不得留下查不到的孤儿文件
        assert list(content.root.rglob("*.pkl")) == []


# ── 与实验记录器的关系（M4 存在的理由） ─────────────────────────────


class TestArtifactOutlivesExperiment:
    async def test_artifact_survives_experiment_eviction(self, store: LabStore) -> None:
        # Arrange：存一份模型产物，并让一条实验记录引用它
        redis = FakeRedis()
        meta = await store.save_model("swept-model", {"weights": [1.0]})
        record = build_record(
            kind="factor_analysis",
            name="扫参实验",
            market="US",
            symbols=["AAPL"],
            metrics=ExperimentMetrics(ic_mean=0.05),
            artifact_ids=[meta.artifact_id],
        )
        await save_experiment(redis, record)
        assert (await get_experiment(redis, record.id)).artifact_ids == [meta.artifact_id]

        # Act：实验记录被淘汰（模拟 MAX_RECORDS 滚动删除）
        await delete_experiment(redis, record.id)

        # Assert：实验没了，产物照样加载得到 —— 产物有独立生命周期
        assert await get_experiment(redis, record.id) is None
        assert await store.load_model(meta.artifact_id) == {"weights": [1.0]}
        assert (await store.get(meta.artifact_id)) is not None

    async def test_experiment_defaults_to_no_artifacts(self) -> None:
        record = build_record(
            kind="formula_factor",
            name="无产物实验",
            market="US",
            symbols=["AAPL"],
            metrics=ExperimentMetrics(),
        )
        assert record.artifact_ids == []
        assert record.to_dict()["artifact_ids"] == []


# ── 容量策略可见 ──────────────────────────────────────────────────


class TestCapacityPolicy:
    def test_policy_is_unlimited_manual_delete(self) -> None:
        assert CAPACITY_POLICY == "unlimited-manual-delete"

    async def test_many_artifacts_are_all_retained(self, store: LabStore) -> None:
        """不限容量：写入远超实验记录器 MAX_RECORDS 感觉的量也不淘汰。"""
        ids = [(await store.save_signal(f"s-{i}", _make_signal(3))).artifact_id for i in range(30)]

        _, total = await store.list(limit=1)

        assert total == 30
        assert await store.load_signal(ids[0]) is not None


# ── 元数据投影 ────────────────────────────────────────────────────


class TestLabApi:
    """产物库 API：路由挂在测试本地的 app 上（router.py 是并行开发期的禁改共享文件）。"""

    @pytest.fixture
    def api(self, tmp_path):
        from fastapi import FastAPI

        from app.api.v1.endpoints import lab as lab_endpoints

        store = LabStore(InMemoryMetaStore(), FileSystemContentStore(tmp_path / "lab"))
        app = FastAPI()
        app.include_router(lab_endpoints.router, prefix="/api/v1/lab")
        app.dependency_overrides[lab_endpoints.get_store] = lambda: store
        return app, store

    async def _client(self, app):
        from httpx import ASGITransport, AsyncClient

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_list_exposes_capacity_policy(self, api) -> None:
        app, store = api
        await store.save_dataset("训练集", _make_dataset())

        async with await self._client(app) as client:
            resp = await client.get("/api/v1/lab/artifacts")

        body = resp.json()
        assert resp.status_code == 200
        assert body["total"] == 1
        assert body["capacity_policy"] == CAPACITY_POLICY
        assert "不设容量上限" in body["capacity_note"]

    async def test_list_filters(self, api) -> None:
        app, store = api
        await store.save_dataset("momentum", _make_dataset())
        await store.save_signal("reversal", _make_signal())

        async with await self._client(app) as client:
            by_kind = await client.get("/api/v1/lab/artifacts", params={"kind": "signal"})
            by_name = await client.get(
                "/api/v1/lab/artifacts", params={"name_contains": "moment"}
            )

        assert by_kind.json()["total"] == 1
        assert by_name.json()["items"][0]["name"] == "momentum"

    async def test_get_and_delete(self, api) -> None:
        app, store = api
        meta = await store.save_signal("alpha", _make_signal())

        async with await self._client(app) as client:
            detail = await client.get(f"/api/v1/lab/artifacts/{meta.artifact_id}")
            removed = await client.delete(f"/api/v1/lab/artifacts/{meta.artifact_id}")
            missing = await client.get(f"/api/v1/lab/artifacts/{meta.artifact_id}")
            deleted_again = await client.delete(f"/api/v1/lab/artifacts/{meta.artifact_id}")

        assert detail.json()["name"] == "alpha"
        assert removed.json()["deleted"] is True
        assert missing.status_code == 404
        assert deleted_again.status_code == 404

    async def test_invalid_id_is_400(self, api) -> None:
        app, _ = api

        async with await self._client(app) as client:
            resp = await client.get("/api/v1/lab/artifacts/not-a-uuid")

        assert resp.status_code == 400

    async def test_preview_dataset_and_signal(self, api) -> None:
        app, store = api
        dataset_meta = await store.save_dataset("训练集", _make_dataset())
        signal_meta = await store.save_signal("alpha", _make_signal())

        async with await self._client(app) as client:
            dataset = await client.get(
                f"/api/v1/lab/artifacts/{dataset_meta.artifact_id}/preview",
                params={"rows": 3},
            )
            signal = await client.get(
                f"/api/v1/lab/artifacts/{signal_meta.artifact_id}/preview"
            )

        assert dataset.json()["columns"] == ["feature_a", "feature_b", "label"]
        assert len(dataset.json()["rows"]) == 3
        assert dataset.json()["total_rows"] == 20
        assert signal.json()["kind"] == "signal"
        assert len(signal.json()["rows"]) == 10

    async def test_preview_rejects_model(self, api) -> None:
        app, store = api
        meta = await store.save_model("m", {"a": 1})

        async with await self._client(app) as client:
            resp = await client.get(f"/api/v1/lab/artifacts/{meta.artifact_id}/preview")

        assert resp.status_code == 400
        assert "detail" in resp.json()["detail"]

    async def test_tampered_content_returns_409(self, api, tmp_path) -> None:
        app, store = api
        meta = await store.save_model("m", {"a": 1})
        FileSystemContentStore(tmp_path / "lab").path_for(meta.artifact_id).write_bytes(b"evil")

        async with await self._client(app) as client:
            resp = await client.get(f"/api/v1/lab/artifacts/{meta.artifact_id}/detail")

        assert resp.status_code == 409

    async def test_model_detail_endpoint(self, api) -> None:
        app, store = api

        with_detail = await store.save_model("with-detail", DummyModel())
        without_detail = await store.save_model("plain", {"a": 1})

        async with await self._client(app) as client:
            ok = await client.get(f"/api/v1/lab/artifacts/{with_detail.artifact_id}/detail")
            bad = await client.get(f"/api/v1/lab/artifacts/{without_detail.artifact_id}/detail")

        assert ok.json()["detail"]["feature_importance"][0]["name"] == "f0"
        assert bad.status_code == 400


class TestPostgresMetaMapping:
    """Postgres 仓储里不依赖数据库的纯函数（SQL 执行路径需真实库，与 A-d 同处理）。"""

    def test_as_tags_accepts_dict_and_json_string(self) -> None:
        from app.quant.lab.metadata import _as_tags

        assert _as_tags({"a": 1}) == {"a": "1"}
        assert _as_tags('{"a": "b"}') == {"a": "b"}
        assert _as_tags(None) == {}
        assert _as_tags("not json") == {}
        assert _as_tags("[1, 2]") == {}

    def test_build_where_composes_filters(self) -> None:
        from app.quant.lab.metadata import _build_where

        empty_clause, empty_params = _build_where(ArtifactFilter())
        clause, params = _build_where(
            ArtifactFilter(kind=ArtifactKind.MODEL, name_contains="lasso")
        )

        assert empty_clause == ""
        assert empty_params == {}
        assert clause == "WHERE kind = :kind AND name ILIKE :name_pattern"
        assert params == {"kind": "model", "name_pattern": "%lasso%"}

    def test_row_to_meta(self) -> None:
        from types import SimpleNamespace

        from app.quant.lab.metadata import _row_to_meta

        created = datetime(2024, 5, 1, tzinfo=UTC)
        row = SimpleNamespace(
            artifact_id="0f7d2a4e-1c3b-4f5a-9d6e-8b0c1d2e3f40",
            kind="signal",
            name="alpha",
            created_at=created,
            size_bytes=42,
            tags={"k": "v"},
            checksum="abc",
        )

        meta = _row_to_meta(row)

        assert meta.kind is ArtifactKind.SIGNAL
        assert meta.created_at == created
        assert meta.size_bytes == 42
        assert meta.tags == {"k": "v"}


class TestArtifactMeta:
    def test_to_dict_is_json_friendly(self) -> None:
        import json

        meta = ArtifactMeta(
            artifact_id="0f7d2a4e-1c3b-4f5a-9d6e-8b0c1d2e3f40",
            kind=ArtifactKind.MODEL,
            name="m",
            created_at=datetime(2024, 5, 1, tzinfo=UTC),
            size_bytes=128,
            tags={"a": "b"},
            checksum="deadbeef",
        )

        payload = meta.to_dict()

        assert payload["kind"] == "model"
        assert payload["created_at"].startswith("2024-05-01")
        assert json.dumps(payload)      # 不抛异常即可 JSON 化
