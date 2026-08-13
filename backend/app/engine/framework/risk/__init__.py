"""组合风控模型（Wave K-d 骨架 / Wave L-b 模型）

`RiskManagementModel` 是三段式框架里目标持仓的最后一道闸门：
`pcm.create_targets() → risk.manage_risk() → execution.execute()`。

它**输出修正后的 `PortfolioTarget`，不输出订单**。清仓表达为 `quantity=0`，
由 `ExecutionModel` 算出「目标 - 当前持仓」的 diff 再下单。风控模型里直接调
`ctx.sell()` 会绕过执行模型，也就拿不到拆单、价差等待这些执行层能力。

可用模型
--------
- `NullRiskModel` — 直通（默认）
- `MaximumDrawdownPerSecurity` — 单标的浮亏超阈值 → 该标的清零
- `MaximumDrawdownPortfolio` — 组合净值回撤超阈值 → 全部清零
- `MaximumUnrealizedProfitPerSecurity` — 单标的浮盈超阈值 → 落袋
- `TrailingStopRiskManagement` — 从持仓期内最高浮盈回撤超阈值 → 清零
- `MaximumSectorExposure` — 单行业敞口超阈值 → 按比例缩减该行业各标的

与 K4 策略级退出（`engine/backtest/broker_exits.py`）的分工
--------------------------------------------------------
两者**都会平仓**，差别如下：

===========  ================================  ================================
             K4 `RiskExitMixin`                 L2 `framework/risk/`
===========  ================================  ================================
作用域       单个 `Trade`                       整个组合的 `PortfolioTarget` 集合
时机         每根 bar，`on_bars` **之前**       每次调仓，PCM 之后、执行之前
适用         传统策略（16 个 preset 那类）       `FrameworkStrategy`
输出         直接挂平仓单                       修正目标数量
配置         `strategy.exit_rules()`            `FrameworkStrategy(risk=...)`
===========  ================================  ================================

**同时启用时两者会各自触发一次平仓，本期不做互斥。** 第二次平仓在持仓已被
第一次平掉后会成为 no-op（券商在 `allow_short=False` 下会拒掉超卖的那一张，
在 `allow_short=True` 下则要靠调用方自行约束），不会产生负持仓。
`tests/test_framework_risk.py::test_k4_stoploss_and_l2_risk_together_never_oversell`
是这条保证的验收用例。

想要单一闸门时，二选一即可：`FrameworkStrategy` 用 L2，传统策略用 K4。
"""

from app.engine.framework.risk.base import (
    RISK_TAG_MAX_DRAWDOWN,
    RISK_TAG_PORTFOLIO_DRAWDOWN,
    RISK_TAG_SECTOR_EXPOSURE,
    RISK_TAG_TRAILING_STOP,
    RISK_TAG_UNREALIZED_PROFIT,
    NullRiskModel,
    RiskManagementModel,
)
from app.engine.framework.risk.drawdown import (
    MaximumDrawdownPerSecurity,
    MaximumDrawdownPortfolio,
    TrailingStopRiskManagement,
)
from app.engine.framework.risk.profit import MaximumUnrealizedProfitPerSecurity
from app.engine.framework.risk.sector import (
    MaximumSectorExposure,
    SectorResolver,
    default_sector_of,
)

__all__ = [
    "RISK_TAG_MAX_DRAWDOWN",
    "RISK_TAG_PORTFOLIO_DRAWDOWN",
    "RISK_TAG_SECTOR_EXPOSURE",
    "RISK_TAG_TRAILING_STOP",
    "RISK_TAG_UNREALIZED_PROFIT",
    "MaximumDrawdownPerSecurity",
    "MaximumDrawdownPortfolio",
    "MaximumSectorExposure",
    "MaximumUnrealizedProfitPerSecurity",
    "NullRiskModel",
    "RiskManagementModel",
    "SectorResolver",
    "TrailingStopRiskManagement",
    "default_sector_of",
]
