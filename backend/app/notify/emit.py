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


def emit_data_gap(
    *,
    market: str,
    frequency: str,
    gaps: list[str],
    failures: list[str],
) -> dict:
    """
    归档下载后的缺口 / 失败明细（Wave O-a / O4，接 M-a 的归档缺口检测）。

    `gaps` 与 `failures` 都空时**不发通知** —— 每次下载都响一下，等于训练用户
    把这个通知当噪音忽略掉，真出缺口时也就没人看了。
    """
    if not gaps and not failures:
        return {"dispatched": 0, "skipped": True}

    payload: dict[str, Any] = {}
    if gaps:
        payload["缺口"] = _summarize_symbols(gaps)
    if failures:
        payload["下载失败"] = _summarize_symbols(failures)
    return notify_safe(NotifyEvent(
        type=NotifyEventType.DATA_GAP,
        title=f"归档存在数据缺口 · {market} {frequency}",
        market=market,
        payload=payload,
    ))


def emit_rebalance_executed(
    *,
    market: str,
    submitted: int,
    rejected: int,
    strategy_id: str | None = None,
) -> dict:
    """
    组合再平衡执行完成（Wave O-a / O4，接 V3 A-b 的 execute 端点）。

    部分成功是常态（`execute_legs` 不做整批回滚），所以两个计数都要给出来。
    """
    payload: dict[str, Any] = {"下单成功": submitted, "被拒": rejected}
    if strategy_id:
        payload["策略"] = strategy_id
    return notify_safe(NotifyEvent(
        type=NotifyEventType.REBALANCE_EXECUTED,
        title=f"再平衡已执行 · {market}",
        market=market,
        payload=payload,
    ))


def emit_retrain_done(
    *,
    model_kind: str,
    market: str,
    artifact_id: str | None,
    new_metrics: dict[str, Any],
    previous_metrics: dict[str, Any] | None,
    window: str = "",
) -> dict:
    """
    自适应再训练完成（V4 M6）。

    **必须把新旧指标并排放进 payload。** 只报新模型的 IC，用户没有任何依据判断
    这次重训是进步还是退步 —— 而这条通知存在的全部意义就是让他做这个判断。
    没有旧模型时明写「无对比基准」，不留空、不用 0 冒充。

    标题里写死「未上线」：这条通知是**待办**，不是完成报告。
    """
    payload: dict[str, Any] = {
        "模型": model_kind,
        "样本外 IC": new_metrics.get("ic", 0.0),
        "样本外 RankIC": new_metrics.get("rank_ic", 0.0),
        "样本外夏普": new_metrics.get("sharpe", 0.0),
    }
    if previous_metrics is None:
        payload["旧模型"] = "无对比基准"
    else:
        payload["旧模型 IC"] = previous_metrics.get("ic", 0.0)
        payload["旧模型 RankIC"] = previous_metrics.get("rank_ic", 0.0)
        payload["旧模型夏普"] = previous_metrics.get("sharpe", 0.0)
    if window:
        payload["训练窗口"] = window
    payload["产物"] = artifact_id or "入库失败"
    payload["状态"] = "已入库，**未上线** —— 需人工确认后替换"
    return notify_safe(NotifyEvent(
        type=NotifyEventType.RETRAIN_DONE,
        title=f"模型再训练完成（未上线）· {model_kind}",
        market=market,
        payload=payload,
    ))


def emit_retrain_failed(*, model_kind: str, market: str, reason: str) -> dict:
    """
    再训练失败（V4 M6）。

    失败必须发通知：一个每周静默失败的定时重训，表现是「模型三个月没更新过，
    而没有任何人知道」。payload 里明写旧模型未受影响，免得用户以为线上被弄坏了。
    """
    return notify_safe(NotifyEvent(
        type=NotifyEventType.RETRAIN_DONE,
        title=f"模型再训练失败 · {model_kind}",
        market=market,
        payload={
            "原因": reason,
            "影响": "线上模型未被改动（训练与评估全部完成后才写入，本次未走到写入）",
        },
    ))


def emit_model_drift(
    *,
    artifact_id: str,
    market: str,
    outlier_ratio: float,
    threshold: float,
    outlier_ratio_threshold: float,
    n_samples: int,
    sampling_note: str = "",
) -> dict:
    """
    特征分布漂移（V4 M6）。

    ⚠️ **只是通知，不触发任何动作。** payload 里明写「不会自动重训」——
    否则用户会以为系统已经在处理了，于是谁也不处理。

    原始 `outlier_ratio` 与两个阈值都要给出：`is_drifting` 是启发式判断，
    用户有权不同意它，但必须看得见判断依据。
    """
    payload: dict[str, Any] = {
        "模型产物": artifact_id,
        "离群样本占比": f"{outlier_ratio:.2%}",
        "离群占比阈值": f"{outlier_ratio_threshold:.2%}",
        "单样本 DI 阈值": threshold,
        "检测样本数": n_samples,
        "处置": "不会自动重训 —— 是否重训请人工判断",
    }
    if sampling_note:
        payload["口径"] = sampling_note
    return notify_safe(NotifyEvent(
        type=NotifyEventType.MODEL_DRIFT,
        title="特征分布疑似漂移",
        market=market,
        payload=payload,
    ))


def emit_reconcile_diff(
    *,
    market: str,
    diff_count: int,
    detail: str = "",
    broker_reachable: bool = True,
) -> dict:
    """
    实盘对账结果通知（V3 G6 接入 `app/oms/reconcile.py`）。

    `broker_reachable=False` 时标题与载荷都要说清「没对上账」而不是「对账正常」——
    此时 `diff_count` 恒为 0，若沿用「存在差异」的文案，用户会读成「0 条差异 = 通过」，
    那正是 G6 要避免的假阳性。

    ⚠️ 一致（可达且零差异）时**不要**调用本函数：判断在 `reconcile.notify_reconcile`，
    每次对账都响一下等于训练用户忽略它。
    """
    if not broker_reachable:
        payload: dict[str, Any] = {
            "状态": "券商不可达，未完成对账",
            "提示": "本次结果不代表账目一致，请检查券商连接后重跑",
        }
        if detail:
            payload["原因"] = detail
        return notify_safe(NotifyEvent(
            type=NotifyEventType.RECONCILE_DIFF,
            title="实盘对账未完成 · 券商不可达",
            market=market,
            payload=payload,
        ))

    payload = {"差异条数": diff_count}
    if detail:
        payload["明细"] = detail
    return notify_safe(NotifyEvent(
        type=NotifyEventType.RECONCILE_DIFF,
        title="实盘对账存在差异",
        market=market,
        payload=payload,
    ))
