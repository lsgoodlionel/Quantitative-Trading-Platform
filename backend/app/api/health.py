"""存活 / 就绪探针。

`/health` 与 `/health/ready` 是两件不同的事，不要合并：
- liveness 回答「这个进程还需要被重启吗」——只有进程自己坏了才该答否。
- readiness 回答「现在能把流量打给它吗」——依赖挂了就该摘流量，但**不该**重启。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import health as health_probes
from app.core.config import settings
from app.core.version import APP_VERSION

router = APIRouter(tags=["System"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """存活探针：进程活着就 200，**不触碰任何依赖**。

    这里一旦查了 Postgres/Redis，一次依赖抖动就会被编排系统翻译成「杀掉容器」，
    而重启并不能修好一个外部依赖，只会让整个集群在滚动重启里反复横跳。
    """
    return {
        "status": "ok",
        "version": APP_VERSION,
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    """就绪探针：逐项探测依赖，Postgres 挂 → 503(down)，仅 Redis 挂 → 200(degraded)。"""
    report = await health_probes.check_readiness()
    return JSONResponse(
        status_code=report.http_status,
        content=report.to_payload(version=APP_VERSION, environment=settings.environment),
    )
