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


# ── 动量指标 ──────────────────────────────────────────────────

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """商品通道指数（CCI）。"""
    typical = (_high(df) + _low(df) + _close(df)) / 3
    mean_dev = typical.rolling(period).apply(
        lambda x: (x - x.mean()).abs().mean(), raw=True
    )
    return (typical - typical.rolling(period).mean()) / (0.015 * mean_dev)


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """威廉斯 %R。"""
    high_max = _high(df).rolling(period).max()
    low_min = _low(df).rolling(period).min()
    return -100 * (high_max - _close(df)) / (high_max - low_min).replace(0, float("nan"))


def roc(df: pd.DataFrame, period: int = 12) -> pd.Series:
    """变化率（Rate of Change）。"""
    close = _close(df)
    return (close - close.shift(period)) / close.shift(period).replace(0, float("nan")) * 100


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """资金流量指数（Money Flow Index）。"""
    typical = (_high(df) + _low(df) + _close(df)) / 3
    raw_mf = typical * _volume(df)
    direction = typical.diff().apply(lambda x: 1 if x > 0 else -1)
    positive_mf = raw_mf.where(direction > 0, 0).rolling(period).sum()
    negative_mf = raw_mf.where(direction < 0, 0).rolling(period).sum()
    mfr = positive_mf / negative_mf.replace(0, float("nan"))
    return 100 - (100 / (1 + mfr))


def tsi(df: pd.DataFrame, r: int = 25, s: int = 13) -> pd.Series:
    """真实强度指数（True Strength Index）。"""
    close = _close(df)
    m = close.diff()
    smooth_m = m.ewm(span=r, adjust=False).mean().ewm(span=s, adjust=False).mean()
    smooth_abs_m = m.abs().ewm(span=r, adjust=False).mean().ewm(span=s, adjust=False).mean()
    return 100 * smooth_m / smooth_abs_m.replace(0, float("nan"))


# ── 趋势增强 ──────────────────────────────────────────────────

def aroon(
    df: pd.DataFrame,
    period: int = 25,
) -> tuple[pd.Series, pd.Series]:
    """Aroon 指标。返回 (aroon_up, aroon_down)。"""
    aroon_up = _high(df).rolling(period + 1).apply(
        lambda x: ((period - x[::-1].argmax()) / period) * 100, raw=True
    )
    aroon_down = _low(df).rolling(period + 1).apply(
        lambda x: ((period - x[::-1].argmin()) / period) * 100, raw=True
    )
    return aroon_up, aroon_down


def donchian_channels(
    df: pd.DataFrame,
    period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """唐奇安通道。返回 (upper, mid, lower)。"""
    upper = _high(df).rolling(period).max()
    lower = _low(df).rolling(period).min()
    mid = (upper + lower) / 2
    return upper, mid, lower


def keltner_channels(
    df: pd.DataFrame,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """凯尔特纳通道。返回 (upper, mid, lower)。"""
    mid = ema(df, period)
    atr_val = atr(df, atr_period)
    upper = mid + multiplier * atr_val
    lower = mid - multiplier * atr_val
    return upper, mid, lower


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Supertrend 指标。
    返回 (supertrend_line, direction)，direction=1 上升趋势，=-1 下降趋势。

    使用 numpy 数组进行迭代（兼容 pandas 3.x Copy-on-Write）。
    """
    import numpy as np

    high = _high(df).to_numpy(dtype=float)
    low = _low(df).to_numpy(dtype=float)
    close = _close(df).to_numpy(dtype=float)
    atr_arr = atr(df, period).to_numpy(dtype=float)

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr_arr
    basic_lower = hl2 - multiplier * atr_arr

    n = len(close)
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    st_line = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)   # default bullish

    for i in range(1, n):
        prev_close = close[i - 1]
        prev_upper = final_upper[i - 1]
        prev_lower = final_lower[i - 1]

        # Ratchet lower band upward only
        final_lower[i] = basic_lower[i] if (
            np.isnan(prev_lower)
            or basic_lower[i] > prev_lower
            or prev_close < prev_lower
        ) else prev_lower

        # Ratchet upper band downward only
        final_upper[i] = basic_upper[i] if (
            np.isnan(prev_upper)
            or basic_upper[i] < prev_upper
            or prev_close > prev_upper
        ) else prev_upper

        prev_dir = direction[i - 1]
        if prev_dir == 1 and close[i] < final_lower[i]:
            direction[i] = -1
        elif prev_dir == -1 and close[i] > final_upper[i]:
            direction[i] = 1
        else:
            direction[i] = prev_dir

        st_line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    idx = df.index
    return pd.Series(st_line, index=idx), pd.Series(direction, index=idx)


def ichimoku(
    df: pd.DataFrame,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
) -> dict[str, pd.Series]:
    """
    一目均衡表。返回字典:
    - tenkan_sen: 转换线
    - kijun_sen: 基准线
    - senkou_span_a: 先行带 A（前移 kijun 期）
    - senkou_span_b: 先行带 B（前移 kijun 期）
    - chikou_span: 迟行带（后移 kijun 期）
    """
    high, low, close = _high(df), _low(df), _close(df)

    tenkan_sen = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    kijun_sen  = (high.rolling(kijun).max()  + low.rolling(kijun).min())  / 2

    senkou_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(kijun)
    chikou  = close.shift(-kijun)

    return {
        "tenkan_sen":    tenkan_sen,
        "kijun_sen":     kijun_sen,
        "senkou_span_a": senkou_a,
        "senkou_span_b": senkou_b,
        "chikou_span":   chikou,
    }


# ── 收益率统计 ────────────────────────────────────────────────

def rolling_sharpe(
    df: pd.DataFrame,
    period: int = 60,
    risk_free: float = 0.0,
    trading_days: int = 252,
) -> pd.Series:
    """滚动夏普比率（年化）。"""
    daily_ret = _close(df).pct_change()
    mean_ret = daily_ret.rolling(period).mean()
    std_ret  = daily_ret.rolling(period).std()
    return (mean_ret - risk_free / trading_days) / std_ret.replace(0, float("nan")) * (trading_days ** 0.5)


def rolling_zscore(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """收盘价的滚动 Z-score（均值回归信号）。"""
    close = _close(df)
    return (close - close.rolling(period).mean()) / close.rolling(period).std().replace(0, float("nan"))
