"""声明式因子库（loader.py）单元测试

覆盖：
- generate_factor_library 按 groups/windows 生成的因子数与命名正确
- 窗口/上限校验抛清晰 ValueError
- 空过滤集经 build_feature_fn 抛 ValueError
- build_feature_fn 对正常 specs 产出多列因子帧
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_lib.loader import (
    MAX_FACTORS,
    build_feature_fn,
    generate_factor_library,
    library_group_meta,
)

# 家族数量（_FAMILIES 长度）与 KBAR 数量，用于推导预期因子数
N_FAMILIES = 24
N_KBAR = 6


def _make_ohlcv(n_days: int = 80, seed: int = 42) -> pd.DataFrame:
    """生成单标的 OHLCV 帧（index 为时间字符串，列含 OHLCV）。"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, n_days)
    close = 100.0 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_days)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_days)))
    open_ = close * (1 + rng.normal(0, 0.003, n_days))
    volume = rng.integers(1_000_000, 5_000_000, n_days).astype(float)
    index = [f"2024-01-{i + 1:02d}" for i in range(n_days)]
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


class TestGenerateFactorLibrary:
    def test_default_generation_count(self) -> None:
        # Arrange / Act：默认 5 个窗口 + 全部家族 + KBAR
        specs = generate_factor_library()

        # Assert
        assert len(specs) == N_KBAR + N_FAMILIES * 5

    def test_windows_scale_family_count(self) -> None:
        # Arrange / Act：单动量家族 × 2 窗口，无 KBAR（K线 不在 groups）
        specs = generate_factor_library(windows=(5, 10), groups=("动量",))

        # Assert
        names = {s.name for s in specs}
        assert names == {"ROC5", "ROC10"}
        assert all(s.group == "动量" for s in specs)

    def test_kbar_group_yields_zero_window_specs(self) -> None:
        # Arrange / Act
        specs = generate_factor_library(groups=("K线",))

        # Assert：KBAR 为无窗口形态因子
        assert len(specs) == N_KBAR
        assert all(s.window == 0 for s in specs)
        assert {s.name for s in specs} == {"KMID", "KLEN", "KMID2", "KUP", "KLOW", "KSFT"}

    def test_spec_meta_reflects_window(self) -> None:
        # Arrange
        specs = generate_factor_library(windows=(20,), groups=("波动",))

        # Act
        meta = specs[0].to_meta()

        # Assert：表达式模板已填充窗口
        assert meta["window"] == 20
        assert "{w}" not in meta["expr"]
        assert "20" in meta["expr"]

    def test_invalid_window_raises_value_error(self) -> None:
        # Arrange / Act / Assert：窗口 < 2 快速失败
        with pytest.raises(ValueError, match="窗口"):
            generate_factor_library(windows=(1, 5))

    def test_exceeding_max_factors_raises(self) -> None:
        # Arrange：10 个窗口 → 24×10 + 6 = 246 > 240 上限
        many_windows = tuple(range(2, 12))

        # Act / Assert
        with pytest.raises(ValueError, match=str(MAX_FACTORS)):
            generate_factor_library(windows=many_windows)

    def test_unmatched_group_returns_empty(self) -> None:
        # Arrange / Act：不存在的分组 → 无因子
        specs = generate_factor_library(groups=("不存在的分组",))

        # Assert
        assert specs == []


class TestBuildFeatureFn:
    def test_empty_specs_raises_clear_error(self) -> None:
        # Arrange：空过滤集
        specs = generate_factor_library(groups=("不存在的分组",))

        # Act / Assert
        with pytest.raises(ValueError, match="因子列表为空"):
            build_feature_fn(specs)

    def test_produces_multi_column_frame(self) -> None:
        # Arrange
        specs = generate_factor_library(windows=(5, 10), groups=("动量", "均线"))
        feature_fn = build_feature_fn(specs)
        ohlcv = _make_ohlcv()

        # Act
        frame = feature_fn(ohlcv)

        # Assert：列名与 spec 对齐、行数不变、无 inf
        assert list(frame.columns) == [s.name for s in specs]
        assert frame.shape == (len(ohlcv), len(specs))
        assert not np.isinf(frame.to_numpy()).any()

    def test_feature_values_are_float(self) -> None:
        # Arrange
        specs = generate_factor_library(groups=("K线",))
        feature_fn = build_feature_fn(specs)
        ohlcv = _make_ohlcv()

        # Act
        frame = feature_fn(ohlcv)

        # Assert
        assert all(dtype == np.float64 for dtype in frame.dtypes)

    def test_index_preserved(self) -> None:
        # Arrange
        specs = generate_factor_library(windows=(5,), groups=("波动",))
        feature_fn = build_feature_fn(specs)
        ohlcv = _make_ohlcv()

        # Act
        frame = feature_fn(ohlcv)

        # Assert
        assert list(frame.index) == list(ohlcv.index)


class TestLibraryGroupMeta:
    def test_group_counts_sum_to_total(self) -> None:
        # Arrange
        specs = generate_factor_library()

        # Act
        meta = library_group_meta(specs)

        # Assert
        assert sum(g["count"] for g in meta) == len(specs)

    def test_group_meta_preserves_insertion_order(self) -> None:
        # Arrange：K线 先于其余家族生成
        specs = generate_factor_library(windows=(5,), groups=("K线", "动量"))

        # Act
        meta = library_group_meta(specs)

        # Assert
        assert [g["name"] for g in meta] == ["K线", "动量"]
