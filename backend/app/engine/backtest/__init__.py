from app.engine.backtest.broker import Fill, Order, OrderStatus, SimulatedBroker
from app.engine.backtest.capacity import (
    CapacityEstimate,
    estimate_capacity,
    leverage_series,
    turnover_series,
)
from app.engine.backtest.commission import CommissionModel, get_commission_model
from app.engine.backtest.crisis import (
    CRISIS_WINDOWS,
    CrisisWindow,
    crisis_performance,
)
from app.engine.backtest.daily_result import (
    ContractDailyResult,
    PortfolioDailyResult,
)
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.order_types import OrderType, TimeInForce
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from app.engine.backtest.position import PortfolioPositions, Position
from app.engine.backtest.reject_reasons import (
    classify_reject_reason,
    rejected_signal_summary,
)
from app.engine.backtest.slippage import SlippageModel, get_slippage_model
from app.engine.backtest.tag_metrics import cross_tag_metrics

__all__ = [
    "CommissionModel",
    "get_commission_model",
    "SlippageModel",
    "get_slippage_model",
    "Position",
    "PortfolioPositions",
    "SimulatedBroker",
    "Order",
    "Fill",
    "OrderStatus",
    "OrderType",
    "TimeInForce",
    "BacktestEngine",
    "BacktestConfig",
    # ── K1 组合回测 ──────────────────────────────────────────
    "PortfolioBroker",
    "PortfolioBacktestEngine",
    "PortfolioBacktestConfig",
    "PortfolioBacktestResult",
    "ContractDailyResult",
    "PortfolioDailyResult",
    # ── N-a 容量/危机/拒绝信号 ───────────────────────────────
    "CapacityEstimate",
    "estimate_capacity",
    "turnover_series",
    "leverage_series",
    "CrisisWindow",
    "CRISIS_WINDOWS",
    "crisis_performance",
    "cross_tag_metrics",
    "classify_reject_reason",
    "rejected_signal_summary",
]
