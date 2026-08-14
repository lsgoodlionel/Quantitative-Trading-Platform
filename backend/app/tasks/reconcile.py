"""
实盘对账定时任务（V3 · G6）

沿用 `app/tasks/data.py` / `archive.py` 的「`asyncio.run()` 桥接」约定：
Celery worker 里没有 FastAPI 的依赖注入，任务自己把 async 调用桥到同步。
**不新建基础设施。**

⚠️ **跨进程的范围差异，必须在读结果时先看懂**

`OrderManager` 的订单簿是**进程内内存态**。Celery worker 与 FastAPI 进程各有一份，
worker 里那份通常是空的 —— 于是券商侧每一个持仓都会被算成一条「本地 0 / 券商 N」的差异。
这不是账目错误，是范围差异。

因此本任务：

* 结果体里始终带 `scope` / `scope_note` / `local_order_count`，让人一眼看出本地侧的口径；
* `local_order_count == 0` 且差异全部来自「本地为 0」时，把 `suppressed_reason` 写进结果并
  **不发通知** —— 一个空订单簿对不出任何有意义的差异，为它响铃只会让真差异被淹没；
* 但**券商不可达照发**：那与本地订单簿是否为空无关，是需要人介入的状态。

要拿到有意义的对账结果，请走 API 端点 `POST /api/v1/reconcile/{market}`
（在 FastAPI 进程内跑，OMS 就是那个真正下过单的实例）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from celery import shared_task

if TYPE_CHECKING:
    from app.oms.reconcile import ReconcileResult

logger = logging.getLogger(__name__)

# 一次 beat 触发默认对哪些市场对账
DEFAULT_MARKETS = ("US", "HK", "A")


@shared_task(name="app.tasks.reconcile.reconcile_market")
def reconcile_market(market: str) -> dict:
    """
    对单个市场执行一次只读对账，按需发 `RECONCILE_DIFF` 通知。

    Returns:
        `ReconcileResult.to_dict()` 再加 `notified` / `suppressed_reason`。
        **`broker_reachable=False` 时 `position_diffs` 为空不代表一致。**
    """
    try:
        return asyncio.run(_reconcile_one(market))
    except Exception as exc:  # noqa: BLE001
        # 对账任务自身炸了同样不能被读成「对账通过」，显式给出不可达结果
        logger.exception("对账任务失败 · market=%s", market)
        return {
            "market": market,
            "broker_reachable": False,
            "is_clean": False,
            "diff_count": 0,
            "error": f"对账任务异常: {exc}",
            "notified": False,
        }


@shared_task(name="app.tasks.reconcile.reconcile_all_markets")
def reconcile_all_markets(markets: list[str] | None = None) -> dict:
    """对多个市场依次对账（beat 的默认入口）。"""
    targets = list(markets or DEFAULT_MARKETS)
    return {"results": [reconcile_market(market) for market in targets]}


async def _reconcile_one(market: str) -> dict:
    """真正的对账逻辑；网关取不到时同样返回「不可达」而非抛错。"""
    from app.data.models import Market
    from app.oms.manager import get_order_manager
    from app.oms.reconcile import notify_reconcile, reconcile

    market_enum = Market(market.upper())
    oms = get_order_manager()

    try:
        gateway = oms.get_gateway(market_enum.value)
    except Exception as exc:  # noqa: BLE001
        logger.error("对账取不到网关 · market=%s：%s", market, exc)
        return {
            "market": market_enum.value,
            "broker_reachable": False,
            "is_clean": False,
            "diff_count": 0,
            "error": f"未注册 {market_enum.value} 交易网关: {exc}",
            "notified": False,
        }

    result = await reconcile(market_enum, oms, gateway)
    payload = result.to_dict()

    suppressed = _suppression_reason(result)
    if suppressed:
        payload["notified"] = False
        payload["suppressed_reason"] = suppressed
        logger.info("对账差异被抑制 · market=%s · %s", market_enum.value, suppressed)
        return payload

    dispatch = notify_reconcile(result)
    payload["notified"] = not dispatch.get("skipped", False)
    return payload


def _suppression_reason(result: ReconcileResult) -> str:
    """
    判断这批差异是否纯属「worker 进程订单簿为空」的范围差异。

    券商不可达永远不抑制 —— 那与订单簿是否为空无关。
    """
    if not result.broker_reachable:
        return ""
    if result.local_order_count > 0 or not result.position_diffs:
        return ""
    if any(d.local_qty != 0 for d in result.position_diffs):
        return ""
    return (
        "worker 进程 OMS 订单簿为空，差异全部来自本地无记录（范围差异而非账目错误）；"
        "请改用 API 端点在交易进程内对账"
    )
