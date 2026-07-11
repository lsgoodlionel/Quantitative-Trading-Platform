"""防护工厂：ProtectionType → 具体防护类。"""

from __future__ import annotations

from app.oms.protections.base import IProtection
from app.oms.protections.config import ProtectionRuleConfig, ProtectionType
from app.oms.protections.cooldown_period import CooldownPeriod
from app.oms.protections.low_profit_pairs import LowProfitPairs
from app.oms.protections.max_drawdown import MaxDrawdownProtection
from app.oms.protections.stoploss_guard import StoplossGuard

_REGISTRY: dict[ProtectionType, type[IProtection]] = {
    ProtectionType.STOPLOSS_GUARD: StoplossGuard,
    ProtectionType.COOLDOWN_PERIOD: CooldownPeriod,
    ProtectionType.MAX_DRAWDOWN: MaxDrawdownProtection,
    ProtectionType.LOW_PROFIT_PAIRS: LowProfitPairs,
}


def build_protection(cfg: ProtectionRuleConfig) -> IProtection:
    """根据规则配置构造对应防护实例。"""
    cls = _REGISTRY.get(cfg.type)
    if cls is None:
        raise ValueError(f"Unknown protection type: {cfg.type}")
    return cls(cfg)
