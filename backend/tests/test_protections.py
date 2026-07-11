"""动态防护（Protections）单元测试

覆盖：
- StoplossGuard：回看窗口内止损达 N 次即锁定（<N 不锁）
- CooldownPeriod：成交后冷却窗口内锁定、窗口过后放行
- MaxDrawdownProtection：权益回撤超阈值即全局锁（未超阈不锁）
- 防护只 gate 入场、不困出场（管理器无出场闸口，锁均为建议性入场锁）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.oms.protections.base import LockScope, TradeRecord
from app.oms.protections.config import (
    ProtectionRuleConfig,
    ProtectionType,
    ProtectionsConfig,
)
from app.oms.protections.cooldown_period import CooldownPeriod
from app.oms.protections.manager import ProtectionManager
from app.oms.protections.max_drawdown import MaxDrawdownProtection
from app.oms.protections.stoploss_guard import StoplossGuard

NOW = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _trade(
    *,
    symbol: str = "AAPL",
    market: str = "US",
    minutes_ago: int = 5,
    profit_ratio: float = -0.02,
    profit_abs: float = -200.0,
    exit_reason: str = "stop_loss",
    side: str = "long",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        market=market,
        side=side,
        close_date=NOW - timedelta(minutes=minutes_ago),
        profit_ratio=profit_ratio,
        profit_abs=profit_abs,
        exit_reason=exit_reason,
    )


class _FakeTradeSource:
    """按需返回预置闭仓交易的只读 TradeSource。"""

    def __init__(self, trades: list[TradeRecord]) -> None:
        self._trades = trades

    def get_closed_trades(
        self, symbol: Optional[str], since: datetime
    ) -> list[TradeRecord]:
        out = [t for t in self._trades if t.close_date >= since]
        if symbol is not None:
            out = [t for t in out if t.symbol == symbol]
        return out


# ── StoplossGuard ─────────────────────────────────────────────

class TestStoplossGuard:
    def _cfg(self, trade_limit: int = 4, only_per_symbol: bool = False) -> ProtectionRuleConfig:
        return ProtectionRuleConfig(
            type=ProtectionType.STOPLOSS_GUARD,
            trade_limit=trade_limit,
            lookback_minutes=1440,
            stop_duration_minutes=60,
            required_profit=0.0,
            only_per_symbol=only_per_symbol,
        )

    def test_locks_after_n_stoplosses(self) -> None:
        # Arrange: 恰好 4 笔亏损止损单
        guard = StoplossGuard(self._cfg(trade_limit=4))
        trades = [_trade(minutes_ago=i + 1) for i in range(4)]

        # Act
        result = guard.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert: 触发全局锁
        assert result is not None
        assert result.scope == LockScope.GLOBAL
        assert result.until > NOW

    def test_no_lock_below_threshold(self) -> None:
        # Arrange: 仅 3 笔（< trade_limit=4）
        guard = StoplossGuard(self._cfg(trade_limit=4))
        trades = [_trade(minutes_ago=i + 1) for i in range(3)]

        # Act
        result = guard.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert
        assert result is None

    def test_profitable_stops_not_counted(self) -> None:
        # Arrange: 止损单但盈亏比 >= required_profit(0) → 不计数
        guard = StoplossGuard(self._cfg(trade_limit=2))
        trades = [
            _trade(minutes_ago=1, profit_ratio=0.01),
            _trade(minutes_ago=2, profit_ratio=0.02),
            _trade(minutes_ago=3, profit_ratio=0.03),
        ]

        # Act
        result = guard.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert: 无一计入 → 不锁
        assert result is None

    def test_only_per_symbol_disables_global(self) -> None:
        # Arrange
        guard = StoplossGuard(self._cfg(trade_limit=2, only_per_symbol=True))
        trades = [_trade(minutes_ago=i + 1) for i in range(3)]

        # Act
        gres = guard.global_stop(NOW, trades, starting_balance=10_000.0)
        sres = guard.stop_per_symbol("AAPL", "US", NOW, trades, 10_000.0)

        # Assert: 全局停被禁用，逐标的仍锁
        assert gres is None
        assert sres is not None
        assert sres.scope == LockScope.SYMBOL
        assert sres.symbol == "AAPL"


# ── CooldownPeriod ────────────────────────────────────────────

class TestCooldownPeriod:
    def _cfg(self, stop_minutes: int = 30) -> ProtectionRuleConfig:
        return ProtectionRuleConfig(
            type=ProtectionType.COOLDOWN_PERIOD,
            stop_duration_minutes=stop_minutes,
        )

    def test_locks_within_cooldown_window(self) -> None:
        # Arrange: 5 分钟前刚成交，冷却 30 分钟
        cd = CooldownPeriod(self._cfg(stop_minutes=30))
        trades = [_trade(minutes_ago=5, exit_reason="roi")]

        # Act
        result = cd.stop_per_symbol("AAPL", "US", NOW, trades, 0.0)

        # Assert
        assert result is not None
        assert result.scope == LockScope.SYMBOL
        # 锁至 成交时刻 + 30min
        assert result.until == NOW - timedelta(minutes=5) + timedelta(minutes=30)

    def test_releases_after_cooldown(self) -> None:
        # Arrange: 成交发生在 40 分钟前，冷却仅 30 分钟 → 已过期
        cd = CooldownPeriod(self._cfg(stop_minutes=30))
        trades = [_trade(minutes_ago=40, exit_reason="roi")]

        # Act
        result = cd.stop_per_symbol("AAPL", "US", NOW, trades, 0.0)

        # Assert: 冷却窗口外 → 放行
        assert result is None

    def test_global_stop_is_noop(self) -> None:
        # Arrange: cooldown 无全局停
        cd = CooldownPeriod(self._cfg())
        trades = [_trade(minutes_ago=1)]

        # Act / Assert
        assert cd.global_stop(NOW, trades, 0.0) is None


# ── MaxDrawdownProtection ─────────────────────────────────────

class TestMaxDrawdownProtection:
    def _cfg(self, dd: float = 0.10, trade_limit: int = 3) -> ProtectionRuleConfig:
        return ProtectionRuleConfig(
            type=ProtectionType.MAX_DRAWDOWN,
            lookback_minutes=1440,
            trade_limit=trade_limit,
            max_allowed_drawdown=dd,
            stop_duration_minutes=120,
        )

    def test_global_lock_on_excess_drawdown(self) -> None:
        # Arrange: 起始 10000，中途大亏造成 ~30% 回撤
        prot = MaxDrawdownProtection(self._cfg(dd=0.10, trade_limit=3))
        trades = [
            _trade(minutes_ago=30, profit_abs=100.0, exit_reason="roi"),
            _trade(minutes_ago=20, profit_abs=-3000.0),
            _trade(minutes_ago=10, profit_abs=100.0, exit_reason="roi"),
        ]

        # Act
        result = prot.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert
        assert result is not None
        assert result.scope == LockScope.GLOBAL

    def test_no_lock_below_drawdown_threshold(self) -> None:
        # Arrange: 小幅波动，回撤 < 10%
        prot = MaxDrawdownProtection(self._cfg(dd=0.10, trade_limit=3))
        trades = [
            _trade(minutes_ago=30, profit_abs=100.0, exit_reason="roi"),
            _trade(minutes_ago=20, profit_abs=-200.0, exit_reason="roi"),
            _trade(minutes_ago=10, profit_abs=150.0, exit_reason="roi"),
        ]

        # Act
        result = prot.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert
        assert result is None

    def test_no_lock_below_trade_limit(self) -> None:
        # Arrange: 交易数不足 trade_limit
        prot = MaxDrawdownProtection(self._cfg(dd=0.10, trade_limit=5))
        trades = [_trade(minutes_ago=10, profit_abs=-5000.0)]

        # Act
        result = prot.global_stop(NOW, trades, starting_balance=10_000.0)

        # Assert
        assert result is None

    def test_stop_per_symbol_is_noop(self) -> None:
        prot = MaxDrawdownProtection(self._cfg())
        assert prot.stop_per_symbol("AAPL", "US", NOW, [], 10_000.0) is None


# ── 管理器集成 + 「只 gate 入场不困出场」 ───────────────────────

class TestProtectionManagerGating:
    def _cooldown_manager(self, trades: list[TradeRecord]) -> ProtectionManager:
        cfg = ProtectionsConfig(
            is_active=True,
            rules=[
                ProtectionRuleConfig(
                    type=ProtectionType.COOLDOWN_PERIOD,
                    enabled=True,
                    stop_duration_minutes=30,
                ),
            ],
        )
        return ProtectionManager(
            config=cfg,
            trade_source=_FakeTradeSource(trades),
            starting_balance=10_000.0,
        )

    def test_check_entry_blocks_within_cooldown(self) -> None:
        # Arrange: 5 分钟前成交 → 冷却期内
        mgr = self._cooldown_manager([_trade(minutes_ago=5, exit_reason="roi")])

        # Act
        lock = mgr.check_entry("AAPL", "US", now=NOW)

        # Assert: 入场被拦
        assert lock is not None
        assert lock.scope == LockScope.SYMBOL

    def test_check_entry_allows_after_cooldown(self) -> None:
        # Arrange: 40 分钟前成交，冷却 30 分钟 → 已过期
        mgr = self._cooldown_manager([_trade(minutes_ago=40, exit_reason="roi")])

        # Act
        lock = mgr.check_entry("AAPL", "US", now=NOW)

        # Assert: 放行
        assert lock is None

    def test_stoploss_guard_global_lock_blocks_other_symbol_entry(self) -> None:
        # Arrange: 全局止损熔断后，其它标的入场也被拦
        cfg = ProtectionsConfig(
            is_active=True,
            rules=[
                ProtectionRuleConfig(
                    type=ProtectionType.STOPLOSS_GUARD,
                    enabled=True,
                    trade_limit=3,
                    lookback_minutes=1440,
                    stop_duration_minutes=60,
                    required_profit=0.0,
                ),
            ],
        )
        trades = [_trade(symbol="AAPL", minutes_ago=i + 1) for i in range(3)]
        mgr = ProtectionManager(
            config=cfg, trade_source=_FakeTradeSource(trades), starting_balance=10_000.0
        )

        # Act
        lock = mgr.check_entry("TSLA", "US", now=NOW)

        # Assert: 全局锁 → 连未止损的 TSLA 也被拦入场
        assert lock is not None
        assert lock.scope == LockScope.GLOBAL

    def test_gates_entry_only_no_exit_gate(self) -> None:
        # Arrange: 处于锁定状态
        mgr = self._cooldown_manager([_trade(minutes_ago=5, exit_reason="roi")])
        assert mgr.check_entry("AAPL", "US", now=NOW) is not None

        # Assert: 防护只提供入场闸口 check_entry，不存在困住出场的接口
        assert hasattr(mgr, "check_entry")
        assert not hasattr(mgr, "check_exit")
        assert not hasattr(mgr, "block_exit")

    def test_inactive_config_never_locks(self) -> None:
        # Arrange: 全局开关关闭
        mgr = self._cooldown_manager([_trade(minutes_ago=1, exit_reason="roi")])
        mgr.update_config(
            ProtectionsConfig(is_active=False, rules=mgr.config.rules)
        )

        # Act
        lock = mgr.check_entry("AAPL", "US", now=NOW)

        # Assert
        assert lock is None
