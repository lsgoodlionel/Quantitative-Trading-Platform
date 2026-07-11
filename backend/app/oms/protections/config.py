"""
动态防护配置模型（Pydantic）

一套 ProtectionsConfig 由多条 ProtectionRuleConfig 组成，
采用扁平 union 字段风格（与 RiskRule 一致，前端编辑器简单）。
具体哪些字段对哪种防护生效，由 build_protection() 决定。

存储于 Redis（protections:config 哈希 + :version 计数器），仿 broker_config。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ProtectionType(str, Enum):
    STOPLOSS_GUARD = "stoploss_guard"
    COOLDOWN_PERIOD = "cooldown_period"
    MAX_DRAWDOWN = "max_drawdown"
    LOW_PROFIT_PAIRS = "low_profit_pairs"


class ProtectionRuleConfig(BaseModel):
    """单条防护规则定义（扁平 union，未用字段按类型忽略）。"""

    type: ProtectionType
    enabled: bool = True

    # 所有类型通用：触发后锁定时长
    stop_duration_minutes: int = Field(default=60, ge=1, le=43200)

    # stoploss_guard / max_drawdown / low_profit_pairs：历史回看窗口
    lookback_minutes: int = Field(default=1440, ge=1, le=43200)

    # stoploss_guard / max_drawdown：触发前最少已平仓交易数
    trade_limit: int = Field(default=4, ge=1, le=1000)

    # stoploss_guard：仅统计盈亏比低于该值的止损单
    required_profit: float = 0.0

    # stoploss_guard：为真时禁用全局停，仅锁定触发标的
    only_per_symbol: bool = False

    # max_drawdown：触发全局熔断的回撤比例（0.10 = 10%）
    max_allowed_drawdown: float = Field(default=0.10, gt=0, le=1)

    # low_profit_pairs：标的聚合盈亏比低于该值则锁定
    min_profit_ratio: float = 0.0

    # low_profit_pairs：判定"低盈利"前该标的最少交易数
    required_trades: int = Field(default=2, ge=1, le=1000)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "enabled": self.enabled,
            "stop_duration_minutes": self.stop_duration_minutes,
            "lookback_minutes": self.lookback_minutes,
            "trade_limit": self.trade_limit,
            "required_profit": self.required_profit,
            "only_per_symbol": self.only_per_symbol,
            "max_allowed_drawdown": self.max_allowed_drawdown,
            "min_profit_ratio": self.min_profit_ratio,
            "required_trades": self.required_trades,
        }


class ProtectionsConfig(BaseModel):
    """完整防护配置集合。"""

    is_active: bool = True
    rules: list[ProtectionRuleConfig] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_active": self.is_active,
            "rules": [r.to_dict() for r in self.rules],
        }


def default_protections_config() -> ProtectionsConfig:
    """工厂函数：默认防护配置。"""
    return ProtectionsConfig(
        is_active=True,
        rules=[
            ProtectionRuleConfig(
                type=ProtectionType.STOPLOSS_GUARD,
                enabled=True,
                lookback_minutes=1440,
                trade_limit=4,
                required_profit=0.0,
                stop_duration_minutes=60,
            ),
            ProtectionRuleConfig(
                type=ProtectionType.COOLDOWN_PERIOD,
                enabled=True,
                stop_duration_minutes=30,
            ),
            ProtectionRuleConfig(
                type=ProtectionType.MAX_DRAWDOWN,
                enabled=True,
                lookback_minutes=1440,
                trade_limit=5,
                max_allowed_drawdown=0.10,
                stop_duration_minutes=120,
            ),
            ProtectionRuleConfig(
                type=ProtectionType.LOW_PROFIT_PAIRS,
                enabled=False,
                lookback_minutes=1440,
                required_trades=2,
                min_profit_ratio=0.0,
                stop_duration_minutes=60,
            ),
        ],
    )
