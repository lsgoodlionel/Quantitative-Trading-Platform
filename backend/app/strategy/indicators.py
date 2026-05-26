"""
技术指标工具函数

封装 pandas-ta 和 pandas 内置计算，返回 Series 或 float。
策略在 on_bar() 中调用这些函数，传入 ctx.history DataFrame。

pandas-ta 文档: https://github.com/twopirllc/pandas-ta
"""

from __future__ import annotations

import pandas as pd


def _close(df: pd.DataFrame) -> pd.Series:
    return df["close"]


def _high(df: pd.DataFrame) -> pd.Series:
    return df["high"]


def _low(df: pd.DataFrame) -> pd.Series:
    return df["low"]


def _volume(df: pd.DataFrame) -> pd.Series:
    return df["volume"]


# ── 移动均线 ──────────────────────────────────────────────────

def sma(df: pd.DataFrame, period: int) -> pd.Series:
    return _close(df).rolling(period).mean()


def ema(df: pd.DataFrame, period: int) -> pd.Series:
    return _close(df).ewm(span=period, adjust=False).mean()


def wma(df: pd.DataFrame, period: int) -> pd.Series:
    weights = pd.Series(range(1, period + 1), dtype=float)
    def _wma(s: pd.Series) -> float:
        return (s * weights).sum() / weights.sum()
    return _close(df).rolling(period).apply(_wma, raw=True)


# ── 震荡指标 ──────────────────────────────────────────────────

def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = _close(df).diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    # 当 loss=0 且 gain>0 时（全涨），RSI=100；loss=0 且 gain=0 时返回 NaN
    rs = gain / loss.where(loss > 0, other=float("nan"))
    result = 100 - (100 / (1 + rs))
    # 纯上涨序列 loss=0 → RSI 应为 100
    result = result.where(loss > 0, other=100.0)
    return result


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """返回 (macd_line, signal_line, histogram)。"""
    close = _close(df)
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """返回 (%K, %D)。"""
    low_min = _low(df).rolling(k_period).min()
    high_max = _high(df).rolling(k_period).max()
    k = 100 * (_close(df) - low_min) / (high_max - low_min).replace(0, float("nan"))
    d = k.rolling(d_period).mean()
    return k, d


# ── 波动率指标 ─────────────────────────────────────────────────

def bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """返回 (upper, mid, lower)。"""
    mid = sma(df, period)
    std = _close(df).rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    close_prev = _close(df).shift(1)
    tr = pd.concat(
        [
            _high(df) - _low(df),
            (_high(df) - close_prev).abs(),
            (_low(df) - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


# ── 趋势指标 ──────────────────────────────────────────────────

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """平均趋向指标（ADX），仅返回 ADX 主线。"""
    high, low, close = _high(df), _low(df), _close(df)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    dm_pos = (high - prev_high).clip(lower=0).where(high - prev_high > prev_low - low, 0)
    dm_neg = (prev_low - low).clip(lower=0).where(prev_low - low > high - prev_high, 0)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    atr_val = tr.rolling(period).mean()
    di_pos = 100 * dm_pos.rolling(period).mean() / atr_val.replace(0, float("nan"))
    di_neg = 100 * dm_neg.rolling(period).mean() / atr_val.replace(0, float("nan"))
    dx = 100 * (di_pos - di_neg).abs() / (di_pos + di_neg).replace(0, float("nan"))
    return dx.rolling(period).mean()


# ── 量价指标 ──────────────────────────────────────────────────

def obv(df: pd.DataFrame) -> pd.Series:
    """能量潮（On-Balance Volume）。"""
    direction = _close(df).diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * _volume(df)).cumsum()


def vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """成交量加权平均价（滚动 VWAP）。"""
    typical_price = (_high(df) + _low(df) + _close(df)) / 3
    vol = _volume(df)
    return (typical_price * vol).rolling(period).sum() / vol.rolling(period).sum()


# ── 工具函数 ──────────────────────────────────────────────────

def crossover(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """series_a 从下穿越 series_b（金叉）时为 True。"""
    prev_below = series_a.shift(1) < series_b.shift(1)
    now_above = series_a >= series_b
    return prev_below & now_above


def crossunder(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """series_a 从上穿越 series_b（死叉）时为 True。"""
    prev_above = series_a.shift(1) > series_b.shift(1)
    now_below = series_a <= series_b
    return prev_above & now_below


def highest(df: pd.DataFrame, period: int) -> pd.Series:
    return _high(df).rolling(period).max()


def lowest(df: pd.DataFrame, period: int) -> pd.Series:
    return _low(df).rolling(period).min()
