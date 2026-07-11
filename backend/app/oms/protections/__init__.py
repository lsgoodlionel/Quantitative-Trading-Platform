"""动态防护 / 熔断包。"""

from __future__ import annotations

from app.oms.protections.base import (
    ActiveLock,
    IProtection,
    LockScope,
    ProtectionResult,
    TradeRecord,
    TradeSource,
)
from app.oms.protections.config import (
    ProtectionRuleConfig,
    ProtectionsConfig,
    ProtectionType,
    default_protections_config,
)
from app.oms.protections.manager import (
    ProtectionManager,
    get_protection_manager,
    init_protection_manager,
)
from app.oms.protections.registry import build_protection

__all__ = [
    "ActiveLock",
    "IProtection",
    "LockScope",
    "ProtectionResult",
    "TradeRecord",
    "TradeSource",
    "ProtectionRuleConfig",
    "ProtectionsConfig",
    "ProtectionType",
    "default_protections_config",
    "ProtectionManager",
    "get_protection_manager",
    "init_protection_manager",
    "build_protection",
]
