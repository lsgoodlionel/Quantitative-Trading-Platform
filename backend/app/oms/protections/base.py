"""
防护基础类型：锁范围、防护结果、活跃锁、交易记录 DTO、防护接口。

设计原则（与 RiskEngine 一致）：
- 防护是建议性的：只读状态并返回锁，绝不修改订单或强平。
- 结果对象为 frozen dataclass，不可变。
- 两种锁范围：GLOBAL（全标的）/ SYMBOL（单标的）。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Protocol

from app.oms.protections.config import ProtectionRuleConfig, ProtectionType


def utcnow() -> datetime:
    """返回带时区（UTC）的当前时间。"""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """将 naive datetime 视作 UTC，返回带时区的副本。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class LockScope(str, Enum):
    GLOBAL = "global"    # 阻止所有标的入场
    SYMBOL = "symbol"    # 阻止单一标的入场


# 保留字段：未来做多/做空分别锁定用，当前恒为 "*"
SIDE_BOTH = "*"


@dataclass(frozen=True)
class TradeRecord:
    """输入给防护的已平仓交易最小 DTO（与 LiveOrder 解耦）。"""

    symbol: str
    market: str
    side: str
    close_date: datetime
    profit_ratio: float
    profit_abs: float
    exit_reason: str

    def is_stoploss(self) -> bool:
        return self.exit_reason.lower() == "stop_loss"


@dataclass(frozen=True)
class ProtectionResult:
    """防护触发时返回的锁定结果；未触发返回 None。"""

    scope: LockScope
    until: datetime
    reason: str
    protection_type: ProtectionType
    symbol: Optional[str] = None
    market: Optional[str] = None
    side: str = SIDE_BOTH

    def to_dict(self, now: Optional[datetime] = None) -> dict:
        ref = now or utcnow()
        return {
            "scope": self.scope.value,
            "symbol": self.symbol,
            "market": self.market,
            "reason": self.reason,
            "protection_type": self.protection_type.value,
            "side": self.side,
            "until": self.until.isoformat(),
            "active": self.until > ref,
        }


@dataclass
class ActiveLock:
    """由 ProtectionResult 提升而来的活跃锁（API/UI 消费）。"""

    scope: LockScope
    reason: str
    protection_type: ProtectionType
    until: datetime
    symbol: Optional[str] = None
    market: Optional[str] = None
    side: str = SIDE_BOTH
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    locked_at: datetime = field(default_factory=utcnow)

    def is_active(self, now: Optional[datetime] = None) -> bool:
        return self.until > (now or utcnow())

    def dedup_key(self) -> tuple:
        return (self.protection_type, self.scope, self.symbol, self.market)

    def to_result(self) -> ProtectionResult:
        return ProtectionResult(
            scope=self.scope,
            until=self.until,
            reason=self.reason,
            protection_type=self.protection_type,
            symbol=self.symbol,
            market=self.market,
            side=self.side,
        )

    def to_dict(self, now: Optional[datetime] = None) -> dict:
        ref = now or utcnow()
        return {
            "id": self.id,
            "scope": self.scope.value,
            "symbol": self.symbol,
            "market": self.market,
            "reason": self.reason,
            "protection_type": self.protection_type.value,
            "locked_at": self.locked_at.isoformat(),
            "until": self.until.isoformat(),
            "active": self.until > ref,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveLock":
        return cls(
            id=data["id"],
            scope=LockScope(data["scope"]),
            symbol=data.get("symbol"),
            market=data.get("market"),
            reason=data.get("reason", ""),
            protection_type=ProtectionType(data["protection_type"]),
            side=data.get("side", SIDE_BOTH),
            locked_at=ensure_utc(datetime.fromisoformat(data["locked_at"])),
            until=ensure_utc(datetime.fromisoformat(data["until"])),
        )


class TradeSource(Protocol):
    """闭仓交易历史只读访问接口。OrderManager（或其适配器）实现之。"""

    def get_closed_trades(
        self, symbol: Optional[str], since: datetime
    ) -> list[TradeRecord]: ...


class IProtection(ABC):
    """防护接口。子类为输入的纯函数：不查存储，只判断给定 trades。"""

    has_global_stop: bool = False
    has_symbol_stop: bool = False

    def __init__(self, cfg: ProtectionRuleConfig) -> None:
        self._cfg = cfg

    @property
    def cfg(self) -> ProtectionRuleConfig:
        return self._cfg

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def short_desc(self) -> str:
        return f"{self.name}(stop={self._cfg.stop_duration_minutes}min)"

    def calculate_lock_until(self, now: datetime) -> datetime:
        return now + timedelta(minutes=self._cfg.stop_duration_minutes)

    @abstractmethod
    def global_stop(
        self,
        now: datetime,
        trades: list[TradeRecord],
        starting_balance: float,
    ) -> Optional[ProtectionResult]: ...

    @abstractmethod
    def stop_per_symbol(
        self,
        symbol: str,
        market: str,
        now: datetime,
        trades: list[TradeRecord],
        starting_balance: float,
    ) -> Optional[ProtectionResult]: ...
