"""Copilot 工具注册表（V3 Wave B-c / I1）—— 动作边界由代码保证

> 凡是会产生订单或改变实盘配置的动作，一律生成「待确认草稿」，绝不直接执行。
> 其余只读动作可直接执行。

**不要靠 prompt 让模型「记得」哪些要确认**：模型会忘、会被诱导、会幻觉出一个
不存在的只读工具名。所以：

1. `requires_confirmation` 的**默认值是 `True`**。新加了工具却忘了标注，
   失败模式是「多点一次确认」；如果默认 False，失败模式是「Copilot 可以未经确认下单」。
2. 需要确认的工具，它的 `handler` 被硬接成 `refuse_direct_execution` ——
   哪怕将来有人写错分支绕过了 `requires_confirmation` 判断，也是当场抛错，
   而不是悄悄把单子发出去。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.copilot import handlers
from app.copilot.args import (
    BacktestIdArgs,
    BacktestRequest,
    MarketArgs,
    QuoteArgs,
    RebalancePreviewRequest,
    ScreenerFilter,
    SubmitOrderRequest,
)
from app.copilot.context import CopilotContext
from app.copilot.drafts import CopilotDraft, build_order_draft, build_rebalance_draft
from app.copilot.schema_export import json_schema_for
from app.core.llm import ToolSpec

ToolHandler = Callable[[CopilotContext, Any], Awaitable[dict[str, Any]]]
DraftBuilder = Callable[[CopilotContext, Any], Awaitable[CopilotDraft]]


class DirectExecutionForbiddenError(RuntimeError):
    """写动作工具被直接执行了 —— 这是代码缺陷，必须当场炸掉。"""


async def refuse_direct_execution(ctx: CopilotContext, args: Any) -> dict[str, Any]:
    """写动作工具的 handler 占位：任何调用都是错误。"""
    del ctx, args
    raise DirectExecutionForbiddenError(
        "需要用户确认的工具不得被直接执行，只能生成待确认草稿。"
    )


@dataclass(frozen=True)
class CopilotTool:
    """一个可被模型调用的工具。"""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    #: True = 执行前必须由用户确认。**白名单式**：新增工具默认要确认，
    #: 要显式声明 `requires_confirmation=False` 才放行为只读直接执行。
    requires_confirmation: bool = True
    #: 需要确认的工具用它构造草稿；只读工具为 None
    draft_builder: DraftBuilder | None = None
    #: 只读工具产出的结构化卡片类型（前端据此选渲染器）
    card_kind: str = ""

    def __post_init__(self) -> None:
        if self.requires_confirmation and self.draft_builder is None:
            raise ValueError(f"工具 {self.name} 需要确认，必须提供 draft_builder")
        if self.requires_confirmation and self.handler is not refuse_direct_execution:
            raise ValueError(
                f"工具 {self.name} 需要确认，handler 必须是 refuse_direct_execution"
            )

    def to_spec(self) -> ToolSpec:
        """导出给 LLM 网关的工具声明（参数 schema 从 Pydantic 模型生成）。"""
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=json_schema_for(self.args_model),
        )


# ── 注册表 ───────────────────────────────────────────────────────────────────
#
# 只读工具必须显式写出 `requires_confirmation=False`，写动作工具什么都不用写 ——
# 这个不对称是刻意的：忘记标注只会让工具变得更保守。

COPILOT_TOOLS: tuple[CopilotTool, ...] = (
    CopilotTool(
        name="get_quote",
        description="查询单个标的的最新行情（开高低收 / 成交量）。只读。",
        args_model=QuoteArgs,
        handler=handlers.handle_get_quote,
        requires_confirmation=False,
        card_kind="quote",
    ),
    CopilotTool(
        name="screen_stocks",
        description=(
            "按市值 / 市盈率 / 涨跌幅 / 行业等条件筛选标的，返回匹配列表。只读。"
        ),
        args_model=ScreenerFilter,
        handler=handlers.handle_screen_stocks,
        requires_confirmation=False,
        card_kind="screener",
    ),
    CopilotTool(
        name="run_backtest",
        description=(
            "对单个标的运行一次策略回测，返回收益 / 风险 / 交易统计指标。只读，不影响实盘。"
        ),
        args_model=BacktestRequest,
        handler=handlers.handle_run_backtest,
        requires_confirmation=False,
        card_kind="backtest",
    ),
    CopilotTool(
        name="get_positions",
        description="查询当前实盘持仓（股数 / 成本 / 浮动盈亏）。只读。",
        args_model=MarketArgs,
        handler=handlers.handle_get_positions,
        requires_confirmation=False,
        card_kind="positions",
    ),
    CopilotTool(
        name="get_account",
        description="查询账户资金状态（现金 / 购买力 / 净值）。只读。",
        args_model=MarketArgs,
        handler=handlers.handle_get_account,
        requires_confirmation=False,
        card_kind="account",
    ),
    CopilotTool(
        name="explain_backtest",
        description="读取一条已保存的回测历史，用于解读其指标表现。只读。",
        args_model=BacktestIdArgs,
        handler=handlers.handle_explain_backtest,
        requires_confirmation=False,
        card_kind="backtest_history",
    ),
    # ── 以下是写动作：只生成草稿，绝不直接执行 ──
    CopilotTool(
        name="draft_order",
        description=(
            "生成一笔实盘订单的待确认草稿（不会下单）。用户在界面上确认后才会真正提交。"
        ),
        args_model=SubmitOrderRequest,
        handler=refuse_direct_execution,
        draft_builder=build_order_draft,
    ),
    CopilotTool(
        name="draft_rebalance",
        description=(
            "按目标权重生成组合再平衡的待确认草稿（不会下单）。"
            "会先算出股数级调仓明细，用户确认后才逐腿提交。"
        ),
        args_model=RebalancePreviewRequest,
        handler=refuse_direct_execution,
        draft_builder=build_rebalance_draft,
    ),
)

_BY_NAME: dict[str, CopilotTool] = {tool.name: tool for tool in COPILOT_TOOLS}


def get_tool(name: str) -> CopilotTool | None:
    """按名字取工具；模型幻觉出不存在的工具名时返回 None。"""
    return _BY_NAME.get(name)


def tool_specs(tools: tuple[CopilotTool, ...] = COPILOT_TOOLS) -> list[ToolSpec]:
    """导出全部工具声明，供 `provider.chat(tools=...)` 使用。"""
    return [tool.to_spec() for tool in tools]
