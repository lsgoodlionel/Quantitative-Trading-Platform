"""Copilot 工具与动作边界测试（V3 Wave B-c / I1）

对应契约 docs/contracts/waveBc-copilot.md §四 验收 2：

- 新增工具默认 `requires_confirmation=True`（用反射断言，防的是「有人加了工具但忘了标注」）
- 只读工具被直接执行；写动作工具**从不**被执行，只产出草稿
- 草稿执行走既有端点（mock 断言调用路径，证明没绕过风控）

全部 LLM 调用都是假的 provider，不连任何模型。
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from app.api.v1.endpoints import orders as orders_endpoint
from app.api.v1.endpoints import rebalance as rebalance_endpoint
from app.api.v1.endpoints.auth import UserInfo
from app.copilot import drafts as drafts_module
from app.copilot import execute as execute_module
from app.copilot.args import RebalancePreviewRequest, SubmitOrderRequest
from app.copilot.context import CopilotContext
from app.copilot.engine import run_copilot_chat
from app.copilot.execute import (
    DraftExecutionError,
    execute_draft,
    reset_consumed_drafts,
)
from app.copilot.schema_export import json_schema_for
from app.copilot.tools import (
    COPILOT_TOOLS,
    CopilotTool,
    DirectExecutionForbiddenError,
    refuse_direct_execution,
)
from tests.copilot_fakes import FakeProvider, call, text_turn

#: 本期允许「直接执行、无需确认」的工具。改这份名单等于改动作边界，
#: 所以它必须写在测试里 —— 有人新增只读工具时会被迫来这里显式登记。
EXPECTED_READ_ONLY = {
    "get_quote",
    "screen_stocks",
    "run_backtest",
    "get_positions",
    "get_account",
    "explain_backtest",
}


def _trader() -> UserInfo:
    return UserInfo(id="t1", email="trader@test.local", role="trader")


# ── 结构性保证：默认必须是「需要确认」 ────────────────────────────────────────

def test_requires_confirmation_defaults_to_true_by_reflection() -> None:
    """反射断言字段默认值。默认 False 的失败模式是「未经确认下单」，不可接受。"""
    field = next(
        f for f in dataclasses.fields(CopilotTool) if f.name == "requires_confirmation"
    )

    assert field.default is True


def test_new_tool_without_the_flag_requires_confirmation() -> None:
    """「加了工具但忘了标注」的实际后果：多点一次确认，而不是直接下单。"""

    class _Args(BaseModel):
        x: int = 0

    tool = CopilotTool(
        name="some_new_tool",
        description="有人新加的工具，忘了写 requires_confirmation",
        args_model=_Args,
        handler=refuse_direct_execution,
        draft_builder=_never_called_draft,
    )

    assert tool.requires_confirmation is True


def test_registry_read_only_set_is_exactly_the_declared_one() -> None:
    """注册表里没有确认标志的工具，必须与显式登记的只读名单完全一致。"""
    read_only = {t.name for t in COPILOT_TOOLS if not t.requires_confirmation}

    assert read_only == EXPECTED_READ_ONLY


def test_write_tools_cannot_be_executed_even_if_dispatch_is_wrong() -> None:
    """写动作工具的 handler 被硬接成「拒绝执行」——绕过分支判断也炸，不会悄悄下单。"""
    write_tools = [t for t in COPILOT_TOOLS if t.requires_confirmation]

    assert {t.name for t in write_tools} == {"draft_order", "draft_rebalance"}
    for tool in write_tools:
        assert tool.handler is refuse_direct_execution
        assert tool.draft_builder is not None


async def test_refuse_direct_execution_raises() -> None:
    with pytest.raises(DirectExecutionForbiddenError):
        await refuse_direct_execution(CopilotContext(), None)


def test_confirming_tool_must_declare_a_draft_builder() -> None:
    class _Args(BaseModel):
        x: int = 0

    with pytest.raises(ValueError, match="draft_builder"):
        CopilotTool(
            name="broken",
            description="",
            args_model=_Args,
            handler=refuse_direct_execution,
        )


# ── 参数 schema 从既有 Pydantic 模型导出 ──────────────────────────────────────

def test_order_tool_schema_comes_from_the_endpoint_model() -> None:
    """手写一份 schema 会与端点校验漂移，模型照它生成的参数会 422。"""
    tool = next(t for t in COPILOT_TOOLS if t.name == "draft_order")

    assert tool.args_model is SubmitOrderRequest
    assert tool.to_spec().parameters == json_schema_for(SubmitOrderRequest)


def test_exported_schema_is_self_contained() -> None:
    """`$ref` 被展开：本地小模型对 `$ref` 的支持很不稳定。"""
    schema = json_schema_for(RebalancePreviewRequest)

    assert "$defs" not in schema
    assert "$ref" not in repr(schema)
    assert "target_weights" in schema["properties"]


def test_quote_schema_enumerates_the_same_markets_as_the_endpoint() -> None:
    tool = next(t for t in COPILOT_TOOLS if t.name == "get_quote")
    schema = tool.to_spec().parameters

    assert schema["properties"]["market"]["enum"] == ["US", "HK", "A"]


# ── 只读工具：直接执行 ────────────────────────────────────────────────────────

async def test_read_only_tool_is_executed_directly() -> None:
    executed: list[dict[str, Any]] = []

    async def _handler(ctx: CopilotContext, args: BaseModel) -> dict[str, Any]:
        del ctx
        executed.append(args.model_dump())
        return {"price": 189.5}

    tool = _read_only_tool(_handler)
    provider = FakeProvider([call("fake_quote", {"symbol": "AAPL"}), text_turn("现价 189.5")])

    outcome = await run_copilot_chat(
        provider, [_user_msg("AAPL 多少钱")], ctx=CopilotContext(), tools=(tool,)
    )

    assert executed == [{"symbol": "AAPL"}]
    assert outcome.text == "现价 189.5"
    assert outcome.drafts == ()
    assert [c.kind for c in outcome.cards] == ["quote"]


async def test_hallucinated_tool_name_is_reported_not_executed() -> None:
    """模型幻觉出一个不存在的只读工具名 —— 如实回错误，不假装成功。"""
    tool = _read_only_tool(_never_called_handler)
    provider = FakeProvider([call("get_secret_alpha", {}), text_turn("没有这个工具")])

    outcome = await run_copilot_chat(
        provider, [_user_msg("跑一下")], ctx=CopilotContext(), tools=(tool,)
    )

    assert outcome.text == "没有这个工具"
    assert any("不存在名为" in m.content for m in provider.last_messages)


# ── 写动作工具：只产出草稿 ────────────────────────────────────────────────────

async def test_write_tool_produces_a_draft_and_never_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[Any] = []
    monkeypatch.setattr(
        orders_endpoint, "submit_order", _spy_async(submitted), raising=True
    )
    provider = FakeProvider(
        [
            call(
                "draft_order",
                {
                    "symbol": "AAPL", "market": "US", "side": "BUY", "qty": 100,
                    "order_type": "LIMIT", "limit_price": 190.0,
                },
            ),
            text_turn("已生成草稿，请确认"),
        ]
    )

    outcome = await run_copilot_chat(
        provider, [_user_msg("买 100 股 AAPL")], ctx=CopilotContext()
    )

    assert submitted == []          # 全程没有任何订单被提交
    assert len(outcome.drafts) == 1
    draft = outcome.drafts[0]
    assert draft.action == "order"
    assert draft.endpoint == "POST /api/v1/orders"
    assert draft.params["qty"] == 100


async def test_order_draft_card_shows_every_parameter() -> None:
    """只写「买入 AAPL」而不写数量的确认按钮，等于没有确认。"""
    args = SubmitOrderRequest(
        symbol="AAPL", market="US", side="BUY", qty=100,
        order_type="LIMIT", limit_price=190.0,
    )

    draft = await drafts_module.build_order_draft(CopilotContext(), args)

    shown = {f.label: f.value for f in draft.fields}
    assert shown["标的"] == "AAPL（US）"
    assert shown["方向"] == "买入"
    assert shown["数量"] == "100 股"
    assert shown["订单类型"] == "限价单"
    assert shown["价格"] == "限价 190.0000"
    assert shown["预估金额"] == "19,000.00"


async def test_market_order_draft_is_honest_about_missing_reference_price() -> None:
    args = SubmitOrderRequest(symbol="AAPL", market="US", side="SELL", qty=5)

    draft = await drafts_module.build_order_draft(CopilotContext(session=None), args)

    shown = {f.label: f.value for f in draft.fields}
    assert shown["价格"] == "市价（无参考价）"
    assert shown["预估金额"] == "无法估算（缺少参考价）"
    assert draft.warnings


async def test_market_order_draft_uses_the_latest_close_as_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drafts_module, "DataService", _FakeDataService)
    args = SubmitOrderRequest(symbol="AAPL", market="US", side="BUY", qty=10)

    draft = await drafts_module.build_order_draft(CopilotContext(session=object()), args)

    shown = {f.label: f.value for f in draft.fields}
    assert shown["价格"] == "市价（参考最新收盘 150.0000）"
    assert shown["预估金额"] == "1,500.00"
    assert draft.warnings == ()


async def test_reference_price_failure_never_breaks_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取不到参考价只是「估不出金额」，不该让整张草稿构建失败。"""
    monkeypatch.setattr(drafts_module, "DataService", _BrokenDataService)
    args = SubmitOrderRequest(symbol="AAPL", market="US", side="BUY", qty=10)

    draft = await drafts_module.build_order_draft(CopilotContext(session=object()), args)

    assert {f.label: f.value for f in draft.fields}["预估金额"] == "无法估算（缺少参考价）"


async def test_limit_order_without_a_price_is_rejected() -> None:
    args = SubmitOrderRequest(
        symbol="AAPL", market="US", side="BUY", qty=10, order_type="LIMIT"
    )

    with pytest.raises(drafts_module.ToolExecutionError, match="limit_price"):
        await drafts_module.build_order_draft(CopilotContext(), args)


async def test_rebalance_draft_previews_before_asking_for_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """草稿里的明细必须是预览算出来的真东西，并带上确认令牌。"""
    monkeypatch.setattr(orders_endpoint, "get_oms", lambda: object())
    monkeypatch.setattr(
        rebalance_endpoint, "preview_rebalance", _fake_preview(), raising=True
    )

    draft = await drafts_module.build_rebalance_draft(
        CopilotContext(), RebalancePreviewRequest(target_weights={"AAPL": 0.6, "MSFT": 0.4})
    )

    assert draft.action == "rebalance"
    assert draft.params["confirm_token"] == "tok-123"
    assert len(draft.legs) == 1
    assert {f.label for f in draft.fields} >= {"买入总额", "卖出总额", "预估佣金"}


async def test_rebalance_draft_refuses_when_nothing_needs_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orders_endpoint, "get_oms", lambda: object())
    monkeypatch.setattr(
        rebalance_endpoint, "preview_rebalance", _fake_preview(legs=[]), raising=True
    )

    with pytest.raises(drafts_module.ToolExecutionError, match="不需要任何调仓"):
        await drafts_module.build_rebalance_draft(
            CopilotContext(), RebalancePreviewRequest(target_weights={"AAPL": 1.0})
        )


async def test_rebalance_draft_translates_broker_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OMS 没配就说 OMS 没配，不要把 503 直接甩给模型。"""

    def _boom() -> Any:
        raise HTTPException(503, "Order management system not available")

    monkeypatch.setattr(orders_endpoint, "get_oms", _boom)

    with pytest.raises(drafts_module.ToolExecutionError, match=r"\[503\]"):
        await drafts_module.build_rebalance_draft(
            CopilotContext(), RebalancePreviewRequest(target_weights={"AAPL": 1.0})
        )


# ── 引擎：错误如实回灌，不产出草稿 ────────────────────────────────────────────

async def test_invalid_tool_arguments_are_reported_back_to_the_model() -> None:
    """参数校验失败要回灌成一句错误，让模型有机会改；不能吞掉也不能 500。"""
    tool = _read_only_tool(_never_called_handler)
    provider = FakeProvider([call("fake_quote", {"symbol": 123, "extra": True}), text_turn("嗯")])

    outcome = await run_copilot_chat(
        provider, [_user_msg("查一下")], ctx=CopilotContext(), tools=(tool,)
    )

    assert outcome.drafts == ()
    assert any("参数不合法" in m.content for m in provider.last_messages)


async def test_draft_build_failure_produces_no_draft() -> None:
    """草稿构造失败（比如券商没配）→ 一句错误，绝不留下半张草稿。"""

    async def _failing_draft(ctx: CopilotContext, args: BaseModel):
        del ctx, args
        raise drafts_module.ToolExecutionError("券商未配置")

    tool = CopilotTool(
        name="fake_write",
        description="",
        args_model=_QuoteArgs,
        handler=refuse_direct_execution,
        draft_builder=_failing_draft,
    )
    provider = FakeProvider([call("fake_write", {"symbol": "AAPL"}), text_turn("失败了")])

    outcome = await run_copilot_chat(
        provider, [_user_msg("下单")], ctx=CopilotContext(), tools=(tool,)
    )

    assert outcome.drafts == ()
    assert any("券商未配置" in m.content for m in provider.last_messages)


# ── 草稿执行：走既有端点 ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_idempotency() -> None:
    reset_consumed_drafts()


async def test_order_draft_executes_through_the_existing_orders_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证明没有为 Copilot 另开一条绕过风控的下单通路。"""
    seen: list[Any] = []
    monkeypatch.setattr(orders_endpoint, "get_oms", lambda: "OMS")
    monkeypatch.setattr(
        execute_module.orders_endpoint, "submit_order", _spy_async(seen), raising=True
    )

    result = await execute_draft(
        draft_id="d-1",
        action="order",
        params={"symbol": "AAPL", "market": "US", "side": "BUY", "qty": 10},
        user=_trader(),
    )

    assert result["action"] == "order"
    body, oms, user = seen[0]
    assert isinstance(body, SubmitOrderRequest)   # 参数被重新校验过
    assert body.symbol == "AAPL"
    assert oms == "OMS"                           # 走的是端点自己的 OMS 依赖
    assert user.role == "trader"


async def test_rebalance_draft_executes_through_the_existing_execute_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(orders_endpoint, "get_oms", lambda: "OMS")
    monkeypatch.setattr(
        execute_module.rebalance_endpoint, "execute_rebalance", _spy_async(seen), raising=True
    )

    await execute_draft(
        draft_id="d-2", action="rebalance", params=_REBALANCE_PARAMS, user=_trader()
    )

    body = seen[0][0]
    assert isinstance(body, rebalance_endpoint.RebalanceExecuteRequest)
    assert body.confirm_token == "tok-123"        # 令牌校验仍由端点执行


async def test_a_draft_cannot_be_executed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orders_endpoint, "get_oms", lambda: "OMS")
    monkeypatch.setattr(
        execute_module.orders_endpoint, "submit_order", _spy_async([]), raising=True
    )
    params = {"symbol": "AAPL", "market": "US", "side": "BUY", "qty": 10}

    await execute_draft(draft_id="d-3", action="order", params=params, user=_trader())

    with pytest.raises(DraftExecutionError) as exc:
        await execute_draft(draft_id="d-3", action="order", params=params, user=_trader())
    assert exc.value.status_code == 409


async def test_invalid_draft_params_are_rejected_without_consuming_the_draft() -> None:
    with pytest.raises(DraftExecutionError) as exc:
        await execute_draft(
            draft_id="d-4", action="order", params={"symbol": "AAPL"}, user=_trader()
        )

    assert exc.value.status_code == 422


async def test_unknown_draft_action_is_rejected() -> None:
    with pytest.raises(DraftExecutionError) as exc:
        await execute_draft(
            draft_id="d-5", action="wire_transfer", params={}, user=_trader()
        )

    assert exc.value.status_code == 400


# ── 测试辅助 ─────────────────────────────────────────────────────────────────

_REBALANCE_PARAMS = {
    "market": "US",
    "confirm_token": "tok-123",
    "legs": [
        {
            "symbol": "AAPL", "current_qty": 0, "target_qty": 10, "delta_qty": 10,
            "price": 100.0, "delta_value": 1000.0, "reason": "buy", "side": "BUY",
        }
    ],
}


class _QuoteArgs(BaseModel):
    symbol: str


def _user_msg(content: str):
    from app.core.llm import ChatMessage

    return ChatMessage(role="user", content=content)


def _read_only_tool(handler) -> CopilotTool:
    return CopilotTool(
        name="fake_quote",
        description="测试用只读工具",
        args_model=_QuoteArgs,
        handler=handler,
        requires_confirmation=False,
        card_kind="quote",
    )


async def _never_called_handler(ctx: CopilotContext, args: BaseModel) -> dict[str, Any]:
    raise AssertionError("这个处理器不应该被调用")


async def _never_called_draft(ctx: CopilotContext, args: BaseModel):
    raise AssertionError("这个草稿构造器不应该被调用")


def _spy_async(sink: list[Any]):
    async def _spy(*args: Any, **kwargs: Any):
        sink.append(args)
        return _StubResult()

    return _spy


class _StubResult:
    def model_dump(self) -> dict[str, Any]:
        return {"ok": True}


class _FakeBar:
    close = 150.0


class _FakeDataService:
    """`drafts._latest_close` 只用到 `get_latest_bar`。"""

    def __init__(self, session: Any) -> None:
        del session

    async def get_latest_bar(self, symbol: Any, market: Any, frequency: Any) -> _FakeBar:
        del symbol, market, frequency
        return _FakeBar()


class _BrokenDataService(_FakeDataService):
    async def get_latest_bar(self, symbol: Any, market: Any, frequency: Any):
        raise RuntimeError("行情源挂了")


def _fake_preview(legs: list | None = None):
    default_legs = [
        rebalance_endpoint.RebalanceLegSchema(
            symbol="AAPL", current_qty=0, target_qty=10, delta_qty=10,
            price=100.0, delta_value=1000.0, reason="buy", side="BUY",
        )
    ]

    async def _preview(body, oms):
        del body, oms
        return rebalance_endpoint.RebalancePreviewResponse(
            market="US",
            portfolio_value=100_000.0,
            legs=default_legs if legs is None else legs,
            total_buy_value=1000.0,
            total_sell_value=0.0,
            estimated_commission=1.0,
            warnings=[],
            confirm_token="tok-123",
            expires_in_seconds=120,
        )

    return _preview
