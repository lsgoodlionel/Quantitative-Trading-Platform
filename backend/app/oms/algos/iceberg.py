"""
Iceberg — 冰山拆单

每次仅向市场「露出」固定的 display_qty 股，成交后再露出下一片，
直至父单总量提交完毕，从而隐藏真实委托规模、减少信息泄漏。

切片数 = ceil(total_qty / display_qty)，末片为剩余零头；各片在
duration_seconds 内等间隔提交（模拟「上一片消化后补下一片」的节奏）。
"""

from __future__ import annotations

import math

from app.oms.algos.base import ChildSlice


def plan_iceberg(
    total_qty: int, display_qty: int, duration_seconds: float
) -> list[ChildSlice]:
    """
    生成 Iceberg 切片。

    - 每片 display_qty 股，末片为剩余零头
    - 等间隔提交：第 i 片在 i * (duration / n) 秒提交
    - display_qty 非法时退化为一次性整单
    """
    if display_qty <= 0 or display_qty >= total_qty:
        return [ChildSlice(index=0, qty=total_qty, delay_seconds=0.0)]

    # 切片数上限，防止 total_qty 巨大 / display_qty 极小时产生海量子单
    _MAX_ICEBERG_SLICES = 100
    n = math.ceil(total_qty / display_qty)
    if n > _MAX_ICEBERG_SLICES:
        n = _MAX_ICEBERG_SLICES
        display_qty = math.ceil(total_qty / n)   # 抬高每片展示量以压回上限内
    gap = duration_seconds / n if n > 0 else 0.0

    slices: list[ChildSlice] = []
    remaining = total_qty
    for i in range(n):
        qty = min(display_qty, remaining)
        if qty <= 0:
            break
        slices.append(ChildSlice(index=i, qty=qty, delay_seconds=i * gap))
        remaining -= qty
    return slices
