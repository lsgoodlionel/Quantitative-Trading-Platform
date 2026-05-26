"""
实盘订单模型

与回测的 Order 独立定义，增加实盘必要字段：
broker_order_id（券商返回的订单号）、strategy_id（策略归属）等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class LiveOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class LiveOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class LiveOrderStatus(str, Enum):
    PENDING_SUBMIT = "pending_submit"   # 本地已创建，等待发送到券商
    SUBMITTED = "submitted"             # 已发送到券商
    PARTIAL = "partial"                 # 部分成交
    FILLED = "filled"                   # 完全成交
    CANCELLED = "cancelled"             # 已撤销
    REJECTED = "rejected"               # 被券商拒绝
    EXPIRED = "expired"                 # 已过期


@dataclass
class LiveOrder:
    """实盘订单，对应数据库 orders 表的一行。"""

    symbol: str
    market: str                          # US / HK
    side: LiveOrderSide
    qty: int
    order_type: LiveOrderType = LiveOrderType.MARKET
    limit_price: Optional[float] = None

    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    broker_order_id: Optional[str] = None   # 券商返回的原始 ID
    strategy_id: Optional[str] = None
    account_id: Optional[str] = None

    status: LiveOrderStatus = LiveOrderStatus.PENDING_SUBMIT
    filled_qty: int = 0
    avg_fill_price: Optional[float] = None
    commission: float = 0.0
    reject_reason: Optional[str] = None

    created_at: datetime = field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_active(self) -> bool:
        return self.status in (
            LiveOrderStatus.PENDING_SUBMIT,
            LiveOrderStatus.SUBMITTED,
            LiveOrderStatus.PARTIAL,
        )

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "broker_order_id": self.broker_order_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side.value,
            "qty": self.qty,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "commission": self.commission,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at.isoformat(),
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }


@dataclass
class LiveFill:
    """一笔实盘成交记录。"""

    order_id: str
    symbol: str
    market: str
    side: LiveOrderSide
    qty: int
    price: float
    commission: float
    filled_at: datetime
    broker_fill_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "broker_fill_id": self.broker_fill_id,
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side.value,
            "qty": self.qty,
            "price": self.price,
            "commission": self.commission,
            "filled_at": self.filled_at.isoformat(),
        }
