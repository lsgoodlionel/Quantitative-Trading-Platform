"""待确认草稿（V3 Wave B-c / I1）

写动作**永不直接执行**，只产出草稿。草稿不是「把参数丢给前端」就完事 ——
一个只写着「买入 AAPL」而没有数量的确认按钮等于没有确认。所以每张草稿都带：

1. 校验过的完整参数（`params`，确认时原样回传，服务端会再校验一遍）
2. 人类可读的逐项展示（`fields`：标的 / 方向 / 数量 / 价格 / 预估金额）
3. 幂等标识（`draft_id`，同一张草稿只允许成功执行一次）
4. **执行时真正走的既有端点**（`endpoint`/`method`）—— 写进卡片里，
   任何人看一眼就知道 Copilot 没有另开一条绕过风控的通路

再平衡草稿必须先跑一次 `rebalance/preview`：拿到股数级明细与 `confirm_token`，
用户看到的就是将要下发的东西；确认时把这份明细原样交给 `rebalance/execute`，
期间价格或持仓变了，令牌校验会拒（409），而不是照单下发到市场上。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app.api.v1.endpoints import orders as orders_endpoint
from app.api.v1.endpoints import rebalance as rebalance_endpoint
from app.copilot.args import RebalancePreviewRequest, SubmitOrderRequest
from app.copilot.context import CopilotContext, ToolExecutionError
from app.data.models import Frequency, Market
from app.data.service import DataService

ORDER_ACTION = "order"
REBALANCE_ACTION = "rebalance"

ORDER_ENDPOINT = "POST /api/v1/orders"
REBALANCE_ENDPOINT = "POST /api/v1/portfolio/rebalance/execute"

_SIDE_LABELS = {"BUY": "买入", "SELL": "卖出"}
_TYPE_LABELS = {"MARKET": "市价单", "LIMIT": "限价单"}


@dataclass(frozen=True)
class DraftField:
    """草稿卡片上的一行。`emphasis` 交给前端加重展示（金额 / 数量）。"""

    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True)
class CopilotDraft:
    """一张待确认草稿。构造出来即代表「已校验、未执行」。"""

    draft_id: str
    tool: str
    action: str
    title: str
    summary: str
    fields: tuple[DraftField, ...]
    params: dict[str, Any]
    endpoint: str
    method: str = "POST"
    legs: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ── 订单草稿 ──────────────────────────────────────────────────────────────────

async def build_order_draft(ctx: CopilotContext, args: SubmitOrderRequest) -> CopilotDraft:
    """把一笔下单意图变成草稿。**不调用 OMS，不产生任何订单。**"""
    side = args.side.upper()
    order_type = args.order_type.upper()
    price, price_label, warnings = await _order_price(ctx, args, order_type)
    estimated = None if price is None else round(price * args.qty, 2)

    fields = [
        DraftField("标的", f"{args.symbol}（{args.market.upper()}）"),
        DraftField("方向", _SIDE_LABELS.get(side, side), emphasis=True),
        DraftField("数量", f"{args.qty} 股", emphasis=True),
        DraftField("订单类型", _TYPE_LABELS.get(order_type, order_type)),
        DraftField("价格", price_label),
        DraftField(
            "预估金额",
            "无法估算（缺少参考价）" if estimated is None else f"{estimated:,.2f}",
            emphasis=True,
        ),
    ]
    if args.strategy_id:
        fields.append(DraftField("归属策略", args.strategy_id))

    return CopilotDraft(
        draft_id=str(uuid.uuid4()),
        tool="draft_order",
        action=ORDER_ACTION,
        title=f"{_SIDE_LABELS.get(side, side)} {args.symbol} {args.qty} 股",
        summary="确认后将通过实盘下单端点提交，风控与角色校验与手动下单完全一致。",
        fields=tuple(fields),
        params=args.model_dump(mode="json"),
        endpoint=ORDER_ENDPOINT,
        warnings=tuple(warnings),
    )


async def _order_price(
    ctx: CopilotContext, args: SubmitOrderRequest, order_type: str
) -> tuple[float | None, str, list[str]]:
    """限价单用限价；市价单取最新收盘做参考价，取不到就如实说取不到。"""
    if order_type == "LIMIT":
        if args.limit_price is None:
            raise ToolExecutionError("限价单必须给出 limit_price。")
        return args.limit_price, f"限价 {args.limit_price:,.4f}", []

    reference = await _latest_close(ctx, args.symbol, args.market)
    if reference is None:
        return None, "市价（无参考价）", ["未取到最新行情，预估金额不可用。"]
    return reference, f"市价（参考最新收盘 {reference:,.4f}）", []


async def _latest_close(ctx: CopilotContext, symbol: str, market: str) -> float | None:
    """尽力取一次最新收盘价。取不到不是错误 —— 草稿照样能展示，只是没有估算。"""
    if ctx.session is None:
        return None
    try:
        bar = await DataService(ctx.session).get_latest_bar(
            symbol, Market(market.upper()), Frequency.DAY_1
        )
    except Exception:  # noqa: BLE001 —— 参考价拿不到不该让草稿构建失败
        return None
    return None if bar is None else bar.close


# ── 再平衡草稿 ────────────────────────────────────────────────────────────────

async def build_rebalance_draft(
    ctx: CopilotContext, args: RebalancePreviewRequest
) -> CopilotDraft:
    """先跑一次预览拿到股数级明细与确认令牌，再包成草稿。**不执行任何一条腿。**"""
    del ctx
    preview = await _preview(args)
    legs = [leg.model_dump() for leg in preview.legs]
    if not legs:
        raise ToolExecutionError("按当前持仓，这组目标权重不需要任何调仓。")

    fields = (
        DraftField("市场", preview.market),
        DraftField("组合净值", f"{preview.portfolio_value:,.2f}"),
        DraftField("调仓腿数", f"{len(legs)} 条", emphasis=True),
        DraftField("买入总额", f"{preview.total_buy_value:,.2f}", emphasis=True),
        DraftField("卖出总额", f"{preview.total_sell_value:,.2f}", emphasis=True),
        DraftField("预估佣金", f"{preview.estimated_commission:,.2f}"),
        DraftField("令牌有效期", f"{preview.expires_in_seconds} 秒"),
    )
    return CopilotDraft(
        draft_id=str(uuid.uuid4()),
        tool="draft_rebalance",
        action=REBALANCE_ACTION,
        title=f"再平衡 {preview.market} 组合（{len(legs)} 条腿）",
        summary=(
            "确认后将凭预览令牌逐腿走实盘下单端点。期间价格或持仓变化会导致令牌失效，"
            "届时请重新生成草稿而不是强行执行。"
        ),
        fields=fields,
        params={
            "market": preview.market,
            "legs": legs,
            "confirm_token": preview.confirm_token,
            "strategy_id": None,
        },
        endpoint=REBALANCE_ENDPOINT,
        legs=tuple(legs),
        warnings=tuple(preview.warnings),
    )


async def _preview(args: RebalancePreviewRequest):
    """调用既有预览端点；OMS 未配置等故障翻成一句人话。"""
    try:
        oms = orders_endpoint.get_oms()
        return await rebalance_endpoint.preview_rebalance(args, oms)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        raise ToolExecutionError(f"[{exc.status_code}] {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolExecutionError(f"生成调仓预览失败：{exc}") from exc
