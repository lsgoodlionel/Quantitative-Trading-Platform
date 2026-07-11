"""
VWAP — 成交量加权平均价格拆单

按典型日内成交量曲线（U 型：开盘/收盘量大、盘中量小）给各时间片加权，
成交量高的时段分配更多股数，使执行更贴近市场 VWAP、降低冲击。

无实时逐笔成交量时，采用参数化 U 型曲线作为成交量代理（业界常用做法）。
"""

from __future__ import annotations

from app.oms.algos.base import ChildSlice, distribute_qty, u_shaped_weights


def plan_vwap(total_qty: int, duration_seconds: float, slice_count: int) -> list[ChildSlice]:
    """
    生成 VWAP 切片。

    - 时间上等间隔（与 TWAP 相同的排程）
    - 数量按 U 型成交量权重分配（端点多、盘中少）
    - 数量为 0 的切片被剔除
    """
    n = max(1, slice_count)
    weights = u_shaped_weights(n)
    qtys = distribute_qty(total_qty, weights)
    gap = duration_seconds / n if n > 0 else 0.0

    slices: list[ChildSlice] = []
    idx = 0
    for i, qty in enumerate(qtys):
        if qty <= 0:
            continue
        slices.append(ChildSlice(index=idx, qty=qty, delay_seconds=i * gap))
        idx += 1
    return slices
