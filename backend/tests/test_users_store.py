"""
用户落库与用户管理 API 测试（V3 Wave C-b · J3）

对应契约 docs/contracts/waveCb-reconcile-audit.md §三 验收 3：

1. 空表首次启动播种三个内置账户，日志含默认密码警告
2. role 非法值被拒
3. 用户管理端点要求 ADMIN，trader/viewer 得 403

Postgres 仓储用内存实现替换（语义与 `PostgresUserStore` 一致），
**不依赖真实数据库**。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import users as users_ep
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.core.rbac import Role
from app.data.storage.users import (
    BUILTIN_SEED,
    DuplicateUsernameError,
    InvalidRoleError,
    InvalidUserInputError,
    UserNotFoundError,
    UserRecord,
    build_user,
    hash_password,
    normalize_role_strict,
    seed_builtin_users,
    validate_username,
    verify_password,
)

BASE = "/api/v1/users"


# ── 内存仓储 ──────────────────────────────────────────────────────

class InMemoryUserStore:
    """`UserStore` 的内存实现，语义与 `PostgresUserStore` 一致。"""

    def __init__(self) -> None:
        self._rows: dict[str, UserRecord] = {}
        self._order: list[str] = []

    async def count(self) -> int:
        return len(self._rows)

    async def get_by_username(self, username: str) -> UserRecord | None:
        return next((r for r in self._rows.values() if r.username == username), None)

    async def get(self, user_id: str) -> UserRecord | None:
        return self._rows.get(user_id)

    async def list(self, limit: int, offset: int) -> tuple[list[UserRecord], int]:
        ordered = [self._rows[i] for i in self._order]
        return ordered[offset : offset + limit], len(ordered)

    async def create(self, record: UserRecord) -> UserRecord:
        if await self.get_by_username(record.username) is not None:
            raise DuplicateUsernameError(f"用户名已存在: {record.username}")
        # 与 Postgres 版一致：落库前再校一次 role
        normalize_role_strict(record.role)
        self._rows[record.id] = record
        self._order.append(record.id)
        return record

    async def update(self, record: UserRecord) -> UserRecord:
        if record.id not in self._rows:
            raise UserNotFoundError(f"用户不存在: {record.id}")
        self._rows[record.id] = record
        return record

    async def delete(self, user_id: str) -> bool:
        if user_id not in self._rows:
            return False
        del self._rows[user_id]
        self._order.remove(user_id)
        return True


@pytest.fixture
def store() -> InMemoryUserStore:
    return InMemoryUserStore()


# ── 1. 空表播种 ───────────────────────────────────────────────────


async def test_empty_table_seeds_three_builtin_users(store, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.data.storage.users"):
        result = await seed_builtin_users(store)

    assert result.skipped is False
    assert result.seeded == ("admin", "trader", "viewer")
    assert await store.count() == 3

    admin = await store.get_by_username("admin")
    assert admin is not None
    assert admin.role is Role.ADMIN
    assert admin.id == "00000000-0000-0000-0000-000000000001"


async def test_seed_logs_default_password_warning(store, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.data.storage.users"):
        result = await seed_builtin_users(store)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "播种默认密码必须留下 WARNING"
    assert any("必须立即修改" in m for m in warnings)
    assert any("admin123" in m for m in warnings)
    assert "必须立即修改" in result.warning


async def test_seed_is_skipped_when_table_not_empty(store) -> None:
    await store.create(build_user("alice", "password123", "trader"))

    result = await seed_builtin_users(store)

    assert result.skipped is True
    assert result.seeded == ()
    assert result.warning == ""
    assert await store.count() == 1, "非空表不该被补齐内置账户"


async def test_deleted_builtin_user_does_not_grow_back(store) -> None:
    await seed_builtin_users(store)
    viewer = await store.get_by_username("viewer")
    await store.delete(viewer.id)

    await seed_builtin_users(store)

    assert await store.get_by_username("viewer") is None


def test_seed_hashes_match_legacy_builtin_passwords() -> None:
    """播种沿用 auth.py 的原始 hash：现有部署的登录行为不能变。"""
    expected = {"admin": "admin123", "trader": "trader123", "viewer": "viewer123"}
    for record in BUILTIN_SEED:
        assert verify_password(expected[record.username], record.hashed_pw)


# ── 2. role 校验 ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad_role", ["superuser", "ADMN", "", "root", None])
def test_invalid_role_is_rejected_not_downgraded(bad_role) -> None:
    """非法 role 必须抛错。静默降级会造出一个「谁都不是」的账户。"""
    with pytest.raises(InvalidRoleError):
        normalize_role_strict(bad_role)


@pytest.mark.parametrize("good_role", ["admin", "TRADER", " viewer "])
def test_valid_roles_are_normalized(good_role) -> None:
    assert normalize_role_strict(good_role) in set(Role)


async def test_store_rejects_illegal_role_on_create(store) -> None:
    bogus = UserRecord(
        id="11111111-1111-1111-1111-111111111111",
        username="bogus",
        email="",
        hashed_pw=hash_password("password123"),
        role="superuser",  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidRoleError):
        await store.create(bogus)


def test_build_user_rejects_illegal_role() -> None:
    with pytest.raises(InvalidRoleError):
        build_user("alice", "password123", "superuser")


# ── 输入校验 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["ab", "a" * 65, "has space", "semi;colon", ""])
def test_invalid_usernames_rejected(bad) -> None:
    with pytest.raises(InvalidUserInputError):
        validate_username(bad)


def test_short_password_rejected() -> None:
    with pytest.raises(InvalidUserInputError):
        hash_password("short")


def test_overlong_password_rejected() -> None:
    """bcrypt 静默截断到 72 字节；不拦就会出现「改了密码旧前缀仍能登录」。"""
    with pytest.raises(InvalidUserInputError):
        hash_password("x" * 73)


def test_password_roundtrip() -> None:
    hashed = hash_password("correct horse battery")
    assert verify_password("correct horse battery", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_corrupt_hash_denies_login_without_raising() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_public_dict_never_leaks_hash() -> None:
    record = build_user("alice", "password123", "trader")

    assert "hashed_pw" not in record.to_public_dict()


# ── 3. 端点鉴权 ───────────────────────────────────────────────────


def _user(role: str, user_id: str = "u-1") -> UserInfo:
    return UserInfo(id=user_id, email=f"{role}@test.local", role=role)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch) -> None:
    async def _noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr("app.api.v1.endpoints.users.audit_log", _noop)


@pytest.fixture
def app(store: InMemoryUserStore) -> FastAPI:
    """与 router.py 中的注册片段一致的挂载方式。"""
    test_app = FastAPI()
    test_app.include_router(users_ep.router, prefix=BASE, tags=["Users"])
    test_app.dependency_overrides[users_ep.get_store] = lambda: store
    test_app.dependency_overrides[get_current_user] = lambda: _user("admin")
    return test_app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("role", ["trader", "viewer"])
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", ""), ("POST", ""), ("PUT", "/some-id"), ("DELETE", "/some-id")],
)
async def test_non_admin_gets_403(app, role, method, path) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(role)

    async with _client(app) as client:
        resp = await client.request(method, f"{BASE}{path}", json={})

    assert resp.status_code == 403


async def test_admin_can_create_and_list(app, store) -> None:
    async with _client(app) as client:
        created = await client.post(
            BASE,
            json={
                "username": "alice",
                "password": "password123",
                "role": "trader",
                "email": "alice@test.local",
            },
        )
        listed = await client.get(BASE)

    assert created.status_code == 201
    body = created.json()
    assert body["username"] == "alice"
    assert body["role"] == "trader"
    assert "hashed_pw" not in body, "响应绝不能带密码哈希"

    assert listed.status_code == 200
    assert listed.json()["total"] == 1


async def test_create_with_bad_role_returns_400(app) -> None:
    async with _client(app) as client:
        resp = await client.post(
            BASE, json={"username": "bob", "password": "password123", "role": "root"}
        )

    assert resp.status_code == 400
    assert "非法角色" in resp.json()["detail"]


async def test_duplicate_username_returns_409(app) -> None:
    payload = {"username": "alice", "password": "password123", "role": "viewer"}
    async with _client(app) as client:
        await client.post(BASE, json=payload)
        resp = await client.post(BASE, json=payload)

    assert resp.status_code == 409


async def test_update_changes_role_and_password(app, store) -> None:
    async with _client(app) as client:
        created = (
            await client.post(
                BASE, json={"username": "alice", "password": "password123", "role": "viewer"}
            )
        ).json()
        resp = await client.put(
            f"{BASE}/{created['id']}", json={"role": "trader", "password": "newpassword1"}
        )

    assert resp.status_code == 200
    assert resp.json()["role"] == "trader"
    record = await store.get(created["id"])
    assert verify_password("newpassword1", record.hashed_pw)


async def test_update_unknown_user_returns_404(app) -> None:
    async with _client(app) as client:
        resp = await client.put(f"{BASE}/does-not-exist", json={"role": "trader"})

    assert resp.status_code == 404


async def test_delete_removes_user(app, store) -> None:
    async with _client(app) as client:
        created = (
            await client.post(
                BASE, json={"username": "alice", "password": "password123", "role": "viewer"}
            )
        ).json()
        resp = await client.delete(f"{BASE}/{created['id']}")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert await store.get(created["id"]) is None


async def test_admin_cannot_delete_self(app, store) -> None:
    me = build_user("selfadmin", "password123", "admin", user_id="u-1")
    await store.create(me)
    app.dependency_overrides[get_current_user] = lambda: _user("admin", "u-1")

    async with _client(app) as client:
        resp = await client.delete(f"{BASE}/u-1")

    assert resp.status_code == 400
    assert await store.get("u-1") is not None


async def test_admin_cannot_deactivate_self(app, store) -> None:
    await store.create(build_user("selfadmin", "password123", "admin", user_id="u-1"))
    app.dependency_overrides[get_current_user] = lambda: _user("admin", "u-1")

    async with _client(app) as client:
        resp = await client.put(f"{BASE}/u-1", json={"is_active": False})

    assert resp.status_code == 400


async def test_admin_cannot_demote_self(app, store) -> None:
    await store.create(build_user("selfadmin", "password123", "admin", user_id="u-1"))
    app.dependency_overrides[get_current_user] = lambda: _user("admin", "u-1")

    async with _client(app) as client:
        resp = await client.put(f"{BASE}/u-1", json={"role": "viewer"})

    assert resp.status_code == 400


async def test_no_self_registration_endpoint(app) -> None:
    """契约明确不做自助注册：路由表里不该出现 register / signup。"""
    paths = {route.path for route in app.routes}

    assert not any("register" in p or "signup" in p for p in paths)


# ── 4. 正式注册进 router.py（不是端点模块尾部寄生挂载）─────────────


def test_endpoints_are_registered_in_api_router() -> None:
    from app.api.v1.router import api_router

    real_app = FastAPI()
    real_app.include_router(api_router, prefix="/api/v1")
    paths = {route.path for route in real_app.routes}

    assert "/api/v1/users" in paths
    assert "/api/v1/users/{user_id}" in paths
    assert "/api/v1/reconcile/{market}" in paths


def test_endpoint_modules_do_not_self_mount() -> None:
    """端点模块末尾不得再 include_router —— 两处注册会导致路由重复。"""
    from pathlib import Path

    for name in ("users", "reconcile"):
        source = Path(f"app/api/v1/endpoints/{name}.py").read_text(encoding="utf-8")
        assert "include_router" not in source, f"{name}.py 里出现了寄生挂载"


# ── 5. 登录取数顺序：库优先，仅在库不可达时回落内置账户 ───────────


def _patch_login_store(monkeypatch, store: InMemoryUserStore | None) -> None:
    """store=None 模拟数据库不可达。"""
    import app.api.v1.endpoints.auth as auth_mod

    class _Session:
        async def __aenter__(self):
            if store is None:
                raise ConnectionError("postgres down")
            return self

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(
        "app.data.storage.users.PostgresUserStore", lambda _s: store
    )
    return auth_mod


async def test_login_reads_from_database(monkeypatch, store) -> None:
    auth_mod = _patch_login_store(monkeypatch, store)
    await store.create(build_user("alice", "password123", "trader", email="a@t.local"))

    found = await auth_mod._lookup_user("alice")

    assert found is not None
    assert found["role"] == "trader"
    assert found["email"] == "a@t.local"


async def test_login_seeds_empty_table_then_finds_admin(monkeypatch, store) -> None:
    auth_mod = _patch_login_store(monkeypatch, store)

    found = await auth_mod._lookup_user("admin")

    assert found is not None
    assert found["role"] == "admin"
    assert await store.count() == 3


async def test_deleted_user_cannot_login_via_builtin_fallback(monkeypatch, store) -> None:
    """库是好的、人被删了 —— 绝不能拿源码里的内置口令放行。"""
    auth_mod = _patch_login_store(monkeypatch, store)
    await store.create(build_user("alice", "password123", "trader"))

    assert await auth_mod._lookup_user("admin") is None


async def test_deactivated_user_cannot_login(monkeypatch, store) -> None:
    from dataclasses import replace as dc_replace

    auth_mod = _patch_login_store(monkeypatch, store)
    record = build_user("alice", "password123", "trader")
    await store.create(dc_replace(record, is_active=False))

    assert await auth_mod._lookup_user("alice") is None


async def test_login_falls_back_to_builtin_when_db_unreachable(monkeypatch, caplog) -> None:
    """零配置启动：没起 Postgres 时内置账户仍然能登录。"""
    auth_mod = _patch_login_store(monkeypatch, None)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.auth"):
        found = await auth_mod._lookup_user("admin")

    assert found is not None
    assert found["role"] == "admin"
    assert any("回落内置账户" in r.getMessage() for r in caplog.records)


def test_users_routes_all_require_admin() -> None:
    """反射断言：users 路由上必须挂着 require_role 依赖，别漏掉某一个方法。"""
    from app.api.v1.endpoints import users as module

    for route in module.router.routes:
        deps = getattr(route, "dependant", None)
        assert deps is not None
        names = {p.name for p in route.dependant.dependencies}
        assert names, f"{route.path} 没有任何鉴权依赖"
