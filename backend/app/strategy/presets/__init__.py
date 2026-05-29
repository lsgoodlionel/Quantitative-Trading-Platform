# ── 趋势跟踪类 ──────────────────────────────────────────────────
from app.strategy.presets.double_ma import DoubleMaStrategy
from app.strategy.presets.triple_ma import TripleMaStrategy
from app.strategy.presets.macd import MacdStrategy
from app.strategy.presets.supertrend import SupertrendStrategy
from app.strategy.presets.adx_trend import AdxTrendStrategy

# ── 均值回归类 ──────────────────────────────────────────────────
from app.strategy.presets.bollinger import BollingerStrategy
from app.strategy.presets.rsi_mean_reversion import RsiMeanReversionStrategy
from app.strategy.presets.stochastic import StochasticStrategy
from app.strategy.presets.vwap_reversion import VwapReversionStrategy

# ── 突破类 ──────────────────────────────────────────────────────
from app.strategy.presets.donchian_breakout import DonchianBreakoutStrategy
from app.strategy.presets.keltner_breakout import KeltnerBreakoutStrategy
from app.strategy.presets.atr_breakout import AtrBreakoutStrategy

# ── 动量类 ──────────────────────────────────────────────────────
from app.strategy.presets.momentum import MomentumStrategy

# ── 复合/高级类 ─────────────────────────────────────────────────
from app.strategy.presets.multi_factor import MultiFactorStrategy
from app.strategy.presets.grid_trading import GridTradingStrategy
from app.strategy.presets.pairs_trading import PairsTradingStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    # 趋势跟踪
    "double_ma":         DoubleMaStrategy,
    "triple_ma":         TripleMaStrategy,
    "macd":              MacdStrategy,
    "supertrend":        SupertrendStrategy,
    "adx_trend":         AdxTrendStrategy,
    # 均值回归
    "bollinger":         BollingerStrategy,
    "rsi_mean_reversion": RsiMeanReversionStrategy,
    "stochastic":        StochasticStrategy,
    "vwap_reversion":    VwapReversionStrategy,
    # 突破
    "donchian_breakout": DonchianBreakoutStrategy,
    "keltner_breakout":  KeltnerBreakoutStrategy,
    "atr_breakout":      AtrBreakoutStrategy,
    # 动量
    "momentum":          MomentumStrategy,
    # 复合/高级
    "multi_factor":      MultiFactorStrategy,
    "grid_trading":      GridTradingStrategy,
    "pairs_trading":     PairsTradingStrategy,
}

__all__ = [
    "DoubleMaStrategy",
    "TripleMaStrategy",
    "MacdStrategy",
    "SupertrendStrategy",
    "AdxTrendStrategy",
    "BollingerStrategy",
    "RsiMeanReversionStrategy",
    "StochasticStrategy",
    "VwapReversionStrategy",
    "DonchianBreakoutStrategy",
    "KeltnerBreakoutStrategy",
    "AtrBreakoutStrategy",
    "MomentumStrategy",
    "MultiFactorStrategy",
    "GridTradingStrategy",
    "PairsTradingStrategy",
    "STRATEGY_REGISTRY",
]
