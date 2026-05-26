from app.strategy.presets.double_ma import DoubleMaStrategy
from app.strategy.presets.bollinger import BollingerStrategy
from app.strategy.presets.macd import MacdStrategy
from app.strategy.presets.rsi_mean_reversion import RsiMeanReversionStrategy
from app.strategy.presets.momentum import MomentumStrategy
from app.strategy.presets.grid_trading import GridTradingStrategy
from app.strategy.presets.pairs_trading import PairsTradingStrategy
from app.strategy.presets.multi_factor import MultiFactorStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "double_ma": DoubleMaStrategy,
    "bollinger": BollingerStrategy,
    "macd": MacdStrategy,
    "rsi_mean_reversion": RsiMeanReversionStrategy,
    "momentum": MomentumStrategy,
    "grid_trading": GridTradingStrategy,
    "pairs_trading": PairsTradingStrategy,
    "multi_factor": MultiFactorStrategy,
}

__all__ = [
    "DoubleMaStrategy",
    "BollingerStrategy",
    "MacdStrategy",
    "RsiMeanReversionStrategy",
    "MomentumStrategy",
    "GridTradingStrategy",
    "PairsTradingStrategy",
    "MultiFactorStrategy",
    "STRATEGY_REGISTRY",
]
