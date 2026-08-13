"""
通知旁路发射器（V3 G5）

长任务（回测 / 参数寻优 / 因子挖掘）与运行状态（数据源降级 / 对账差异）完成后
在此统一发事件。

**唯一允许吞异常的地方**：通知是旁路，一次 Telegram 超时不该把回测结果弄丢。
所有发射都经 `notify_safe`，异常记 `logger.exception` 而非静默丢弃。
"""

from __future__ import annotations

import logging
from typing import Any

from app.notify.config import NotifyEventType
from app.notify.events import NotifyEvent

logger = logging.getLogger(__name__)

# 事件 payload 里列出的标的数量上限（避免几百个 symbol 撑爆消息体）
_MAX_LISTED_SYMBOLS = 10


def notify_safe(event: NotifyEvent) -> dict:
    """
    发射一个通知事件，失败绝不上抛。

    返回 dispatch_event 的结果；失败时返回 {"dispatched": 0, "failed": True}。
    """
    try:
        from app.notify.dispatcher import dispatch_event

        return dispatch_event(event)
    except Exception:
        logger.exception("通知发送失败，不影响任务结果 · event=%s", event.type.value)
        return {"dispatched": 0, "failed": True}


def _summarize_symbols(symbols: list[str]) -> str:
    """标的列表摘要：超过上限时以 `A, B, … (共 N 个)` 形式收敛。"""
    listed = list(symbols[:_MAX_LISTED_SYMBOLS])
    if len(symbols) > _MAX_LISTED_SYMBOLS:
        return f"{', '.join(listed)} … (共 {len(symbols)} 个)"
    return ", ".join(listed)


def emit_backtest_done(
    *,
    strategy_name: str,
    symbol: str,
    market: str,
    metrics: dict[str, Any],
) -> dict:
    """单标的回测完成。"""
    return notify_safe(NotifyEvent(
        type=NotifyEventType.BACKTEST_DONE,
        title=f"回测完成 · {strategy_name}",
        symbol=symbol,
        market=market,
        payload={
            "总收益率": f"{metrics.get('total_return_pct', 0.0)}%",
            "夏普比率": metrics.get("sharpe_ratio", 0.0),
            "最大回撤": f"{metrics.get('max_drawdown_pct', 0.0)}%",
            "交易次数": metrics.get("total_trades", 0),
        },
    ))


def emit_batch_backtest_done(
    *,
    strategy_name: str,
    market: str,
    symbols: list[str],
    succeeded: int,
    failed: int,
) -> dict:
    """批量回测完成（含失败计数，便于用户判断是否需要复跑）。"""
    return notify_safe(NotifyEvent(
        type=NotifyEventType.BACKTEST_DONE,
        title=f"批量回测完成 · {strategy_name}",
        market=market,
        payload={
            "标的": _summarize_symbols(symbols),
            "成功": succeeded,
            "失败": failed,
        },
    ))


def emit_hyperopt_done(
    *,
    strategy_name: str,
    symbol: str,
    market: str,
    best_params: dict[str, Any],
    best_loss: float | None,
    evaluated: int,
) -> dict:
    """参数寻优完成。"""
    return notify_safe(NotifyEvent(
        type=NotifyEventType.HYPEROPT_DONE,
        title=f"参数寻优完成 · {strategy_name}",
        symbol=symbol,
        market=market,
        payload={
            "最优参数": best_params,
            "最优损失": best_loss,
            "评估组合数": evaluated,
        },
    ))


def emit_mining_done(
    *,
    market: str,
    symbols: list[str],
    generations: int,
    best_expr: str | None,
    best_fitness: float | None,
) -> dict:
    """遗传因子挖掘完成。"""
    return notify_safe(NotifyEvent(
        type=NotifyEventType.MINING_DONE,
        title="因子挖掘完成",
        market=market,
        payload={
            "标的": _summarize_symbols(symbols),
            "进化代数": generations,
            "最优因子": best_expr or "未找到有效个体",
            "适应度": best_fitness,
        },
    ))


def emit_data_source_degraded(
    *,
    market: str,
    failed_sources: list[str],
    active_source: str | None,
) -> dict:
    """数据源降级：全部真实源探活失败，或当前生效源发生回退。"""
    return notify_safe(NotifyEvent(
        type=NotifyEventType.DATA_SOURCE_DEGRADED,
        title=f"数据源降级 · {market}",
        market=market,
        payload={
            "失败源": ", ".join(failed_sources) or "—",
            "当前生效源": active_source or "无（已降级到演示数据）",
        },
    ))


def emit_price_alert(
    *,
    symbol: str,
    market: str,
    condition: str,
    threshold: float,
    price: float,
    note: str = "",
) -> dict:
    """
    价格预警触发（改走统一通知总线，类型 risk_alert）。

    risk_alert 属于 IN_APP_ONLY_DEFAULT_EVENTS：默认只进站内通知中心，
    用户必须在渠道里主动勾选才会推送到 Telegram/Webhook。
    """
    payload: dict[str, Any] = {
        "触发条件": condition,
        "阈值": threshold,
        "当前价格": price,
    }
    if note:
        payload["备注"] = note
    return notify_safe(NotifyEvent(
        type=NotifyEventType.RISK_ALERT,
        title=f"价格预警触发 · {symbol}",
        symbol=symbol,
        market=market,
        payload=payload,
    ))


def emit_reconcile_diff(
    *,
    market: str,
    diff_count: int,
    detail: str = "",
) -> dict:
    """实盘对账差异（G6 预留；本期只提供发射器，不接对账逻辑）。"""
    payload: dict[str, Any] = {"差异条数": diff_count}
    if detail:
        payload["明细"] = detail
    return notify_safe(NotifyEvent(
        type=NotifyEventType.RECONCILE_DIFF,
        title="实盘对账存在差异",
        market=market,
        payload=payload,
    ))
