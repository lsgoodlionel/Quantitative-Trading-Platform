from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.core.config import settings
from app.core.database import engine
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    logger.info("QuantBot starting", environment=settings.environment)

    # 预热数据库连接池
    async with engine.begin():
        pass
    logger.info("Database connection pool ready")

    # 初始化 OMS：自动检测 Redis 中的 Alpaca 配置
    # - 已配置 Alpaca → AlpacaGateway (Paper/Live) 处理美股
    # - 未配置 Alpaca → PaperGateway（本地纸面交易）
    from app.oms.manager import init_hybrid_order_manager
    from app.core.redis import get_redis_pool
    import redis.asyncio as aioredis
    try:
        redis_client = aioredis.Redis(connection_pool=get_redis_pool())
        await redis_client.ping()
    except Exception:
        redis_client = None
        logger.warning("Redis unavailable, OMS events will not be published")
    await init_hybrid_order_manager(redis_client=redis_client)

    yield

    # 关闭时清理
    from app.oms.manager import get_order_manager
    try:
        oms = get_order_manager()
        await oms.stop()
    except RuntimeError:
        pass
    await engine.dispose()
    logger.info("QuantBot shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="QuantBot API",
        version="0.1.0",
        description="Multi-market quantitative trading platform (US/HK/A)",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # CORS origins — dev 默认允许 localhost；生产通过 ALLOWED_ORIGINS 环境变量控制
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # Prometheus 指标端点
    if settings.prometheus_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

    # 路由注册
    from app.api.v1.router import api_router
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0", "environment": settings.environment}

    return app


app = create_app()
