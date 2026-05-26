"""
风控规则模型

定义所有支持的风控规则类型和违规结果。
规则分为两类：
  - 前置检查（pre-trade）：在下单前调用，拒绝超限订单
  - 实时监控（real-time）：持续检查组合状态，触发警告或强制平仓
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class RuleType(str, Enum):
    # 前置检查
    MAX_POSITION_PCT = "max_position_pct"       # 单标的最大仓位占组合比例
    MAX_ORDER_VALUE = "max_order_value"          # 单笔订单最大金额
    MAX_DAILY_ORDERS = "max_daily_orders"        # 每日最大下单次数
    ALLOWED_MARKETS = "allowed_markets"          # 允许交易的市场白名单
    ALLOWED_SYMBOLS = "allowed_symbols"          # 允许交易的标的白名单（为空=全允许）

    # 实时监控
    DAILY_LOSS_LIMIT = "daily_loss_limit"        # 当日最大亏损占初始净值比例
    MAX_DRAWDOWN = "max_drawdown"                # 最大回撤限制（触发后暂停交易）
    MAX_LEVERAGE = "max_leverage"                # 最大杠杆倍数（仅期货）
    POSITION_CONCENTRATION = "position_concentration"  # 单标的持仓集中度上限


class ViolationSeverity(str, Enum):
    WARNING = "warning"   # 预警，不阻止交易
    BLOCK = "block"       # 阻止本次订单
    HALT = "halt"         # 暂停所有交易


@dataclass(frozen=True)
class RiskRule:
    """单条风控规则定义。"""
    rule_type: RuleType
    value: Any            # 规则阈值（含义因 rule_type 而异）
    enabled: bool = True
    severity: ViolationSeverity = ViolationSeverity.BLOCK

    def to_dict(self) -> dict:
        return {
            "rule_type": self.rule_type.value,
            "value": self.value,
            "enabled": self.enabled,
            "severity": self.severity.value,
        }


@dataclass(frozen=True)
class RiskViolation:
    """一条风控违规记录。"""
    rule_type: RuleType
    severity: ViolationSeverity
    message: str
    value_actual: Any = None    # 实际值
    value_limit: Any = None     # 限制阈值
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "rule_type": self.rule_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "value_actual": self.value_actual,
            "value_limit": self.value_limit,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RiskConfig:
    """
    一套完整风控配置，包含多条规则。

    支持热更新：引擎持有 RiskConfig 引用，替换引用即可生效。
    """
    name: str = "default"
    rules: list[RiskRule] = field(default_factory=list)
    is_active: bool = True

    def get_rule(self, rule_type: RuleType) -> Optional[RiskRule]:
        for rule in self.rules:
            if rule.rule_type == rule_type and rule.enabled:
                return rule
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rules": [r.to_dict() for r in self.rules],
            "is_active": self.is_active,
        }


def default_risk_config() -> RiskConfig:
    """工厂函数：生成默认风控配置。"""
    return RiskConfig(
        name="default",
        rules=[
            RiskRule(RuleType.MAX_POSITION_PCT, 0.20),           # 单标的最多占组合 20%
            RiskRule(RuleType.MAX_ORDER_VALUE, 500_000),          # 单笔最多 50 万
            RiskRule(RuleType.MAX_DAILY_ORDERS, 50),              # 每日最多 50 单
            RiskRule(RuleType.DAILY_LOSS_LIMIT, 0.05),            # 当日亏损不超 5%
            RiskRule(RuleType.MAX_DRAWDOWN, 0.15),                # 最大回撤 15%
            RiskRule(RuleType.POSITION_CONCENTRATION, 0.30),      # 单标的集中度 30%
        ],
    )
