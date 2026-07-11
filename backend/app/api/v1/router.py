from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth, alerts, bars, broker_config, data_config, strategies, backtests,
    orders, positions, quant, risk, stream, portfolio_opt,
)
from app.api.v1.endpoints import live_strategy
from app.api.v1.endpoints import (
    factor_processors, backtest_report, protections, notify,
)
from app.api.v1.endpoints import (
    fundamentals, screener, factor_library, backtest_validation,
)
from app.api.v1.endpoints import (
    factor_mining, topk_portfolio, backtest_robustness, order_algos,
    pairlist, news, calendar, options, futu_config,
)
from app.api.v1.endpoints import sequence_models, audit
from app.core.config import settings

api_router = APIRouter()


@api_router.get("/health", tags=["System"])
async def api_health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0", "environment": settings.environment}


api_router.include_router(auth.router,          prefix="/auth",            tags=["Auth"])
api_router.include_router(bars.router,          prefix="/bars",            tags=["Market Data"])
api_router.include_router(strategies.router,    prefix="/strategies",      tags=["Strategies"])
api_router.include_router(backtests.router,     prefix="/backtests",       tags=["Backtests"])
api_router.include_router(orders.router,        prefix="/orders",          tags=["Orders"])
api_router.include_router(positions.router,     prefix="/positions",       tags=["Positions"])
api_router.include_router(risk.router,          prefix="/risk",            tags=["Risk"])
api_router.include_router(broker_config.router, prefix="/broker-config",   tags=["Broker Config"])
api_router.include_router(data_config.router,  prefix="/data-config",     tags=["Data Config"])
api_router.include_router(quant.router,         prefix="/quant",           tags=["Quant Algorithms"])
api_router.include_router(stream.router,        prefix="/stream",          tags=["Real-time Stream"])
api_router.include_router(portfolio_opt.router, prefix="/portfolio",       tags=["Portfolio Optimizer"])
api_router.include_router(alerts.router,        prefix="/alerts",          tags=["Alerts"])
api_router.include_router(live_strategy.router, prefix="/live-strategies", tags=["Live Strategies"])
# ── v2.0 Wave 1 ──────────────────────────────────────────────────
api_router.include_router(factor_processors.router, prefix="/quant",       tags=["Factor Processors"])
api_router.include_router(backtest_report.router,   prefix="/backtests",   tags=["Backtest Report"])
api_router.include_router(protections.router,       prefix="/protections", tags=["Protections"])
api_router.include_router(notify.router,            prefix="/notify",      tags=["Notifications"])
# ── v2.0 Wave 2 ──────────────────────────────────────────────────
api_router.include_router(fundamentals.router,       prefix="/fundamentals", tags=["Fundamentals"])
api_router.include_router(screener.router,           prefix="/screener",     tags=["Screener"])
api_router.include_router(factor_library.router,     prefix="/quant",        tags=["Factor Library"])
api_router.include_router(backtest_validation.router, prefix="/backtests",   tags=["Backtest Validation"])
# ── v2.0 Wave 3 ──────────────────────────────────────────────────
api_router.include_router(factor_mining.router,       prefix="/quant",        tags=["Factor Mining"])
api_router.include_router(topk_portfolio.router,      prefix="/portfolio",    tags=["Portfolio Optimizer"])
api_router.include_router(backtest_robustness.router, prefix="/backtests",    tags=["Backtest Robustness"])
api_router.include_router(order_algos.router,         prefix="/orders",       tags=["Order Algos"])
api_router.include_router(pairlist.router,            prefix="/screener",     tags=["Pairlist"])
api_router.include_router(news.router,                prefix="/news",         tags=["News"])
api_router.include_router(calendar.router,            prefix="/calendar",     tags=["Calendar"])
api_router.include_router(options.router,             prefix="/options",      tags=["Options"])
api_router.include_router(futu_config.router,         prefix="/broker-config", tags=["Broker Config"])
# ── v2.0 补充：序列模型 + 审计 ────────────────────────────────────
api_router.include_router(sequence_models.router,     prefix="/quant",        tags=["Sequence Models"])
api_router.include_router(audit.router,               prefix="/audit",        tags=["Audit"])
