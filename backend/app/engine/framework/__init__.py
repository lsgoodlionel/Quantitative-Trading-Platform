"""Alpha → Insight → PortfolioTarget → Execution 三段式框架（Wave K-d / K5）

结构参考 Lean 的 `Algorithm.Framework`（Apache-2.0）：

    AlphaModel                 行情 → Insight（方向 + 置信度 + 建议权重）
    PortfolioConstructionModel Insight → PortfolioTarget（目标持仓）
    RiskManagementModel        目标的最后一道闸门
    ExecutionModel             目标 → 增量订单

它同时解决 V3 记录的两条断链：因子挖掘产出无法交易、组合优化结果无法下单。
"""

from app.engine.framework.alpha import AlphaModel, LegacyStrategyAlphaAdapter
from app.engine.framework.execution import (
    ExecutionModel,
    ImmediateExecutionModel,
    SpreadExecution,
    StandardDeviationExecution,
    VolumeWeightedAveragePriceExecution,
)
from app.engine.framework.factor_alpha import (
    FormulaFactorAlphaModel,
    LibraryFactorAlphaModel,
    PanelLibraryFactorAlphaModel,
)
from app.engine.framework.insight import (
    NEVER_EXPIRES,
    Insight,
    InsightDirection,
    group_insights,
)
from app.engine.framework.optimizer_pcm import OptimizerPCM
from app.engine.framework.portfolio_construction import (
    EqualWeightingPCM,
    InsightWeightingPCM,
    PortfolioConstructionModel,
)
from app.engine.framework.risk import (
    MaximumDrawdownPerSecurity,
    MaximumDrawdownPortfolio,
    MaximumSectorExposure,
    MaximumUnrealizedProfitPerSecurity,
    NullRiskModel,
    RiskManagementModel,
    TrailingStopRiskManagement,
)
from app.engine.framework.strategy import FrameworkStrategy
from app.engine.framework.target import PortfolioTarget

__all__ = [
    "NEVER_EXPIRES",
    "AlphaModel",
    "EqualWeightingPCM",
    "ExecutionModel",
    "FormulaFactorAlphaModel",
    "LibraryFactorAlphaModel",
    "PanelLibraryFactorAlphaModel",
    "FrameworkStrategy",
    "ImmediateExecutionModel",
    "Insight",
    "InsightDirection",
    "InsightWeightingPCM",
    "LegacyStrategyAlphaAdapter",
    "MaximumDrawdownPerSecurity",
    "MaximumDrawdownPortfolio",
    "MaximumSectorExposure",
    "MaximumUnrealizedProfitPerSecurity",
    "NullRiskModel",
    "OptimizerPCM",
    "PortfolioConstructionModel",
    "PortfolioTarget",
    "RiskManagementModel",
    "SpreadExecution",
    "StandardDeviationExecution",
    "TrailingStopRiskManagement",
    "VolumeWeightedAveragePriceExecution",
    "group_insights",
]
