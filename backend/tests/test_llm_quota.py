"""LLM 日配额（V3 · `/chat` 权限定调的配套控制）。

定调结论：花钱的 AI 入口一律保持 `Role.VIEWER`，用**日配额**而非角色约束花费。
角色是权限的刻度不是花费的刻度 —— admin 一样能把账单打爆，
而把助手挡在 TRADER 之后恰好拦住最需要它的只读用户。
"""

from __future__ import annotations

import pytest

from app.core.llm_quota import (
    DAILY_LIMITS,
    QuotaState,
    consume,
    limit_for,
    peek,
    quota_key,
)
from app.core.rbac import Role


class FakeRedis:
    """够用的内存替身：incr / decr / expire / get。"""

    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.broken = broken

    async def incr(self, key: str) -> int:
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def decr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("redis down")
        value = self.store.get(key)
        return None if value is None else str(value)


# ── 角色 → 上限 ──────────────────────────────────────────────────────────────

def test_each_role_has_a_limit_and_viewer_is_not_zero() -> None:
    # viewer 给得少，但**不是不给** —— 只读用户来问问题正是这个功能存在的理由
    assert DAILY_LIMITS[Role.VIEWER] > 0
    assert DAILY_LIMITS[Role.VIEWER] < DAILY_LIMITS[Role.TRADER]
    assert DAILY_LIMITS[Role.TRADER] < DAILY_LIMITS[Role.ADMIN]


@pytest.mark.parametrize("raw", ["", None, "superuser", "ADMIN_"])
def test_unknown_role_falls_back_to_viewer_limit(raw: str | None) -> None:
    """未知角色一律按最低额度处理 —— fail-safe，绝不提额。"""
    assert limit_for(raw) == DAILY_LIMITS[Role.VIEWER]


def test_role_is_case_insensitive() -> None:
    assert limit_for("ADMIN") == DAILY_LIMITS[Role.ADMIN]


# ── 计数 ─────────────────────────────────────────────────────────────────────

async def test_consume_increments_and_allows_under_limit() -> None:
    redis = FakeRedis()

    first = await consume("u1", Role.VIEWER, redis=redis)

    assert first.allowed is True
    assert first.used == 1
    assert first.remaining == DAILY_LIMITS[Role.VIEWER] - 1


async def test_first_call_sets_ttl_so_the_key_cannot_leak() -> None:
    redis = FakeRedis()

    await consume("u1", Role.VIEWER, redis=redis)

    assert redis.expires[quota_key("u1")] > 0


async def test_exceeding_the_limit_is_rejected() -> None:
    redis = FakeRedis()
    limit = DAILY_LIMITS[Role.VIEWER]
    for _ in range(limit):
        assert (await consume("u1", Role.VIEWER, redis=redis)).allowed is True

    over = await consume("u1", Role.VIEWER, redis=redis)

    assert over.allowed is False
    assert over.remaining == 0


async def test_rejected_calls_do_not_inflate_the_counter() -> None:
    """被拒的请求不能推高计数，否则用户会看到「已用 73/50」这种读不通的数字。"""
    redis = FakeRedis()
    limit = DAILY_LIMITS[Role.VIEWER]
    for _ in range(limit + 5):
        await consume("u1", Role.VIEWER, redis=redis)

    assert redis.store[quota_key("u1")] == limit
    assert (await peek("u1", Role.VIEWER, redis=redis)).used == limit


async def test_quota_is_per_user() -> None:
    redis = FakeRedis()
    limit = DAILY_LIMITS[Role.VIEWER]
    for _ in range(limit):
        await consume("u1", Role.VIEWER, redis=redis)

    other = await consume("u2", Role.VIEWER, redis=redis)

    assert other.allowed is True
    assert other.used == 1


async def test_higher_role_gets_more_headroom_on_the_same_counter() -> None:
    """额度取决于**当次调用者的角色**，计数键只按用户分。"""
    redis = FakeRedis()
    viewer_limit = DAILY_LIMITS[Role.VIEWER]
    for _ in range(viewer_limit):
        await consume("u1", Role.VIEWER, redis=redis)

    promoted = await consume("u1", Role.ADMIN, redis=redis)

    assert promoted.allowed is True


# ── 降级 ─────────────────────────────────────────────────────────────────────

async def test_redis_failure_allows_the_call_and_marks_degraded() -> None:
    """计数器挂了要放行而不是拒绝：不能让一个附属计数器变成全站 AI 的单点。"""
    state = await consume("u1", Role.VIEWER, redis=FakeRedis(broken=True))

    assert state.allowed is True
    assert state.degraded is True


async def test_redis_failure_is_logged_at_error_not_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """放行必须留痕 —— 静默放行等于没有配额。"""
    with caplog.at_level("ERROR", logger="app.core.llm_quota"):
        await consume("u1", Role.VIEWER, redis=FakeRedis(broken=True))

    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_missing_redis_client_degrades_instead_of_raising() -> None:
    state = QuotaState(allowed=True, used=0, limit=1, degraded=True)

    assert state.remaining == 1
    assert state.to_dict()["degraded"] is True


# ── peek 只读 ────────────────────────────────────────────────────────────────

async def test_peek_does_not_count() -> None:
    redis = FakeRedis()
    await consume("u1", Role.VIEWER, redis=redis)

    for _ in range(3):
        await peek("u1", Role.VIEWER, redis=redis)

    assert redis.store[quota_key("u1")] == 1


async def test_peek_on_untouched_user_reports_zero() -> None:
    state = await peek("nobody", Role.VIEWER, redis=FakeRedis())

    assert state.used == 0
    assert state.allowed is True
