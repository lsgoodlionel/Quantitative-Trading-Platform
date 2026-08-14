"""只读工具的处理器（V3 Wave B-c / I1）

**每个处理器都调用既有端点函数**，不复制一份查询逻辑：端点怎么校验、怎么把
券商故障翻成 503，工具走的就是同一条路。端点抛的 `HTTPException` 在这里被翻成
`ToolExecutionError`，由对话引擎回灌成一句人话，模型可以据此换个问法。

**结果一律裁剪后再回灌模型**。一次回测的净值曲线有几千个点，筛选结果可能 200 条 ——
原样塞回对话窗口会瞬间撑爆本地小模型的上下文，还要为此付 token。这里只回摘要，
完整数据走 `card` 直接给前端渲染。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.v1.endpoints import backtest_history, backtests, bars, positions, screener
from app.copilot.args import (
    BacktestIdArgs,
    BacktestRequest,
    MarketArgs,
    QuoteArgs,
    ScreenerFilter,
)
from app.copilot.context import CopilotContext, ToolExecutionError
from app.data.service import DataService
from app.data.storage.backtest_history import PostgresBacktestHistoryStore

#: 回灌给模型的筛选结果条数上限 —— 再多也帮不上忙，只是烧 token
MAX_SCREENER_ROWS = 15
#: 回灌给模型的持仓条数上限
MAX_POSITION_ROWS = 30


async def handle_get_quote(ctx: CopilotContext, args: QuoteArgs) -> dict[str, Any]:
    """最新行情（走 `GET /bars/latest` 的端点函数）。"""
    service = DataService(ctx.require_session())
    bar = await _call(
        bars.get_latest_bar(
            symbol=args.symbol, market=args.market, frequency=args.frequency, svc=service
        )
    )
    if bar is None:
        raise ToolExecutionError(
            f"没有查到 {args.symbol}（{args.market.value}）的行情，请确认代码与市场是否匹配。"
        )
    return {
        "symbol": args.symbol,
        "market": args.market.value,
        "frequency": args.frequency.value,
        **bar.model_dump(),
    }


async def handle_screen_stocks(ctx: CopilotContext, args: ScreenerFilter) -> dict[str, Any]:
    """条件筛选（走 `POST /screener/run` 的端点函数）。"""
    del ctx  # 筛选走快照服务，不需要数据库会话
    result = await _call(screener.run_screener(args))
    rows = [c.model_dump() for c in result.candidates]
    return {
        "market": result.market,
        "universe_size": result.universe_size,
        "count": result.count,
        "truncated": len(rows) > MAX_SCREENER_ROWS,
        "candidates": rows[:MAX_SCREENER_ROWS],
        # 完整列表只给前端卡片，不回灌模型
        "_card": {"candidates": rows, "count": result.count, "market": result.market},
    }


async def handle_run_backtest(ctx: CopilotContext, args: BacktestRequest) -> dict[str, Any]:
    """单标的回测（走 `POST /backtests/run` 的端点函数）。"""
    service = DataService(ctx.require_session())
    result = await _call(backtests.run_backtest(args, service))
    metrics = result.metrics.model_dump()
    return {
        "backtest_id": result.backtest_id,
        "strategy_name": result.strategy_name,
        "symbol": result.symbol,
        "market": result.market,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "initial_cash": result.initial_cash,
        "final_value": result.final_value,
        "metrics": metrics,
        # 净值曲线只给卡片，不回灌模型
        "_card": {
            "backtest_id": result.backtest_id,
            "strategy_name": result.strategy_name,
            "symbol": result.symbol,
            "final_value": result.final_value,
            "metrics": metrics,
            "equity_curve": result.equity_curve,
        },
    }


async def handle_get_positions(ctx: CopilotContext, args: MarketArgs) -> dict[str, Any]:
    """实盘持仓（走 `GET /positions` 的端点函数）。"""
    del ctx
    rows = await _call(positions.list_positions(market=args.market.value))
    dumped = [p.model_dump() for p in rows]
    return {
        "market": args.market.value,
        "count": len(dumped),
        "positions": dumped[:MAX_POSITION_ROWS],
        "truncated": len(dumped) > MAX_POSITION_ROWS,
        "_card": {"market": args.market.value, "positions": dumped},
    }


async def handle_get_account(ctx: CopilotContext, args: MarketArgs) -> dict[str, Any]:
    """账户资金（走 `GET /positions/account` 的端点函数）。"""
    del ctx
    account = await _call(positions.get_account(market=args.market.value))
    data = account.model_dump()
    return {**data, "market": args.market.value, "_card": data}


async def handle_explain_backtest(
    ctx: CopilotContext, args: BacktestIdArgs
) -> dict[str, Any]:
    """解读已有回测（走 `GET /backtests/history/{id}` 的端点函数）。"""
    store = PostgresBacktestHistoryStore(ctx.require_session())
    record = await _call(backtest_history.get_history(args.backtest_id, store))
    curve = record.get("equity_curve") or []
    summary = {k: v for k, v in record.items() if k != "equity_curve"}
    return {**summary, "_card": {**summary, "equity_curve": curve}}


# ── 内部 ─────────────────────────────────────────────────────────────────────

async def _call(awaitable: Any) -> Any:
    """执行端点函数，把它抛的 HTTP 错误翻成一句可回灌模型的人话。"""
    try:
        return await awaitable
    except HTTPException as exc:
        raise ToolExecutionError(_describe(exc)) from exc
    except ToolExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 工具失败绝不该冒泡成 500
        raise ToolExecutionError(f"工具执行失败：{exc}") from exc


def _describe(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return f"[{exc.status_code}] {detail}"
