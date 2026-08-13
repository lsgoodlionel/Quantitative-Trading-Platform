"""
再平衡执行服务（V3 Wave A-b · G2）

两件事：
1. `load_snapshot()` —— 从 OMS 实时拉持仓 / 净值 / 价格，拼出 `plan_rebalance` 的入参
2. `execute_legs()` —— 逐腿走 `OrderManager.submit_order`

**执行必须走 OMS，不得直连 gateway**：只有走 `submit_order` 才能拿到
`_pre_trade_risk_check`、L-a 的 `TradingControl`、protections 熔断与审计日志。

部分失败如实返回：某些腿成功、某些被拒是常态，不做整批回滚
（跨券商的原子性根本不存在，假装原子只会掩盖真实成交状态）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.engine.portfolio.rebalance import RebalanceLeg
from app.oms.manager import OrderManager, RiskViolationError
from app.oms.order import LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)

#: 逐笔佣金估算率（万分之三，仅用于预览展示，实际以券商回报为准）
COMMISSION_RATE = 0.0003


@dataclass(frozen=True)
class MarketSnapshot:
    """某个市场的实时持仓/净值/价格快照。"""

    market: str
    portfolio_value: float
    current_qty: dict[str, int] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionOutcome:
    """一次执行的结果。两个数组都可能非空 —— 部分成功是常态。"""

    submitted: tuple[dict, ...] = ()
    rejected: tuple[dict, ...] = ()


async def load_snapshot(
    oms: OrderManager, market: str, symbols: Sequence[str]
) -> MarketSnapshot:
    """
    拉取实时快照。价格优先取持仓回报里的现价，缺失的再回落到最新 K 线收盘价。

    任何一个标的取不到价格都不静默丢弃，而是写进 `warnings` 交给前端展示。
    """
    market = market.upper()
    positions = await oms.get_positions(market)
    account = await oms.get_account(market)

    current_qty = _qty_map(positions)
    prices = {
        p["symbol"]: float(p["current_price"])
        for p in positions
        if p.get("current_price")
    }

    missing = [s for s in symbols if s not in prices]
    if missing:
        prices.update(await fetch_reference_prices(missing, market))

    unpriced = sorted(s for s in symbols if s not in prices)
    warnings = [f"{s} 取不到有效价格，已从调仓计划中剔除" for s in unpriced]

    untargeted = sorted(s for s, q in current_qty.items() if q and s not in symbols)
    if untargeted:
        warnings.append(
            f"持有但不在目标权重中的标的不会被调整: {', '.join(untargeted)}"
            "（需要清仓请显式传入权重 0）"
        )

    return MarketSnapshot(
        market=market,
        portfolio_value=float(account["portfolio_value"]),
        current_qty=current_qty,
        prices=prices,
        warnings=tuple(warnings),
    )


async def current_positions(oms: OrderManager, market: str) -> dict[str, int]:
    """执行前重新拉一次持仓，用于比对确认令牌里的快照哈希。"""
    return _qty_map(await oms.get_positions(market.upper()))


def _qty_map(positions: Sequence[dict]) -> dict[str, int]:
    return {p["symbol"]: int(p["qty"]) for p in positions}


async def fetch_reference_prices(symbols: Sequence[str], market: str) -> dict[str, float]:
    """未持有标的的参考价：最新日线收盘。取不到就不放进结果，由调用方告警。"""
    from app.core.database import AsyncSessionLocal
    from app.data.models import Frequency, Market
    from app.data.service import DataService

    try:
        market_enum = Market(market.upper())
    except ValueError:
        logger.warning("未知市场 %s，跳过参考价补齐", market)
        return {}

    prices: dict[str, float] = {}
    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        for symbol in symbols:
            try:
                bar = await svc.get_latest_bar(symbol, market_enum, Frequency.DAY_1)
            except Exception as exc:   # 数据源故障不应让整个预览 500
                logger.warning("%s 参考价拉取失败: %s", symbol, exc)
                continue
            if bar is not None and bar.close > 0:
                prices[symbol] = float(bar.close)
    return prices


def estimate_commission(legs: Sequence[RebalanceLeg], rate: float = COMMISSION_RATE) -> float:
    """预估佣金（成交额 × 费率）。仅供预览展示。"""
    return round(sum(abs(leg.delta_value) for leg in legs) * rate, 2)


async def execute_legs(
    oms: OrderManager,
    market: str,
    legs: Sequence[RebalanceLeg],
    *,
    strategy_id: str | None = None,
) -> ExecutionOutcome:
    """逐腿提交市价单。任何一腿失败都只记录该腿，绝不回滚已成功的部分。"""
    submitted: list[dict] = []
    rejected: list[dict] = []

    for leg in legs:
        try:
            order = await oms.submit_order(
                symbol=leg.symbol,
                market=market.upper(),
                side=LiveOrderSide.BUY if leg.delta_qty > 0 else LiveOrderSide.SELL,
                qty=leg.abs_qty,
                order_type=LiveOrderType.MARKET,
                strategy_id=strategy_id,
            )
        except (RiskViolationError, RuntimeError, ValueError) as exc:
            logger.warning("再平衡下单失败 %s: %s", leg.symbol, exc)
            rejected.append(_rejection(leg, f"{type(exc).__name__}: {exc}"))
            continue

        if order.status == LiveOrderStatus.REJECTED:
            rejected.append(_rejection(leg, order.reject_reason or "被券商/风控拒绝"))
        else:
            submitted.append(
                {
                    "symbol": leg.symbol,
                    "order_id": order.order_id,
                    "side": leg.side,
                    "qty": leg.abs_qty,
                    "status": order.status.value,
                }
            )

    return ExecutionOutcome(submitted=tuple(submitted), rejected=tuple(rejected))


def _rejection(leg: RebalanceLeg, reason: str) -> dict:
    return {
        "symbol": leg.symbol,
        "side": leg.side,
        "qty": leg.abs_qty,
        "reason": reason,
    }
