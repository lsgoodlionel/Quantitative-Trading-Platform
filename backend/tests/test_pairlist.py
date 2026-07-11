"""动态标的池筛选（PairlistService）单元测试（Epic E / E5）

覆盖：
- 单条规则 min/max 阈值过滤：成交量 / 价格 / 波动 / 市值
- 缺失值遇阈值即排除（与 screener 口径一致）
- 市值规则单位换算（亿 → 本币原始单位）
- 排序（asc/desc）+ top N 截断；缺失值排序时沉底
- 规则链有序执行（apply_chain）
- bars 派生指标计算（波动率 / 累计收益 / 价差代理）
- 参数规整与序列化辅助
"""

from __future__ import annotations

import pytest

from app.data.pairlist import (
    PairlistRule,
    PairMetrics,
    _apply_rule,
    _compute_bar_metrics,
    apply_chain,
    clamp_lookback,
    metrics_to_dict,
)


# ── 公用构造器 ─────────────────────────────────────────────────

def _sample_items() -> list[PairMetrics]:
    """4 个合成标的，覆盖不同价格/量/波动/表现，D 含缺失量。"""
    return [
        PairMetrics(
            "A", "US", "a", price=10.0, volume=1000,
            volatility=5.0, performance=2.0, market_cap=5e8,
        ),
        PairMetrics(
            "B", "US", "b", price=50.0, volume=500,
            volatility=20.0, performance=-3.0, market_cap=1e8,
        ),
        PairMetrics(
            "C", "US", "c", price=200.0, volume=2000,
            volatility=8.0, performance=10.0, market_cap=3e8,
        ),
        PairMetrics(
            "D", "US", "d", price=5.0, volume=None,
            volatility=None, performance=1.0, market_cap=None,
        ),
    ]


# ── 单条规则阈值过滤 ───────────────────────────────────────────

class TestThresholdFilter:
    def test_volume_min_excludes_low_and_missing(self) -> None:
        # Arrange
        items = _sample_items()

        # Act: 成交量 >= 800 → 保留 A、C；B(500) 与 D(None) 排除
        kept = _apply_rule(items, PairlistRule(kind="volume", min_value=800))

        # Assert
        assert [it.symbol for it in kept] == ["A", "C"]

    def test_price_max_filter(self) -> None:
        # Arrange
        items = _sample_items()

        # Act: 价格 <= 100 → 排除 C(200)
        kept = _apply_rule(items, PairlistRule(kind="price", max_value=100))

        # Assert
        assert [it.symbol for it in kept] == ["A", "B", "D"]

    def test_volatility_band_filter(self) -> None:
        # Arrange
        items = _sample_items()

        # Act: 波动率 6~15 → 仅 C(8)；A(5) 过低、B(20) 过高、D(None) 排除
        kept = _apply_rule(
            items, PairlistRule(kind="volatility", min_value=6.0, max_value=15.0)
        )

        # Assert
        assert [it.symbol for it in kept] == ["C"]

    def test_missing_value_excluded_when_bound_present(self) -> None:
        # Arrange: 只有下界时，缺失字段应被排除
        items = _sample_items()

        # Act
        kept = _apply_rule(items, PairlistRule(kind="performance", min_value=-100.0))

        # Assert: 全部有 performance 值 → 全保留（D 有 1.0）
        assert len(kept) == 4

    def test_no_bound_keeps_all(self) -> None:
        # Arrange: 无 min/max → 不做阈值过滤（含缺失值也保留）
        items = _sample_items()

        # Act
        kept = _apply_rule(items, PairlistRule(kind="volume"))

        # Assert
        assert len(kept) == 4


# ── 市值单位换算 ───────────────────────────────────────────────

class TestMarketCapConversion:
    def test_min_value_in_yi_converted_to_raw(self) -> None:
        # Arrange: 阈值 3（亿）→ 3e8 本币；保留 A(5e8)、C(3e8)
        items = _sample_items()

        # Act
        kept = _apply_rule(items, PairlistRule(kind="market_cap", min_value=3.0))

        # Assert: B(1e8) 与 D(None) 排除
        assert [it.symbol for it in kept] == ["A", "C"]

    def test_metrics_to_dict_adds_yi_field(self) -> None:
        # Arrange
        item = PairMetrics("X", "US", "x", market_cap=5e8)

        # Act
        d = metrics_to_dict(item)

        # Assert
        assert d["market_cap_yi"] == pytest.approx(5.0)


# ── 排序与截断 ─────────────────────────────────────────────────

class TestSortAndTop:
    def test_sort_desc_and_top_n(self) -> None:
        # Arrange
        items = _sample_items()

        # Act: 按成交量降序取前 2 → C(2000)、A(1000)
        kept = _apply_rule(items, PairlistRule(kind="volume", sort="desc", top=2))

        # Assert
        assert [it.symbol for it in kept] == ["C", "A"]

    def test_sort_desc_pushes_missing_to_end(self) -> None:
        # Arrange: 缺失值降序排序应沉底
        items = [
            PairMetrics("A", "US", "a", performance=None),
            PairMetrics("B", "US", "b", performance=5.0),
        ]

        # Act
        kept = _apply_rule(items, PairlistRule(kind="performance", sort="desc"))

        # Assert
        assert [it.symbol for it in kept] == ["B", "A"]

    def test_sort_asc_orders_ascending(self) -> None:
        # Arrange
        items = _sample_items()

        # Act: 按价格升序 → D(5) A(10) B(50) C(200)
        kept = _apply_rule(items, PairlistRule(kind="price", sort="asc"))

        # Assert
        assert [it.symbol for it in kept] == ["D", "A", "B", "C"]


# ── 规则链有序执行 ─────────────────────────────────────────────

class TestApplyChain:
    def test_chain_filters_sequentially(self) -> None:
        # Arrange: 先价格 <=100，再成交量 >=800
        items = _sample_items()
        rules = [
            PairlistRule(kind="price", max_value=100),
            PairlistRule(kind="volume", min_value=800),
        ]

        # Act
        result = apply_chain(items, rules)

        # Assert: price<=100 → {A,B,D}；再 volume>=800 → 仅 A
        assert [it.symbol for it in result] == ["A"]

    def test_empty_rules_returns_all(self) -> None:
        # Arrange
        items = _sample_items()

        # Act
        result = apply_chain(items, [])

        # Assert: 无规则 → 原样返回（不修改输入）
        assert [it.symbol for it in result] == ["A", "B", "C", "D"]

    def test_apply_chain_does_not_mutate_input(self) -> None:
        # Arrange
        items = _sample_items()
        original = [it.symbol for it in items]

        # Act
        apply_chain(items, [PairlistRule(kind="volume", min_value=1500)])

        # Assert: 输入列表顺序/内容不变（不可变契约）
        assert [it.symbol for it in items] == original


# ── bars 派生指标计算 ──────────────────────────────────────────

class TestBarMetrics:
    def test_rising_series_positive_performance(self) -> None:
        # Arrange: 净上涨序列
        closes = [100, 102, 104, 103, 106]
        highs = [101, 103, 105, 104, 107]
        lows = [99, 101, 103, 102, 105]

        # Act
        metrics = _compute_bar_metrics(closes, highs, lows)

        # Assert: 累计收益 = (106/100 - 1)*100 = 6%；波动/价差为正
        assert metrics["performance"] == pytest.approx(6.0, abs=1e-6)
        assert metrics["volatility"] > 0
        assert metrics["spread_proxy"] > 0

    def test_too_short_series_returns_none(self) -> None:
        # Arrange / Act: 单点序列无法计算
        metrics = _compute_bar_metrics([100], [100], [100])

        # Assert
        assert metrics == {
            "volatility": None,
            "performance": None,
            "spread_proxy": None,
        }

    def test_flat_series_zero_performance(self) -> None:
        # Arrange: 恒定价格 → 零收益、零波动
        metrics = _compute_bar_metrics([100, 100, 100], [100, 100, 100], [100, 100, 100])

        # Assert
        assert metrics["performance"] == pytest.approx(0.0)
        assert metrics["volatility"] == pytest.approx(0.0)


# ── 参数规整 ───────────────────────────────────────────────────

class TestClampLookback:
    def test_none_returns_default(self) -> None:
        assert clamp_lookback(None) == 20

    def test_below_min_clamped(self) -> None:
        assert clamp_lookback(1) == 2

    def test_above_max_clamped(self) -> None:
        assert clamp_lookback(9999) == 120

    def test_within_range_unchanged(self) -> None:
        assert clamp_lookback(45) == 45
