"""
MaxDrawdownProtection — 窗口内权益回撤超阈值即全局熔断。

在 lookback 窗口内，基于累计 profit_abs 权益曲线计算峰值到谷底回撤，
超过 max_allowed_drawdown 则锁定全局。
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


class MaxDrawdownProtection(IProtection):
    has_global_stop = True
    has_symbol_stop = False

    def short_desc(self) -> str:
        return (
            f"最大回撤熔断：{self._cfg.lookback_minutes} 分钟内回撤超 "
            f"{self._cfg.max_allowed_drawdown:.0%} 即全局锁定"
        )

    def stop_per_symbol(
        self,
        symbol: str,
        market: str,
        now: datetime,
        trades: list[TradeRecord],
        starting_balance: float,
    ) -> Optional[ProtectionResult]:
        return None

    def global_stop(
        self, now: datetime, trades: list[TradeRecord], starting_balance: float
    ) -> Optional[ProtectionResult]:
        if len(trades) < self._cfg.trade_limit:
            return None

        ordered = sorted(trades, key=lambda t: t.close_date)
        # 以 starting_balance 为初始权益，逐笔累加 profit_abs 构建权益曲线
        equity = starting_balance
        peak = starting_balance if starting_balance > 0 else 0.0
        max_drawdown = 0.0
        for t in ordered:
            equity += t.profit_abs
            if equity > peak:
                peak = equity
            if peak > 0:
                drawdown = (peak - equity) / peak
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        if max_drawdown <= self._cfg.max_allowed_drawdown:
            return None

        return ProtectionResult(
            scope=LockScope.GLOBAL,
            until=self.calculate_lock_until(now),
            reason=(
                f"{self._cfg.lookback_minutes} 分钟内回撤达 {max_drawdown:.1%}，"
                f"超过阈值 {self._cfg.max_allowed_drawdown:.1%}，"
                f"全局锁定 {self._cfg.stop_duration_minutes} 分钟。"
            ),
            protection_type=self._cfg.type,
        )
