"""依赖探测：给就绪探针（readiness）用的真实健康检查。

设计上的三条硬约束，改动前请先读：

1. **liveness 与 readiness 必须分开。** 本模块只服务 readiness。把依赖检查塞进
   `/health`（liveness）会让「Redis 抖了一下」变成「容器被 kill 重启」—— 重启修不好
   Redis，只会让服务在滚动重启里反复横跳。
2. **每个探测都有超时。** 一个卡住的探测会让编排系统一直等到它自己的超时，
   那段时间里实例状态是未知的，比明确的「不就绪」更糟。
3. **`down` 与 `degraded` 分开。** Postgres 挂 = down（核心数据没了，摘流量）；
   Redis 挂 = degraded（缓存/事件降级，主要功能仍在，继续收流量）。
   全判成 down 会让一次缓存抖动摘掉整个集群。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# 2s：比常见编排系统的探针超时（3~5s）短，保证是我们自己给出结论，
# 而不是让 kubelet/负载均衡器等到它自己超时后猜。
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0

# 健康检查是**未鉴权**端点，错误信息会直接吐给调用方。
# 驱动层异常里可能夹带 `postgresql://user:password@host/db` 这样的连接串，
# 必须在出口处抹掉凭证，否则这就成了一个免鉴权的密码泄露口。
_CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>[a-zA-Z0-9+.\-]+://)[^\s/@]*@")
_MAX_ERROR_CHARS = 200


def sanitize_error(exc: BaseException) -> str:
    """把异常压成一行安全、简短的说明：抹掉连接串凭证并截断。"""
    raw = str(exc).strip() or exc.__class__.__name__
    redacted = _CREDENTIALS_IN_URL.sub(r"\g<scheme>***@", raw)
    collapsed = " ".join(redacted.split())
    if len(collapsed) > _MAX_ERROR_CHARS:
        return collapsed[:_MAX_ERROR_CHARS] + "…"
    return collapsed


@dataclass(frozen=True)
class CheckResult:
    """单个依赖的探测结果。"""

    ok: bool
    latency_ms: float | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class ReadinessReport:
    """聚合结论。`http_status` 是给编排系统看的那一半，别只看 body。"""

    status: str  # "ok" | "degraded" | "down"
    checks: dict[str, CheckResult] = field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return 503 if self.status == "down" else 200

    def to_payload(self, *, version: str, environment: str) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": version,
            "environment": environment,
            "checks": {name: r.to_payload() for name, r in self.checks.items()},
        }


async def run_probe(
    probe: Callable[[], Awaitable[None]],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> CheckResult:
    """执行一次探测，计时、限时，并把任何异常翻成结构化结果。

    这里刻意吞掉所有异常：探测失败**就是**本函数要表达的信息，
    向上抛会让一个依赖的故障把整个就绪端点打成 500，反而看不出是谁挂了。
    """
    start = time.perf_counter()
    try:
        await asyncio.wait_for(probe(), timeout=timeout)
    except TimeoutError:
        return CheckResult(ok=False, error=f"probe timed out after {timeout}s")
    except Exception as exc:  # 见 docstring：探测失败是结果，不是异常
        return CheckResult(ok=False, error=sanitize_error(exc))
    elapsed_ms = (time.perf_counter() - start) * 1000
    return CheckResult(ok=True, latency_ms=round(elapsed_ms, 2))


async def check_postgres(*, timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> CheckResult:
    """`SELECT 1`：既验连接池能拿到连接，也验服务端真的在应答。"""

    async def probe() -> None:
        from sqlalchemy import text

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

    return await run_probe(probe, timeout=timeout)


async def check_redis(*, timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> CheckResult:
    async def probe() -> None:
        import redis.asyncio as aioredis

        from app.core.redis import get_redis_pool

        client = aioredis.Redis(connection_pool=get_redis_pool())
        try:
            await client.ping()
        finally:
            await client.aclose()

    return await run_probe(probe, timeout=timeout)


async def check_readiness(*, timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> ReadinessReport:
    """并行探测所有依赖并给出聚合结论。

    并行而非串行：串行时总耗时是各超时之和，两个依赖同时挂会让探针耗时翻倍，
    很容易越过编排系统的超时，退化成「探测无响应」。
    """
    postgres, redis = await asyncio.gather(
        check_postgres(timeout=timeout),
        check_redis(timeout=timeout),
    )

    if not postgres.ok:
        status = "down"
    elif not redis.ok:
        status = "degraded"
    else:
        status = "ok"

    return ReadinessReport(status=status, checks={"postgres": postgres, "redis": redis})
