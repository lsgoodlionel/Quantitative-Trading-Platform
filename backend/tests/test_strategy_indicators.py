"""技术指标单元测试"""

from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from app.strategy.indicators import (
    sma, ema, rsi, macd, bollinger_bands, atr,
    crossover, crossunder, obv, vwap,
)


def _make_df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "open": [c * 0.99 for c in closes],
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.98 for c in closes],
        "close": closes,
        "volume": [10_000] * n,
    })


class TestSma:
    def test_sma_length(self) -> None:
        df = _make_df(list(range(1, 21)))
        result = sma(df, 5)
        assert len(result) == 20

    def test_sma_value(self) -> None:
        df = _make_df([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(df, 3)
        assert abs(result.iloc[-1] - 4.0) < 1e-9

    def test_sma_nan_for_insufficient_data(self) -> None:
        df = _make_df([1.0, 2.0])
        result = sma(df, 5)
        assert result.isna().all()


class TestEma:
    def test_ema_length(self) -> None:
        df = _make_df(list(range(1, 21)))
        result = ema(df, 5)
        assert len(result) == 20

    def test_ema_is_weighted_recent(self) -> None:
        # EMA 对近期数据赋予更高权重，所以上升序列中 EMA < 最新值
        closes = list(range(1, 21))  # 递增
        df = _make_df(closes)
        result = ema(df, 5)
        # 最后 EMA 应 < 最新收盘价（因为有历史低值拖累）
        assert result.iloc[-1] < closes[-1]


class TestRsi:
    def test_rsi_bounds(self) -> None:
        closes = [100.0 + i * 0.5 for i in range(30)]
        df = _make_df(closes)
        result = rsi(df, 14).dropna()
        assert (result >= 0).all() and (result <= 100).all()

    def test_rsi_high_on_uptrend(self) -> None:
        closes = [100.0 + i for i in range(30)]
        df = _make_df(closes)
        result = rsi(df, 14).dropna()
        assert result.iloc[-1] > 70

    def test_rsi_low_on_downtrend(self) -> None:
        closes = [100.0 - i for i in range(30)]
        df = _make_df(closes)
        result = rsi(df, 14).dropna()
        assert result.iloc[-1] < 30


class TestMacd:
    def test_macd_returns_three_series(self) -> None:
        closes = [100.0 + i * 0.1 for i in range(50)]
        df = _make_df(closes)
        macd_line, signal_line, histogram = macd(df, 12, 26, 9)
        assert len(macd_line) == len(closes)
        assert len(signal_line) == len(closes)
        assert len(histogram) == len(closes)

    def test_histogram_equals_macd_minus_signal(self) -> None:
        closes = [100.0 + i * 0.1 for i in range(50)]
        df = _make_df(closes)
        macd_line, signal_line, histogram = macd(df, 12, 26, 9)
        diff = (macd_line - signal_line - histogram).dropna()
        assert (diff.abs() < 1e-9).all()


class TestBollingerBands:
    def test_upper_above_mid_above_lower(self) -> None:
        closes = [100.0 + i * 0.1 for i in range(40)]
        df = _make_df(closes)
        upper, mid, lower = bollinger_bands(df, 20, 2.0)
        valid = ~(upper.isna() | mid.isna() | lower.isna())
        assert (upper[valid] >= mid[valid]).all()
        assert (mid[valid] >= lower[valid]).all()

    def test_band_width_scales_with_std_dev(self) -> None:
        closes = [100.0 + (i % 5) * 1.0 for i in range(40)]
        df = _make_df(closes)
        upper1, mid1, lower1 = bollinger_bands(df, 20, 1.0)
        upper2, mid2, lower2 = bollinger_bands(df, 20, 2.0)
        # std_dev=2 的带宽应该是 std_dev=1 的 2 倍
        width1 = (upper1 - lower1).dropna().iloc[-1]
        width2 = (upper2 - lower2).dropna().iloc[-1]
        assert abs(width2 / width1 - 2.0) < 1e-6


class TestCrossover:
    def test_crossover_detected(self) -> None:
        # a 从 1.5 涨到 3.0 穿越 b=2.0（crossover 发生在最后一根）
        a = pd.Series([1.0, 1.5, 3.0])
        b = pd.Series([2.0, 2.0, 2.0])
        result = crossover(a, b)
        assert result.iloc[-1]

    def test_no_crossover_when_always_above(self) -> None:
        a = pd.Series([3.0, 4.0, 5.0])
        b = pd.Series([1.0, 1.0, 1.0])
        result = crossover(a, b)
        assert not result.iloc[-1]

    def test_crossunder_detected(self) -> None:
        # a 从 2.5 跌到 1.0 穿越 b=2.0（crossunder 发生在最后一根）
        a = pd.Series([3.0, 2.5, 1.0])
        b = pd.Series([2.0, 2.0, 2.0])
        result = crossunder(a, b)
        assert result.iloc[-1]


class TestObv:
    def test_obv_increases_on_up_days(self) -> None:
        closes = [100.0, 101.0, 102.0, 103.0]
        volumes = [1000, 1000, 1000, 1000]
        df = pd.DataFrame({
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": volumes,
        })
        result = obv(df)
        assert result.iloc[-1] > result.iloc[0]
