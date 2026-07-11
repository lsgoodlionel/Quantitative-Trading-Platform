"""
AlgoExecutor — 高级订单算法异步调度器

职责：
- 依据算法类型构建切片计划（TWAP / VWAP / Iceberg）
- 为每个父单起一个 asyncio 后台任务，按切片累计延迟逐一提交子单
- 子单统一走现有 OMS.submit_order（同订单生命周期、同事件、同风控/防护）
- 跟踪父单进度，支持查询与撤销

设计：内存单例（镜像 OrderManager / ProtectionManager 的单例约定）。
子单提交复用全局 OrderManager，无需在启动时额外接线。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.oms.algos.base import (
    AlgoOrder,
    AlgoStatus,
    AlgoType,
    ChildSlice,
    SliceStatus,
)
from app.oms.algos.iceberg import plan_iceberg
from app.oms.algos.twap import plan_twap
from app.oms.algos.vwap import plan_vwap

logger = logging.getLogger(__name__)

# 参数边界（防误操作 / 资源保护）
MIN_SLICES = 1
MAX_SLICES = 100
MIN_DURATION_SECONDS = 1.0
MAX_DURATION_SECONDS = 6 * 60 * 60.0     # 单个算法单最长 6 小时
MAX_TOTAL_QTY = 1_000_000                 # 父单总量上限
DEFAULT_SLICES = 6
DEFAULT_DURATION_SECONDS = 300.0


class AlgoValidationError(Exception):
    """算法单参数校验失败。"""


class AlgoExecutor:
    """高级订单算法调度器（内存单例）。"""

    def __init__(self) -> None:
        self._algos: dict[str, AlgoOrder] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    # ── 提交 ──────────────────────────────────────────────────

    def submit_algo(
        self,
        *,
        symbol: str,
        market: str,
        side: str,
        total_qty: int,
        algo_type: AlgoType,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        strategy_id: Optional[str] = None,
        duration_seconds: float = DEFAULT_DURATION_SECONDS,
        slice_count: int = DEFAULT_SLICES,
        display_qty: Optional[int] = None,
    ) -> AlgoOrder:
        """校验参数 → 构建切片计划 → 起后台任务 → 返回父单。"""
        duration_seconds, slice_count = self._validate(
            total_qty=total_qty,
            algo_type=algo_type,
            order_type=order_type,
            limit_price=limit_price,
            duration_seconds=duration_seconds,
            slice_count=slice_count,
            display_qty=display_qty,
        )

        algo = AlgoOrder(
            symbol=symbol.upper(),
            market=market.upper(),
            side=side.upper(),
            total_qty=total_qty,
            algo_type=algo_type,
            order_type=order_type.upper(),
            limit_price=limit_price,
            strategy_id=strategy_id,
            duration_seconds=duration_seconds,
            slice_count=slice_count,
            display_qty=display_qty,
        )
        algo.slices = self._build_plan(algo)
        if not algo.slices:
            raise AlgoValidationError("切片计划为空（total_qty 过小）")

        self._algos[algo.algo_id] = algo
        self._tasks[algo.algo_id] = asyncio.create_task(self._run(algo))
        logger.info(
            "Algo submitted: %s %s %s x%d in %d slice(s)",
            algo.algo_type.value, algo.side, algo.symbol,
            algo.total_qty, len(algo.slices),
        )
        return algo

    def _validate(
        self,
        *,
        total_qty: int,
        algo_type: AlgoType,
        order_type: str,
        limit_price: Optional[float],
        duration_seconds: float,
        slice_count: int,
        display_qty: Optional[int],
    ) -> tuple[float, int]:
        if total_qty <= 0:
            raise AlgoValidationError("total_qty 必须为正")
        if total_qty > MAX_TOTAL_QTY:
            raise AlgoValidationError(f"total_qty 超过上限 {MAX_TOTAL_QTY}")
        if order_type.upper() == "LIMIT" and not limit_price:
            raise AlgoValidationError("限价算法单需提供 limit_price")
        if limit_price is not None and limit_price <= 0:
            raise AlgoValidationError("limit_price 必须为正")

        duration = float(
            min(max(duration_seconds, MIN_DURATION_SECONDS), MAX_DURATION_SECONDS)
        )
        slices = int(min(max(slice_count, MIN_SLICES), MAX_SLICES))

        if algo_type == AlgoType.ICEBERG:
            if not display_qty or display_qty <= 0:
                raise AlgoValidationError("冰山单需提供正的 display_qty")
        return duration, slices

    def _build_plan(self, algo: AlgoOrder) -> list[ChildSlice]:
        if algo.algo_type == AlgoType.TWAP:
            return plan_twap(algo.total_qty, algo.duration_seconds, algo.slice_count)
        if algo.algo_type == AlgoType.VWAP:
            return plan_vwap(algo.total_qty, algo.duration_seconds, algo.slice_count)
        if algo.algo_type == AlgoType.ICEBERG:
            return plan_iceberg(
                algo.total_qty, algo.display_qty or algo.total_qty, algo.duration_seconds
            )
        raise AlgoValidationError(f"未知算法类型: {algo.algo_type}")

    # ── 执行 ──────────────────────────────────────────────────

    async def _run(self, algo: AlgoOrder) -> None:
        """逐切片按累计延迟提交子单；异常/撤销时安全收尾。"""
        algo.status = AlgoStatus.RUNNING
        algo.started_at = datetime.now(timezone.utc)
        algo.touch()

        manager = self._resolve_manager(algo)
        if manager is None:
            self._finalize(algo)
            return

        try:
            elapsed = 0.0
            for sl in algo.slices:
                if algo.algo_id in self._cancelled:
                    sl.status = SliceStatus.SKIPPED
                    continue
                wait = max(0.0, sl.delay_seconds - elapsed)
                if wait > 0:
                    await asyncio.sleep(wait)
                elapsed = sl.delay_seconds
                if algo.algo_id in self._cancelled:
                    sl.status = SliceStatus.SKIPPED
                    continue
                await self._submit_slice(manager, algo, sl)
                algo.touch()
        except asyncio.CancelledError:
            for sl in algo.slices:
                if sl.status == SliceStatus.SCHEDULED:
                    sl.status = SliceStatus.SKIPPED
            raise
        finally:
            self._refresh_slices(manager, algo)
            self._finalize(algo)

    def _refresh_slices(self, manager, algo: AlgoOrder) -> None:
        """收尾时从 OMS 回读各子单最新成交状态（捕获异步网关的迟到成交）。"""
        if manager is None:
            return
        for sl in algo.slices:
            oid = getattr(sl, "child_order_id", None)
            if not oid:
                continue
            try:
                child = manager.get_order(oid)
            except Exception:      # noqa: BLE001
                child = None
            if child is None:
                continue
            sl.filled_qty = child.filled_qty
            sl.avg_fill_price = child.avg_fill_price
            status_val = child.status.value if hasattr(child.status, "value") else str(child.status)
            if status_val == "filled":
                sl.status = SliceStatus.FILLED
            elif status_val in ("cancelled", "rejected"):
                sl.status = SliceStatus.REJECTED if status_val == "rejected" else sl.status

    async def _submit_slice(self, manager, algo: AlgoOrder, sl: ChildSlice) -> None:
        """把单个切片作为普通实盘子单提交到 OMS。"""
        from app.oms.order import LiveOrderSide, LiveOrderType

        try:
            side = LiveOrderSide(algo.side)
            order_type = LiveOrderType(algo.order_type)
            child = await manager.submit_order(
                symbol=algo.symbol,
                market=algo.market,
                side=side,
                qty=sl.qty,
                order_type=order_type,
                limit_price=algo.limit_price,
                strategy_id=algo.strategy_id or f"algo:{algo.algo_type.value}:{algo.algo_id}",
            )
            sl.child_order_id = child.order_id
            sl.submitted_at = datetime.now(timezone.utc)
            sl.filled_qty = child.filled_qty
            sl.avg_fill_price = child.avg_fill_price
            status_val = child.status.value if hasattr(child.status, "value") else str(child.status)
            if status_val == "rejected":
                sl.status = SliceStatus.REJECTED
                sl.error = child.reject_reason
            elif status_val == "filled":
                sl.status = SliceStatus.FILLED
            else:
                sl.status = SliceStatus.SUBMITTED
        except Exception as e:      # noqa: BLE001 — 单片失败不应中断整个算法
            sl.status = SliceStatus.REJECTED
            sl.error = str(e)
            logger.warning("Algo slice submit failed (%s #%d): %s", algo.algo_id, sl.index, e)

    def _finalize(self, algo: AlgoOrder) -> None:
        if algo.status in (AlgoStatus.CANCELLED, AlgoStatus.FAILED, AlgoStatus.COMPLETED):
            return
        submitted = [s for s in algo.slices if s.status in (
            SliceStatus.SUBMITTED, SliceStatus.FILLED,
        )]
        if algo.algo_id in self._cancelled:
            algo.status = AlgoStatus.CANCELLED
        elif not submitted and algo.slices:
            algo.status = AlgoStatus.FAILED
        else:
            algo.status = AlgoStatus.COMPLETED
        algo.finished_at = datetime.now(timezone.utc)
        algo.touch()

    def _resolve_manager(self, algo: AlgoOrder):
        try:
            from app.oms.manager import get_order_manager
            return get_order_manager()
        except Exception as e:      # noqa: BLE001
            for sl in algo.slices:
                sl.status = SliceStatus.SKIPPED
                sl.error = "OMS 未初始化"
            logger.error("Algo aborted, OMS unavailable: %s", e)
            return None

    # ── 查询 / 撤销 ───────────────────────────────────────────

    def get_algo(self, algo_id: str) -> Optional[AlgoOrder]:
        return self._algos.get(algo_id)

    def list_algos(
        self,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[AlgoOrder]:
        items = list(self._algos.values())
        if strategy_id:
            items = [a for a in items if a.strategy_id == strategy_id]
        if status:
            items = [a for a in items if a.status.value == status]
        items.sort(key=lambda a: a.created_at, reverse=True)
        return items[:limit]

    def cancel_algo(self, algo_id: str) -> AlgoOrder:
        algo = self._algos.get(algo_id)
        if algo is None:
            raise KeyError(algo_id)
        if algo.status not in (AlgoStatus.PENDING, AlgoStatus.RUNNING):
            raise ValueError(f"算法单 {algo_id} 当前状态 {algo.status.value} 不可撤销")
        self._cancelled.add(algo_id)
        task = self._tasks.get(algo_id)
        if task and not task.done():
            task.cancel()
        # 已提交的子单不再回撤（尊重成交），仅停止后续切片
        algo.status = AlgoStatus.CANCELLED
        algo.finished_at = datetime.now(timezone.utc)
        algo.touch()
        logger.info("Algo cancelled: %s", algo_id)
        return algo

    async def shutdown(self) -> None:
        """应用关闭时取消所有在途算法任务（可选接线）。"""
        for algo_id, task in list(self._tasks.items()):
            self._cancelled.add(algo_id)
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ── 全局单例 ──────────────────────────────────────────────────

_executor: Optional[AlgoExecutor] = None


def get_algo_executor() -> AlgoExecutor:
    """按需构建执行器单例（首个请求触发；无需启动时接线）。"""
    global _executor
    if _executor is None:
        _executor = AlgoExecutor()
    return _executor
