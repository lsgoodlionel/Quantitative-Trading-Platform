"""
交易日历（Wave K-b / K7）

三市场日线级交易日历，用于回测的日期校验、数据缺口检出与年化基准实测。
默认**不参与**回测：`BacktestConfig.calendar` 为 None 时行为与接入前完全一致。

    from app.engine.calendar import get_calendar
    cal = get_calendar(Market.A)
    cal.sessions_count(date(2024, 1, 1), date(2024, 12, 31))   # 242
"""

from __future__ import annotations

from app.data.models import Market
from app.engine.calendar.a_share import AShareCalendar
from app.engine.calendar.base import SessionWindow, TradingCalendar
from app.engine.calendar.hk import HKCalendar
from app.engine.calendar.us import USCalendar

_REGISTRY: dict[Market, type[TradingCalendar]] = {
    Market.US: USCalendar,
    Market.HK: HKCalendar,
    Market.A: AShareCalendar,
}

# 日历无状态（仅内部缓存），进程内复用单例即可
_INSTANCES: dict[Market, TradingCalendar] = {}


def get_calendar(market: Market) -> TradingCalendar:
    """按市场返回交易日历单例。"""
    if market not in _REGISTRY:
        raise ValueError(f"暂不支持 {market} 的交易日历，可选：{sorted(m.value for m in _REGISTRY)}")
    if market not in _INSTANCES:
        _INSTANCES[market] = _REGISTRY[market]()
    return _INSTANCES[market]


__all__ = [
    "AShareCalendar",
    "HKCalendar",
    "SessionWindow",
    "TradingCalendar",
    "USCalendar",
    "get_calendar",
]
