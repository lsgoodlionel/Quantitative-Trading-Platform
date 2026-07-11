"""
StoplossGuard — 在回看窗口内止损次数达到阈值即熔断。

参考：refs/freqtrade plugins/protections/stoploss_guard.py（仅模式，未复制代码）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.oms.protections.base import (
    IProtection,
    LockScope,
    ProtectionResult,
    TradeRecord,
)


class StoplossGuard(IProtection):
    has_global_stop = True
    has_symbol_stop = True

    def short_desc(self) -> str:
        return (
            f"止损熔断：{self._cfg.lookback_minutes} 分钟内止损达 "
            f"{self._cfg.trade_limit} 次即锁定 {self._cfg.stop_duration_minutes} 分钟"
        )

    def _count_stoplosses(self, trades: list[TradeRecord]) -> int:
        return sum(
            1
            for t in trades
            if t.is_stoploss() and t.profit_ratio < self._cfg.required_profit
        )

    def global_stop(
        self, now: datetime, trades: list[TradeRecord], starting_balance: float
    ) -> Optional[ProtectionResult]:
        if self._cfg.only_per_symbol:
            return None
        count = self._count_stoplosses(trades)
        if count < self._cfg.trade_limit:
            return None
        return ProtectionResult(
            scope=LockScope.GLOBAL,
            until=self.calculate_lock_until(now),
            reason=(
                f"{self._cfg.lookback_minutes} 分钟内触发 {count} 次止损，"
                f"全局锁定 {self._cfg.stop_duration_minutes} 分钟。"
            ),
            protection_type=self._cfg.type,
        )

    def stop_per_symbol(
        self,
        symbol: str,
        market: str,
        now: datetime,
        trades: list[TradeRecord],
        starting_balance: float,
    ) -> Optional[ProtectionResult]:
        count = self._count_stoplosses(trades)
        if count < self._cfg.trade_limit:
            return None
        return ProtectionResult(
            scope=LockScope.SYMBOL,
            until=self.calculate_lock_until(now),
            reason=(
                f"{symbol} 在 {self._cfg.lookback_minutes} 分钟内触发 {count} 次止损，"
                f"锁定 {self._cfg.stop_duration_minutes} 分钟。"
            ),
            protection_type=self._cfg.type,
            symbol=symbol,
            market=market,
        )
