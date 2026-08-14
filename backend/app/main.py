from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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

    # 用户表播种（V3 J3）：空表时写入 admin / trader / viewer 三个内置账户。
    # 失败只警告不阻断启动 —— 登录路径本身也会在数据库不可达时回落内置账户。
    try:
        from app.core.database import AsyncSessionLocal
        from app.data.storage.users import PostgresUserStore, seed_builtin_users

        async with AsyncSessionLocal() as session:
            seed = await seed_builtin_users(PostgresUserStore(session))
        if seed.warning:
            logger.warning(seed.warning)
    except Exception as e:
        logger.warning("User seeding skipped: %s", e)

    # 初始化 OMS：自动检测 Redis 中的 Alpaca 配置
    # - 已配置 Alpaca → AlpacaGateway (Paper/Live) 处理美股
    # - 未配置 Alpaca → PaperGateway（本地纸面交易）
    import redis.asyncio as aioredis

    from app.core.redis import get_redis_pool
    from app.oms.manager import init_hybrid_order_manager
    try:
        redis_client = aioredis.Redis(connection_pool=get_redis_pool())
        await redis_client.ping()
    except Exception:
        redis_client = None
        logger.warning("Redis unavailable, OMS events will not be published")
    # 载入多源数据通道配置（顺序/禁用/强制），缺失时用默认
    try:
        from app.data.source_registry import DataSourceRegistry
        await DataSourceRegistry.instance().load_config(redis_client)
        logger.info("Data source config loaded")
    except Exception as e:
        logger.warning("Data source config load skipped: %s", e)

    manager = await init_hybrid_order_manager(redis_client=redis_client)
    # 富途 HK 网关：配置存在时接入 OMS（否则 HK 保持 PaperGateway）
    try:
        from app.oms.futu_wiring import register_futu_gateway
        await register_futu_gateway(manager, redis_client=redis_client)
    except Exception as e:
        logger.warning("Futu gateway wiring skipped: %s", e)

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

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """把「用户配置错误」类异常翻译成 4xx，而不是让它们变成 500。

    500 的含义是「服务端出了意料之外的问题」。用户策略目录里放了一个与
    preset 重名的文件是**用户能自己修好的配置问题**，报 500 会让人以为
    是平台坏了，而真正的原因（哪两个名字撞了）还埋在服务端日志里。
    """
    from fastapi.responses import JSONResponse

    from app.strategy.resolver import StrategyNameConflictError

    @app.exception_handler(StrategyNameConflictError)
    async def _on_strategy_name_conflict(_request: Request, exc: StrategyNameConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})


app = create_app()
