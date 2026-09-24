"""只读工具处理器测试（V3 Wave B-c / I1）

两件事必须钉死：

1. **每个处理器都走既有端点函数**，不复制一份查询逻辑（monkeypatch 端点函数即可断言）。
2. **回灌给模型的结果是裁剪过的**：净值曲线、200 条筛选结果只进卡片，
   原样塞回对话窗口会撑爆本地小模型的上下文。

端点抛的 `HTTPException` 要被翻成一句人话，而不是冒泡成 500。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.backtests import BacktestMetricsResponse, BacktestResponse
from app.api.v1.endpoints.bars import BarResponse
from app.api.v1.endpoints.positions import AccountResponse, PositionResponse
from app.api.v1.endpoints.screener import CandidateOut, ScreenerRunResponse
from app.copilot import handlers
from app.copilot.args import BacktestIdArgs, MarketArgs, QuoteArgs, ScreenerFilter
from app.copilot.context import CopilotContext, ToolExecutionError

_SESSION = object()   # DataService 只是被构造，真实查询已被 monkeypatch 掉


def _ctx() -> CopilotContext:
    return CopilotContext(session=_SESSION)


def _returns(value: Any):
    async def _fn(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return value

    return _fn


def _raises(exc: Exception):
    async def _fn(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise exc

    return _fn


# ── get_quote ────────────────────────────────────────────────────────────────

async def test_get_quote_goes_through_the_bars_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar = BarResponse(
        time="2026-08-13T00:00:00", open=1.0, high=2.0, low=0.5, close=1.5, volume=100
    )
    monkeypatch.setattr(handlers.bars, "get_latest_bar", _returns(bar))

    result = await handlers.handle_get_quote(_ctx(), QuoteArgs(symbol="AAPL"))

    assert result["symbol"] == "AAPL"
    assert result["market"] == "US"
    assert result["close"] == 1.5


async def test_get_quote_reports_a_missing_symbol_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handlers.bars, "get_latest_bar", _returns(None))

    with pytest.raises(ToolExecutionError, match="没有查到"):
        await handlers.handle_get_quote(_ctx(), QuoteArgs(symbol="NOPE"))


async def test_get_quote_without_a_session_fails_loudly() -> None:
    """接线问题要明确报错，不能悄悄返回空结果。"""
    with pytest.raises(ToolExecutionError, match="数据库会话"):
        await handlers.handle_get_quote(CopilotContext(), QuoteArgs(symbol="AAPL"))


async def test_endpoint_http_errors_become_human_sentences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers.bars, "get_latest_bar", _raises(HTTPException(503, "Data feed error"))
    )

    with pytest.raises(ToolExecutionError, match=r"\[503\] Data feed error"):
        await handlers.handle_get_quote(_ctx(), QuoteArgs(symbol="AAPL"))


# ── screen_stocks ────────────────────────────────────────────────────────────

async def test_screen_stocks_truncates_what_it_feeds_back_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total = handlers.MAX_SCREENER_ROWS + 7
    monkeypatch.setattr(
        handlers.screener, "run_screener", _returns(_screener_response(total))
    )

    result = await handlers.handle_screen_stocks(_ctx(), ScreenerFilter())

    assert result["count"] == total
    assert result["truncated"] is True
    assert len(result["candidates"]) == handlers.MAX_SCREENER_ROWS
    # 完整列表只给前端卡片
    assert len(result["_card"]["candidates"]) == total


# ── run_backtest ─────────────────────────────────────────────────────────────

async def test_run_backtest_keeps_the_equity_curve_out_of_the_model_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handlers.backtests, "run_backtest", _returns(_backtest_response()))

    result = await handlers.handle_run_backtest(_ctx(), _backtest_request())

    assert "equity_curve" not in result
    assert result["metrics"]["sharpe_ratio"] == 0.0
    assert result["_card"]["equity_curve"] == [{"time": "2026-01-01", "value": 1.0}]


# ── 持仓 / 账户 ───────────────────────────────────────────────────────────────

async def test_get_positions_goes_through_the_positions_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [PositionResponse(symbol="AAPL", market="US", qty=10, avg_cost=100.0)]
    monkeypatch.setattr(handlers.positions, "list_positions", _returns(rows))

    result = await handlers.handle_get_positions(_ctx(), MarketArgs())

    assert result["count"] == 1
    assert result["positions"][0]["symbol"] == "AAPL"
    assert result["truncated"] is False


async def test_get_account_goes_through_the_positions_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = AccountResponse(
        account_id="A-1", currency="USD", cash=1.0, buying_power=2.0, portfolio_value=3.0
    )
    monkeypatch.setattr(handlers.positions, "get_account", _returns(account))

    result = await handlers.handle_get_account(_ctx(), MarketArgs())

    assert result["account_id"] == "A-1"
    assert result["market"] == "US"


# ── explain_backtest ─────────────────────────────────────────────────────────

async def test_explain_backtest_splits_curve_from_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "id": "r-1",
        "strategy_name": "double_ma",
        "metrics": {"sharpe_ratio": 1.2},
        "equity_curve": [{"time": "2026-01-01", "value": 1.0}],
    }
    monkeypatch.setattr(handlers.backtest_history, "get_history", _returns(record))

    result = await handlers.handle_explain_backtest(_ctx(), BacktestIdArgs(backtest_id="r-1"))

    assert "equity_curve" not in result
    assert result["metrics"]["sharpe_ratio"] == 1.2
    assert result["_card"]["equity_curve"]


async def test_unexpected_handler_failures_never_bubble_up_as_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers.backtest_history, "get_history", _raises(RuntimeError("boom"))
    )

    with pytest.raises(ToolExecutionError, match="工具执行失败：boom"):
        await handlers.handle_explain_backtest(_ctx(), BacktestIdArgs(backtest_id="r-1"))


# ── 测试数据 ─────────────────────────────────────────────────────────────────

def _screener_response(count: int) -> ScreenerRunResponse:
    return ScreenerRunResponse(
        market="US",
        generated_at="2026-08-13T00:00:00",
        universe_size=count,
        count=count,
        candidates=[
            CandidateOut(symbol=f"S{i}", market="US", name=f"N{i}", sector="Tech")
            for i in range(count)
        ],
    )


def _backtest_request():
    from datetime import date

    from app.copilot.args import BacktestRequest

    return BacktestRequest(
        strategy_name="double_ma",
        symbol="AAPL",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 1),
    )


def _backtest_response() -> BacktestResponse:
    metrics = BacktestMetricsResponse(**dict.fromkeys(BacktestMetricsResponse.model_fields, 0))
    return BacktestResponse(
        backtest_id="b-1",
        strategy_name="double_ma",
        symbol="AAPL",
        market="US",
        start_date="2026-01-01",
        end_date="2026-06-01",
        initial_cash=100_000.0,
        final_value=110_000.0,
        metrics=metrics,
        equity_curve=[{"time": "2026-01-01", "value": 1.0}],
        drawdown_series=[],
        monthly_returns={},
        pnl_distribution=[],
        fills=[],
        generated_at="2026-08-13T00:00:00",
    )
