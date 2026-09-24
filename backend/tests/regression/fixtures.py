"""回归基线的确定性数据与用例枚举。

设计约束（保证快照可复现）：
- 不触网、不读数据库：价格由固定种子的 numpy PCG64 生成。
- 不使用 `random` 全局状态：避免被其他测试污染。
- 生成的 OHLC 严格满足 high >= max(open, close) 且 low <= min(open, close)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from app.data.models import Bar, Frequency, Market

# ── 常量 ──────────────────────────────────────────────────────────

BASE_SEED = 20260807          # 固定种子：改动它等于作废整份基线
N_BARS = 250                  # 约一年日线，足够 60 窗口策略预热后产生信号
INITIAL_CASH = 100_000.0
START_TIME = datetime(2024, 1, 2, tzinfo=UTC)

# 每个市场用符合该市场惯例的代码，以走到各自的手续费/滑点/T+1 分支
MARKET_SYMBOLS: dict[Market, str] = {
    Market.US: "AAPL",
    Market.HK: "00700",
    Market.A: "600519",
}


@dataclass(frozen=True)
class Regime:
    """价格情景：漂移 + 波动率，覆盖趋势型与震荡型策略的不同表现区。"""

    name: str
    drift: float        # 每 bar 对数漂移
    vol: float          # 每 bar 对数波动率


REGIMES: tuple[Regime, ...] = (
    Regime("uptrend", drift=0.0020, vol=0.012),
    Regime("choppy", drift=0.0000, vol=0.018),
    Regime("downtrend", drift=-0.0015, vol=0.014),
)


# ── 价格生成 ──────────────────────────────────────────────────────

# 市场 → 固定偏移量。**不可用 `hash()`**：CPython 对 str 的哈希按进程随机化
# （PYTHONHASHSEED），会让种子在每次运行时改变，基线永远对不上。
_MARKET_OFFSET: dict[Market, int] = {
    Market.US: 0,
    Market.HK: 1,
    Market.A: 2,
}


def _regime_seed(market: Market, regime: Regime) -> int:
    """由市场与情景派生子种子，保证各用例数据互不相同但跨进程可复现。"""
    return BASE_SEED + _MARKET_OFFSET[market] * 17 + REGIMES.index(regime) * 101


def make_bars(market: Market, regime: Regime, n: int = N_BARS) -> list[Bar]:
    """生成确定性的日线序列（几何布朗运动 + 日内高低点）。"""
    rng = np.random.Generator(np.random.PCG64(_regime_seed(market, regime)))

    log_returns = regime.drift + regime.vol * rng.standard_normal(n)
    closes = 100.0 * np.exp(np.cumsum(log_returns))

    # 开盘价 = 上一根收盘价叠加隔夜跳空；首根以 100 为基准
    gaps = 1.0 + 0.25 * regime.vol * rng.standard_normal(n)
    opens = np.empty(n)
    opens[0] = 100.0
    opens[1:] = closes[:-1] * gaps[1:]

    # 日内振幅：在 max(o,c)/min(o,c) 之外再各自扩展一段非负幅度
    up_ext = np.abs(rng.standard_normal(n)) * regime.vol
    dn_ext = np.abs(rng.standard_normal(n)) * regime.vol
    highs = np.maximum(opens, closes) * (1.0 + up_ext)
    lows = np.minimum(opens, closes) * (1.0 - dn_ext)

    volumes = (1_000_000 * (1.0 + 0.3 * rng.random(n))).astype(np.int64)

    symbol = MARKET_SYMBOLS[market]
    return [
        Bar(
            time=START_TIME + timedelta(days=i),
            symbol=symbol,
            market=market,
            frequency=Frequency.DAY_1,
            open=float(opens[i]),
            high=float(highs[i]),
            low=float(lows[i]),
            close=float(closes[i]),
            volume=int(volumes[i]),
            vwap=float((highs[i] + lows[i] + closes[i]) / 3.0),
        )
        for i in range(n)
    ]


# ── 用例枚举 ──────────────────────────────────────────────────────

def case_id(strategy_name: str, market: Market, regime: Regime) -> str:
    return f"{strategy_name}|{market.value}|{regime.name}"


def iter_cases() -> list[tuple[str, Market, Regime]]:
    """全部回归用例：注册表内每个策略 × 3 市场 × 3 情景。"""
    from app.strategy.presets import STRATEGY_REGISTRY

    return [
        (name, market, regime)
        for name in sorted(STRATEGY_REGISTRY)
        for market in (Market.US, Market.HK, Market.A)
        for regime in REGIMES
    ]
