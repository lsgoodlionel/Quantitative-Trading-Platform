"""高级订单算法拆单单元测试（Wave-3 / OMS algos）

覆盖：
- Iceberg：巨量 total_qty 时切片数 <= 100（上限保护）、切片量之和 == total、延迟递增
- TWAP：等量拆分、切片数受 [MIN,MAX] 约束、qty 之和 == total、延迟等间隔递增
- VWAP：U 型量能加权（两端多、盘中少）、qty 之和 == total、延迟递增
- 数量分配工具 distribute_qty：守恒、最大余数法、非法权重退化为等分
"""

from __future__ import annotations

import pytest

from app.oms.algos.base import distribute_qty, u_shaped_weights
from app.oms.algos.iceberg import plan_iceberg
from app.oms.algos.twap import plan_twap
from app.oms.algos.vwap import plan_vwap

# 与 executor 参数边界一致
MIN_SLICES = 1
MAX_SLICES = 100
MAX_ICEBERG_SLICES = 100


def _delays_non_decreasing(slices) -> bool:
    return all(
        slices[i].delay_seconds <= slices[i + 1].delay_seconds
        for i in range(len(slices) - 1)
    )


# ── Iceberg ────────────────────────────────────────────────────

class TestIceberg:
    def test_huge_total_caps_slice_count(self) -> None:
        # Arrange: total 巨大、display 极小 → 天然切片数远超上限
        total, display = 1_000_000, 1

        # Act
        slices = plan_iceberg(total, display, duration_seconds=300.0)

        # Assert: 切片数被压回 <= 100，总量守恒
        assert len(slices) <= MAX_ICEBERG_SLICES
        assert sum(s.qty for s in slices) == total

    def test_slices_sum_and_delays_increase(self) -> None:
        # Arrange
        total, display = 100, 30

        # Act
        slices = plan_iceberg(total, display, duration_seconds=300.0)

        # Assert: 4 片（30/30/30/10）、量守恒、延迟递增
        assert len(slices) == 4
        assert [s.qty for s in slices] == [30, 30, 30, 10]
        assert sum(s.qty for s in slices) == total
        assert _delays_non_decreasing(slices)

    def test_invalid_display_falls_back_to_single_slice(self) -> None:
        # Arrange / Act: display >= total → 一次性整单
        slices = plan_iceberg(100, 100, duration_seconds=300.0)

        # Assert
        assert len(slices) == 1
        assert slices[0].qty == 100
        assert slices[0].delay_seconds == pytest.approx(0.0)

    def test_first_slice_starts_immediately(self) -> None:
        slices = plan_iceberg(500, 100, duration_seconds=600.0)
        assert slices[0].delay_seconds == pytest.approx(0.0)


# ── TWAP ───────────────────────────────────────────────────────

class TestTwap:
    def test_slice_count_within_bounds_and_sum_conserved(self) -> None:
        # Arrange
        total, slice_count = 1000, 6

        # Act
        slices = plan_twap(total, duration_seconds=300.0, slice_count=slice_count)

        # Assert: 切片数落在 [MIN,MAX]、总量守恒
        assert MIN_SLICES <= len(slices) <= MAX_SLICES
        assert len(slices) == slice_count
        assert sum(s.qty for s in slices) == total

    def test_equal_split_delays_are_evenly_spaced(self) -> None:
        # Arrange
        total, slice_count, duration = 1000, 5, 300.0

        # Act
        slices = plan_twap(total, duration, slice_count)

        # Assert: 每片 200 股、延迟等间隔递增（0,60,120,...）
        assert [s.qty for s in slices] == [200, 200, 200, 200, 200]
        gaps = [
            round(slices[i + 1].delay_seconds - slices[i].delay_seconds, 6)
            for i in range(len(slices) - 1)
        ]
        assert all(g == pytest.approx(duration / slice_count) for g in gaps)
        assert _delays_non_decreasing(slices)

    def test_remainder_distributed_to_leading_slices(self) -> None:
        # Arrange: 10 股拆 3 片 → 4/3/3（最大余数法）
        slices = plan_twap(10, duration_seconds=300.0, slice_count=3)

        # Assert: 总量守恒、零头补到前片
        assert sum(s.qty for s in slices) == 10
        assert slices[0].qty == 4

    def test_zero_qty_slices_dropped(self) -> None:
        # Arrange: total < slice_count → 部分片为 0 被剔除
        slices = plan_twap(3, duration_seconds=300.0, slice_count=10)

        # Assert: 保留 3 片、总量守恒、index 连续
        assert sum(s.qty for s in slices) == 3
        assert all(s.qty > 0 for s in slices)
        assert [s.index for s in slices] == list(range(len(slices)))


# ── VWAP ───────────────────────────────────────────────────────

class TestVwap:
    def test_sum_conserved_and_delays_increase(self) -> None:
        # Arrange
        total, slice_count = 1000, 6

        # Act
        slices = plan_vwap(total, duration_seconds=300.0, slice_count=slice_count)

        # Assert
        assert MIN_SLICES <= len(slices) <= MAX_SLICES
        assert sum(s.qty for s in slices) == total
        assert _delays_non_decreasing(slices)

    def test_u_shaped_allocation_edges_exceed_middle(self) -> None:
        # Arrange
        total, slice_count = 1000, 6

        # Act
        slices = plan_vwap(total, duration_seconds=300.0, slice_count=slice_count)
        qtys = [s.qty for s in slices]

        # Assert: 端点分配 > 盘中（U 型量能曲线）
        middle_min = min(qtys[1:-1])
        assert qtys[0] > middle_min
        assert qtys[-1] > middle_min


# ── 数量分配工具 ───────────────────────────────────────────────

class TestDistributeQty:
    def test_equal_weights_conserve_total(self) -> None:
        result = distribute_qty(100, [1.0, 1.0, 1.0, 1.0])
        assert sum(result) == 100
        assert all(q >= 0 for q in result)

    def test_largest_remainder_method(self) -> None:
        # 7 股 3 等分 → 3/2/2（零头给小数部分最大者）
        result = distribute_qty(7, [1.0, 1.0, 1.0])
        assert sum(result) == 7
        assert sorted(result, reverse=True) == [3, 2, 2]

    def test_weighted_split_respects_ratios(self) -> None:
        # 权重 3:1 → 大约 75 / 25
        result = distribute_qty(100, [3.0, 1.0])
        assert sum(result) == 100
        assert result[0] > result[1]

    def test_invalid_weights_fall_back_to_equal(self) -> None:
        # 含负权重 → 退化为等分
        result = distribute_qty(100, [1.0, -1.0, 1.0, 1.0])
        assert sum(result) == 100
        # 全零权重 → 退化为等分
        zero = distribute_qty(100, [0.0, 0.0])
        assert sum(zero) == 100
        assert zero == [50, 50]

    def test_empty_or_nonpositive_total_returns_empty(self) -> None:
        assert distribute_qty(0, [1.0, 1.0]) == []
        assert distribute_qty(100, []) == []

    def test_u_shaped_weights_are_symmetric(self) -> None:
        weights = u_shaped_weights(5)
        assert len(weights) == 5
        # 端点权重相等且大于中点
        assert weights[0] == pytest.approx(weights[-1])
        assert weights[0] > weights[len(weights) // 2]
