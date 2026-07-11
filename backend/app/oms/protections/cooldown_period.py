"""
CooldownPeriod — 任一标的成交后，在冷却窗口内禁止再次入场。

冷却窗口即 stop_duration_minutes；不使用 lookback。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.oms.protections.base import (
    IProtection,
    LockScope,
    ProtectionResult,
    TradeRecord,
)


class CooldownPeriod(IProtection):
    has_global_stop = False
    has_symbol_stop = True

    def short_desc(self) -> str:
        return f"冷却期：成交后 {self._cfg.stop_duration_minutes} 分钟内禁止再入场"

    def global_stop(
        self, now: datetime, trades: list[TradeRecord], starting_balance: float
    ) -> Optional[ProtectionResult]:
        return None

    def stop_per_symbol(
        self,
        symbol: str,
        market: str,
        now: datetime,
        trades: list[TradeRecord],
        starting_balance: float,
    ) -> Optional[ProtectionResult]:
        window_start = now - timedelta(minutes=self._cfg.stop_duration_minutes)
        recent = [t for t in trades if t.close_date >= window_start]
        if not recent:
            return None
        last_close = max(t.close_date for t in recent)
        until = last_close + timedelta(minutes=self._cfg.stop_duration_minutes)
        if until <= now:
            return None
        return ProtectionResult(
            scope=LockScope.SYMBOL,
            until=until,
            reason=(
                f"{symbol} 刚于冷却窗口内成交，"
                f"冷却 {self._cfg.stop_duration_minutes} 分钟后方可再入场。"
            ),
            protection_type=self._cfg.type,
            symbol=symbol,
            market=market,
        )
