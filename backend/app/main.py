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

    yield

    # 关闭时清理
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
