"""
事件类型定义

参考: refs/vnpy/vnpy/event/engine.py 的 Event 设计
简化为纯数据类，适合 asyncio 场景（无 Thread 锁）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.data.models import Bar, Tick


class EventType(str, Enum):
    # 行情事件
    BAR = "bar"
    TICK = "tick"

    # 订单/成交事件
    ORDER = "order"
    FILL = "fill"

    # 策略生命周期
    STRATEGY_START = "strategy_start"
    STRATEGY_STOP = "strategy_stop"
    STRATEGY_ERROR = "strategy_error"

    # 风控事件
    RISK_BREACH = "risk_breach"

    # 系统事件
    TIMER = "timer"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    """统一事件对象。参考 refs/vnpy/vnpy/event/engine.py Event"""
    type: EventType
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class BarEvent(Event):
    def __init__(self, bar: Bar) -> None:
        super().__init__(type=EventType.BAR, data=bar)


@dataclass
class TickEvent(Event):
    def __init__(self, tick: Tick) -> None:
        super().__init__(type=EventType.TICK, data=tick)
