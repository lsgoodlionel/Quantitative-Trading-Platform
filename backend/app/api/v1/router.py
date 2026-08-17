from fastapi import APIRouter

from app.api.v1.endpoints import (
    ai_reports,
    alerts,
    audit,
    auth,
    backtest_batch,
    backtest_full_validation,
    backtest_history,
    backtest_report,
    backtest_robustness,
    backtest_validation,
    backtests,
    bars,
    broker_config,
    calendar,
    copilot,
    data_archive,
    data_config,
    data_sources,
    factor_library,
    factor_mining,
    factor_processors,
    factor_strategy,
    fundamentals,
    futu_config,
    lab,
    lab_auto_loop,
    live_strategy,
    llm,
    news,
    notifications,
    notify,
    options,
    order_algos,
    orders,
    pairlist,
    portfolio_backtest,
    portfolio_opt,
    positions,
    protections,
    quant,
    rebalance,
    reconcile,
    retrain,
    risk,
    screener,
    sequence_models,
    strategies,
    stream,
    topk_portfolio,
    universe,
    users,
)
from app.core.config import settings
from app.core.version import APP_VERSION

api_router = APIRouter()


@api_router.get("/health", tags=["System"])
async def api_health() -> dict[str, str]:
    """与 `/health` 同语义的存活探针（前端探测的是带版本前缀的这一个）。

    版本号取自 `APP_VERSION` 单一常量 —— 此前这里和 main.py 各硬编码了一份。
    """
    return {"status": "ok", "version": APP_VERSION, "environment": settings.environment}


api_router.include_router(auth.router,          prefix="/auth",            tags=["Auth"])
api_router.include_router(bars.router,          prefix="/bars",            tags=["Market Data"])
api_router.include_router(strategies.router,    prefix="/strategies",      tags=["Strategies"])
api_router.include_router(backtests.router,     prefix="/backtests",       tags=["Backtests"])
# order_algos 必须在 orders 之前注册：否则 orders 的 /{order_id} 会吞掉 /orders/algo
api_router.include_router(order_algos.router,    prefix="/orders",          tags=["Order Algos"])
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
api_router.include_router(pairlist.router,            prefix="/screener",     tags=["Pairlist"])
api_router.include_router(news.router,                prefix="/news",         tags=["News"])
api_router.include_router(calendar.router,            prefix="/calendar",     tags=["Calendar"])
api_router.include_router(options.router,             prefix="/options",      tags=["Options"])
api_router.include_router(futu_config.router,         prefix="/broker-config", tags=["Broker Config"])
# ── v2.0 补充：序列模型 + 审计 ────────────────────────────────────
api_router.include_router(sequence_models.router,     prefix="/quant",        tags=["Sequence Models"])
api_router.include_router(audit.router,               prefix="/audit",        tags=["Audit"])
api_router.include_router(data_sources.router,        prefix="/data-sources", tags=["Data Sources"])

# ── v3.0 Wave A ──────────────────────────────────────────────────
# 这五个子路由此前由各 endpoints 模块在文件末尾 `include_router` 寄生挂载
# （因为并行开发期 router.py 是禁改的共享文件）。集成时统一提到这里正式注册，
# 各模块尾部的寄生挂载已一并删除 —— 两处同时保留会导致路由重复注册。
api_router.include_router(factor_strategy.router,        prefix="/factors",   tags=["Factor Strategy"])
api_router.include_router(rebalance.router,              prefix="/portfolio", tags=["Portfolio Optimizer"])
api_router.include_router(backtest_batch.router,         prefix="/backtests", tags=["Backtests"])
api_router.include_router(backtest_full_validation.router, prefix="/backtests", tags=["Backtest Validation"])
api_router.include_router(backtest_history.router,       prefix="/backtests", tags=["Backtest History"])
api_router.include_router(portfolio_backtest.router,      prefix="/backtests", tags=["Portfolio Backtest"])
api_router.include_router(notifications.router,          prefix="/notify",    tags=["Notifications"])

# ── v4.0 Wave M ──────────────────────────────────────────────────
api_router.include_router(data_archive.router, prefix="/data",     tags=["Data Archive"])
api_router.include_router(universe.router,     prefix="/universe", tags=["Universe"])
api_router.include_router(lab.router,          prefix="/lab",      tags=["Lab Artifacts"])

# ── v3.0 Wave B-a：LLM 网关 ──────────────────────────────────────
api_router.include_router(llm.router, prefix="/llm", tags=["LLM Gateway"])
api_router.include_router(copilot.router, prefix="/copilot", tags=["Copilot"])

# ── v3.0 Wave C-a：AI 研报 / 回测诊断 ────────────────────────────
api_router.include_router(ai_reports.router, prefix="/ai/reports", tags=["AI Reports"])

# ── v3.0 Wave C-b：实盘对账（G6）+ 多用户（J3）─────────────────────
# reconcile 只读：对账端点不提交/撤销任何订单，也不自动纠正差异。
# users 全部端点要求 Role.ADMIN，本期只做「管理员维护账户」，不做自助注册。
api_router.include_router(reconcile.router, prefix="/reconcile", tags=["Reconcile"])
api_router.include_router(users.router,     prefix="/users",     tags=["Users"])

# ── v3.0 Wave C-c：自动因子研发循环（I2）────────────────────────
# 必须在 lab.router 之后注册：lab 的 /artifacts/{artifact_id} 不会吞掉
# /auto-loop（前缀不同段），但保持「专用路由跟在通用路由后面」的既有约定。
api_router.include_router(lab_auto_loop.router, prefix="/lab", tags=["Auto Factor Loop"])

# ── v4.0 Wave F-a：自适应再训练 + 漂移检测（M6）──────────────────
# 只有「触发 / 查历史」，**没有**上线端点：重训产出写进 LabStore 后由人工确认，
# 漂移检测只发通知不触发重训。两条立场与 I2 一致，详见端点模块 docstring。
api_router.include_router(retrain.router, prefix="/retrain", tags=["Adaptive Retrain"])
