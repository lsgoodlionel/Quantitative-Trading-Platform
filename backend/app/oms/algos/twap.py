"""
TWAP — 时间加权平均价格拆单

把父单等量拆成 slice_count 个子单，在 duration_seconds 内等间隔提交，
以摊平在时间轴上的市场冲击。子单数量用最大余数法处理零头。
"""

from __future__ import annotations

from app.oms.algos.base import ChildSlice, distribute_qty


def plan_twap(total_qty: int, duration_seconds: float, slice_count: int) -> list[ChildSlice]:
    """
    生成 TWAP 切片。

    - 等量：每片 ≈ total_qty / slice_count（零头补到前几片）
    - 等间隔：第 i 片在 i * (duration / slice_count) 秒提交
    - 数量为 0 的切片被剔除（total < slice_count 时自然发生）
    """
    n = max(1, slice_count)
    qtys = distribute_qty(total_qty, [1.0] * n)
    gap = duration_seconds / n if n > 0 else 0.0

    slices: list[ChildSlice] = []
    idx = 0
    for i, qty in enumerate(qtys):
        if qty <= 0:
            continue
        slices.append(ChildSlice(index=idx, qty=qty, delay_seconds=i * gap))
        idx += 1
    return slices
