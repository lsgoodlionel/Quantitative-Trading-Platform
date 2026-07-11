"""
LowProfitPairs — 锁定长期低盈利/亏损的标的。

在 lookback 窗口内，若某标的交易数 >= required_trades，
且其盈亏比的均值 < min_profit_ratio，则锁定该标的。

聚合方式：采用 profit_ratio 的均值（mean）。
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


class LowProfitPairs(IProtection):
    has_global_stop = False
    has_symbol_stop = True

    def short_desc(self) -> str:
        return (
            f"低盈利标的锁定：{self._cfg.lookback_minutes} 分钟内均盈亏比低于 "
            f"{self._cfg.min_profit_ratio:.2%} 即锁定"
        )

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
        if len(trades) < self._cfg.required_trades:
            return None

        mean_profit = sum(t.profit_ratio for t in trades) / len(trades)
        if mean_profit >= self._cfg.min_profit_ratio:
            return None

        return ProtectionResult(
            scope=LockScope.SYMBOL,
            until=self.calculate_lock_until(now),
            reason=(
                f"{symbol} 近 {len(trades)} 笔平均盈亏比 {mean_profit:.2%}，"
                f"低于阈值 {self._cfg.min_profit_ratio:.2%}，"
                f"锁定 {self._cfg.stop_duration_minutes} 分钟。"
            ),
            protection_type=self._cfg.type,
            symbol=symbol,
            market=market,
        )
