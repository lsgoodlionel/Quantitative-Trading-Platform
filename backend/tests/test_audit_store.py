"""
审计落库测试（V3 Wave C-b · J3）

对应契约 docs/contracts/waveCb-reconcile-audit.md §三 验收 4：

1. 写失败记 error 而非 debug，且不阻断主流程
2. 无 UPDATE/DELETE 端点（反射断言路由表）

Redis 用 stream 假件，Postgres 用内存仓储 —— **不连任何真实服务**。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI

from app.core import audit as audit_mod
from app.core.audit import (
    AUDIT_STREAM,
    AuditAction,
    audit_failure_count,
    audit_log,
    parse_entry,
    reset_audit_failure_count,
)
from app.data.storage.audit_log import AuditEntry, build_entry

# ── 假件 ─────────────────────────────────────────────────────────

class FakeStreamRedis:
    """只实现 xadd / xrevrange 的 Redis 假件。"""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self._fail = fail
        self.closed = False

    async def xadd(self, key, fields, maxlen=None, approximate=True) -> str:
        if self._fail:
            raise self._fail
        assert key == AUDIT_STREAM
        entry_id = f"{len(self.entries) + 1}-0"
        self.entries.append((entry_id, dict(fields)))
        return entry_id

    async def xrevrange(self, key, max="+", min="-", count=None):
        return list(reversed(self.entries))[:count]

    async def aclose(self) -> None:
        self.closed = True


class InMemoryAuditStore:
    """`AuditLogStore` 的内存实现。**刻意只有 append / list。**"""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.rows: list[AuditEntry] = []
        self._fail = fail

    async def append(self, entry: AuditEntry) -> None:
        if self._fail:
            raise self._fail
        self.rows.append(entry)

    async def list(self, *, action=None, actor=None, limit=100, offset=0):
        rows = list(reversed(self.rows))
        if action:
            rows = [r for r in rows if r.action == action]
        if actor:
            rows = [r for r in rows if actor.lower() in r.actor.lower()]
        return rows[offset : offset + limit], len(rows)


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_audit_failure_count()
    yield
    reset_audit_failure_count()


@pytest.fixture
def pg(monkeypatch) -> InMemoryAuditStore:
    """把 `_write_postgres` 接到内存仓储上（默认写入成功）。"""
    store = InMemoryAuditStore()
    _patch_pg(monkeypatch, store)
    return store


def _patch_pg(monkeypatch, store: InMemoryAuditStore) -> None:
    async def _write(action, actor, detail, session) -> None:
        try:
            await store.append(build_entry(action, actor, detail))
        except Exception as exc:
            audit_mod._record_failure("postgres", action, exc)

    monkeypatch.setattr(audit_mod, "_write_postgres", _write)


# ── 1. 双写 ───────────────────────────────────────────────────────


async def test_audit_writes_to_both_layers(pg) -> None:
    redis = FakeStreamRedis()

    await audit_log(AuditAction.ORDER_SUBMIT, "trader@test.local", {"symbol": "AAPL"}, redis=redis)

    assert len(redis.entries) == 1
    assert len(pg.rows) == 1
    assert pg.rows[0].action == AuditAction.ORDER_SUBMIT
    assert pg.rows[0].detail == {"symbol": "AAPL"}
    assert audit_failure_count() == 0


async def test_redis_failure_does_not_block_postgres(monkeypatch, pg) -> None:
    """一条路径挂了另一条照写 —— 这是双写存在的意义。"""
    redis = FakeStreamRedis(fail=ConnectionError("redis down"))

    await audit_log(AuditAction.ORDER_SUBMIT, "trader", {"x": 1}, redis=redis)

    assert pg.rows, "Redis 失败不该带走 Postgres 的那份"
    assert audit_failure_count("redis") == 1
    assert audit_failure_count("postgres") == 0


async def test_postgres_failure_does_not_block_redis(monkeypatch) -> None:
    _patch_pg(monkeypatch, InMemoryAuditStore(fail=RuntimeError("pg down")))
    redis = FakeStreamRedis()

    await audit_log(AuditAction.ORDER_SUBMIT, "trader", {"x": 1}, redis=redis)

    assert len(redis.entries) == 1
    assert audit_failure_count("postgres") == 1


# ── 2. 失败记 ERROR 且不阻断主流程 ────────────────────────────────


async def test_write_failure_logs_error_not_debug(monkeypatch, caplog) -> None:
    _patch_pg(monkeypatch, InMemoryAuditStore(fail=RuntimeError("pg down")))
    redis = FakeStreamRedis(fail=ConnectionError("redis down"))

    with caplog.at_level(logging.DEBUG, logger="app.core.audit"):
        await audit_log(AuditAction.ORDER_SUBMIT, "trader", {"x": 1}, redis=redis)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2, "两条路径各记一条 ERROR"
    assert all("审计写入失败" in r.getMessage() for r in errors)
    # 关键：不能只是 debug —— 静默丢失等于没做审计
    debug_only = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert not any("审计写入失败" in r.getMessage() for r in debug_only)


async def test_write_failure_never_raises(monkeypatch) -> None:
    """审计挂了不该让下单失败：调用方永远拿不到异常。"""
    _patch_pg(monkeypatch, InMemoryAuditStore(fail=RuntimeError("pg down")))
    redis = FakeStreamRedis(fail=ConnectionError("redis down"))

    result = await audit_log(AuditAction.ORDER_SUBMIT, "trader", {"x": 1}, redis=redis)

    assert result is None


async def test_failure_counter_accumulates(monkeypatch) -> None:
    _patch_pg(monkeypatch, InMemoryAuditStore(fail=RuntimeError("pg down")))
    redis = FakeStreamRedis(fail=ConnectionError("redis down"))

    for _ in range(3):
        await audit_log(AuditAction.ORDER_CANCEL, "trader", redis=redis)

    assert audit_failure_count("redis") == 3
    assert audit_failure_count("postgres") == 3
    assert audit_failure_count() == 6


async def test_missing_redis_client_counts_as_failure(monkeypatch, pg) -> None:
    """拿不到 Redis 客户端也是「没留痕」，不能当成什么都没发生。"""
    async def _no_redis(_redis):
        return None, False

    monkeypatch.setattr(audit_mod, "_resolve_redis", _no_redis)

    await audit_log(AuditAction.ORDER_SUBMIT, "trader")

    assert audit_failure_count("redis") == 1


async def test_borrowed_redis_client_is_closed(monkeypatch, pg) -> None:
    client = FakeStreamRedis()

    async def _borrow(_redis):
        return client, True

    monkeypatch.setattr(audit_mod, "_resolve_redis", _borrow)

    await audit_log(AuditAction.ORDER_SUBMIT, "trader")

    assert client.closed is True


# ── 3. Postgres 熔断 ──────────────────────────────────────────────


async def test_postgres_breaker_opens_after_repeated_failures(monkeypatch) -> None:
    """连续失败后暂停写库，但计数继续涨 —— 监控仍看得到「审计没落库」。"""
    attempts: list[str] = []

    class _Boom:
        async def append(self, entry) -> None:
            attempts.append(entry.action)
            raise RuntimeError("pg down")

    monkeypatch.setattr(
        "app.data.storage.audit_log.PostgresAuditLogStore", lambda _s: _Boom()
    )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _Session())
    redis = FakeStreamRedis()

    for _ in range(6):
        await audit_log(AuditAction.ORDER_SUBMIT, "trader", redis=redis)

    assert len(attempts) == audit_mod._PG_BREAKER_THRESHOLD, "熔断后不再尝试连接"
    assert audit_failure_count("postgres") == audit_mod._PG_BREAKER_THRESHOLD
    assert audit_failure_count("postgres_skipped") == 3
    assert audit_failure_count() >= 6, "被跳过的写入仍计入「未留痕」总数"
    assert len(redis.entries) == 6, "熔断只影响 Postgres，Redis 照写"


async def test_explicit_session_bypasses_breaker(monkeypatch) -> None:
    """调用方自带 session 时没有连接成本，熔断不该拦它。"""
    appended: list[str] = []

    class _Store:
        def __init__(self, _session) -> None:
            pass

        async def append(self, entry) -> None:
            appended.append(entry.action)

    monkeypatch.setattr("app.data.storage.audit_log.PostgresAuditLogStore", _Store)
    audit_mod._pg_retry_after = audit_mod.time.monotonic() + 999
    try:
        await audit_log(
            AuditAction.ORDER_SUBMIT, "trader", redis=FakeStreamRedis(), session=object()
        )
    finally:
        audit_mod._pg_retry_after = 0.0

    assert appended == [AuditAction.ORDER_SUBMIT]


# ── 4. 审计记录不可变：无 UPDATE / DELETE 端点 ────────────────────


def test_audit_router_exposes_only_get() -> None:
    """反射断言路由表：审计端点只能有 GET。"""
    from app.api.v1.endpoints import audit as audit_ep

    methods: set[str] = set()
    for route in audit_ep.router.routes:
        methods |= set(getattr(route, "methods", set()))

    assert methods <= {"GET", "HEAD", "OPTIONS"}, f"审计端点出现了写方法：{methods}"
    assert "DELETE" not in methods
    assert "PUT" not in methods
    assert "PATCH" not in methods
    assert "POST" not in methods


def test_full_api_has_no_audit_write_route() -> None:
    """整张路由表里也不能有 /audit 的写端点（防止别处又挂一个上去）。"""
    from app.api.v1.router import api_router

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    offenders = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if "/audit" in getattr(route, "path", "")
        and set(getattr(route, "methods", set())) - {"GET", "HEAD", "OPTIONS"}
    ]

    assert offenders == [], f"审计记录必须不可变，发现写端点：{offenders}"


def test_audit_store_protocol_has_no_mutation_methods() -> None:
    """仓储接口层面也只有 append / list。"""
    from app.data.storage.audit_log import AuditLogStore, PostgresAuditLogStore

    public = {n for n in dir(AuditLogStore) if not n.startswith("_")}
    assert public == {"append", "list"}

    store_public = {n for n in dir(PostgresAuditLogStore) if not n.startswith("_")}
    assert not (store_public & {"update", "delete", "purge", "truncate"})


# ── 5. 序列化 ─────────────────────────────────────────────────────


def test_parse_entry_survives_malformed_detail() -> None:
    record = parse_entry("1-0", {"ts": "t", "action": "a", "actor": "x", "detail": "{oops"})

    assert record["detail"] == {"raw": "{oops"}


def test_build_entry_defaults_actor_to_system() -> None:
    entry = build_entry(AuditAction.ORDER_SUBMIT, "", None)

    assert entry.actor == "system"
    assert entry.detail == {}
    assert entry.to_dict()["action"] == AuditAction.ORDER_SUBMIT


def test_row_to_entry_parses_string_json_detail() -> None:
    from app.data.storage.audit_log import _row_to_entry

    class _Row:
        id = 7
        ts = "2026-08-14T00:00:00+00:00"
        action = "order.submit"
        actor = "trader"
        detail = '{"symbol": "AAPL"}'

    entry = _row_to_entry(_Row())

    assert entry.id == "7"
    assert entry.detail == {"symbol": "AAPL"}


def test_row_to_entry_survives_corrupt_detail(caplog) -> None:
    from app.data.storage.audit_log import _row_to_entry

    class _Row:
        id = 8
        ts = "2026-08-14T00:00:00+00:00"
        action = "order.submit"
        actor = ""
        detail = "{broken"

    with caplog.at_level(logging.ERROR, logger="app.data.storage.audit_log"):
        entry = _row_to_entry(_Row())

    assert entry.detail == {"raw": "{broken"}
    assert entry.actor == "system"
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


# ── 6. 查询端点：Redis 优先，失败回落 Postgres ────────────────────


def _audit_app(redis, pg_store):
    """按 router.py 的注册方式挂载审计端点，两层都替换成假件。"""
    from app.api.v1.endpoints import audit as audit_ep
    from app.core.database import get_db
    from app.core.redis import get_redis

    app = FastAPI()
    app.include_router(audit_ep.router, prefix="/api/v1/audit")
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_db] = lambda: object()
    return app, audit_ep


async def _get_audit(app, **params):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/v1/audit", params=params)


async def test_query_prefers_redis(monkeypatch, pg) -> None:
    redis = FakeStreamRedis()
    await audit_log(AuditAction.ORDER_SUBMIT, "trader", {"symbol": "AAPL"}, redis=redis)
    app, _ = _audit_app(redis, pg)

    body = (await _get_audit(app)).json()

    assert body["source"] == "redis"
    assert body["total"] == 1
    assert body["items"][0]["action"] == AuditAction.ORDER_SUBMIT


async def test_query_falls_back_to_postgres_when_redis_down(monkeypatch, pg) -> None:
    """Redis 一挂就返回空集，在用户眼里和「审计被删了」没区别 —— 必须回落。"""
    await pg.append(build_entry(AuditAction.ORDER_CANCEL, "trader", {"id": "1"}))
    redis = FakeStreamRedis(fail=ConnectionError("down"))

    async def _boom(*args, **kwargs):
        raise ConnectionError("down")

    redis.xrevrange = _boom  # type: ignore[method-assign]
    app, audit_ep = _audit_app(redis, pg)
    monkeypatch.setattr(audit_ep, "PostgresAuditLogStore", lambda _s: pg)

    body = (await _get_audit(app)).json()

    assert body["source"] == "postgres"
    assert body["total"] == 1
    assert body["items"][0]["action"] == AuditAction.ORDER_CANCEL


async def test_query_reports_none_when_both_layers_down(monkeypatch, pg) -> None:
    redis = FakeStreamRedis()

    async def _boom(*args, **kwargs):
        raise ConnectionError("down")

    redis.xrevrange = _boom  # type: ignore[method-assign]
    app, audit_ep = _audit_app(redis, pg)

    class _DeadStore:
        async def list(self, **kwargs):
            raise RuntimeError("pg down")

    monkeypatch.setattr(audit_ep, "PostgresAuditLogStore", lambda _s: _DeadStore())

    body = (await _get_audit(app)).json()

    assert body["source"] == "none"
    assert body["items"] == []


async def test_query_filters_by_action(monkeypatch, pg) -> None:
    redis = FakeStreamRedis()
    await audit_log(AuditAction.ORDER_SUBMIT, "alice", redis=redis)
    await audit_log(AuditAction.ORDER_CANCEL, "bob", redis=redis)
    app, _ = _audit_app(redis, pg)

    body = (await _get_audit(app, action=AuditAction.ORDER_CANCEL)).json()

    assert body["total"] == 1
    assert body["items"][0]["actor"] == "bob"


def test_new_audit_actions_exist() -> None:
    assert AuditAction.USER_CREATE == "user.create"
    assert AuditAction.USER_UPDATE == "user.update"
    assert AuditAction.USER_DELETE == "user.delete"
    assert AuditAction.RECONCILE_RUN == "reconcile.run"
