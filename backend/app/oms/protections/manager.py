"""
ProtectionManager — 组合防护规则，评估全局 + 逐标的锁，维护活跃锁。

设计（镜像 RiskEngine 单例）：
- 持有 ProtectionsConfig 引用，update_config 热替换。
- 依赖 TradeSource（OrderManager 或适配器）读取闭仓历史。
- 防护类为纯函数：manager 负责取数、按窗口切片、汇总锁。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from app.oms.protections.base import (
    ActiveLock,
    IProtection,
    LockScope,
    ProtectionResult,
    TradeRecord,
    TradeSource,
    ensure_utc,
    utcnow,
)
from app.oms.protections.config import ProtectionsConfig, default_protections_config
from app.oms.protections.registry import build_protection
from app.oms.protections.store import delete_lock, save_lock

logger = logging.getLogger(__name__)


class ProtectionManager:
    def __init__(
        self,
        config: Optional[ProtectionsConfig] = None,
        trade_source: Optional[TradeSource] = None,
        starting_balance: float = 0.0,
        redis_client=None,
    ) -> None:
        self._config = config or default_protections_config()
        self._trade_source = trade_source
        self._starting_balance = starting_balance
        self._locks: dict[str, ActiveLock] = {}   # lock_id → ActiveLock
        self._redis = redis_client   # 可选：活跃锁持久化到 Redis（重启恢复）

    # ── 活跃锁持久化（Redis，可选，fire-and-forget 不阻塞热路径） ──

    def restore_locks(self, locks: list[ActiveLock]) -> None:
        """启动时用持久化的活跃锁回填内存（过期锁自动忽略）。"""
        now = utcnow()
        for lock in locks:
            if lock.is_active(now):
                self._locks[lock.id] = lock

    def _persist_lock(self, lock: ActiveLock) -> None:
        if self._redis is None:
            return
        self._schedule(save_lock(self._redis, lock))

    def _persist_delete(self, lock_id: str) -> None:
        if self._redis is None:
            return
        self._schedule(delete_lock(self._redis, lock_id))

    def _schedule(self, coro) -> None:
        """在当前事件循环上以后台任务执行持久化协程；无循环时安全丢弃。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()   # 无运行中的事件循环，避免 "never awaited" 告警
            return
        loop.create_task(self._guard(coro))

    @staticmethod
    async def _guard(coro) -> None:
        try:
            await coro
        except Exception:
            logger.debug("Protection lock persistence failed", exc_info=True)

    # ── 配置 ──────────────────────────────────────────────────

    def update_config(self, config: ProtectionsConfig) -> None:
        """热更新配置；保留已有活跃锁。"""
        self._config = config
        logger.info("Protections config updated (%d rules)", len(config.rules))

    @property
    def config(self) -> ProtectionsConfig:
        return self._config

    def set_starting_balance(self, value: float) -> None:
        self._starting_balance = value

    # ── 内部工具 ──────────────────────────────────────────────

    def _enabled_protections(self) -> list[IProtection]:
        out: list[IProtection] = []
        for rule in self._config.rules:
            if not rule.enabled:
                continue
            try:
                out.append(build_protection(rule))
            except ValueError:
                logger.warning("Skipping unknown protection: %s", rule.type)
        return out

    def _widest_since(self, now: datetime) -> datetime:
        """所有启用规则中最宽的回看窗口起点。"""
        max_minutes = 1
        for rule in self._config.rules:
            if not rule.enabled:
                continue
            max_minutes = max(max_minutes, rule.lookback_minutes, rule.stop_duration_minutes)
        return now - timedelta(minutes=max_minutes)

    def _fetch_trades(self, symbol: Optional[str], since: datetime) -> list[TradeRecord]:
        if self._trade_source is None:
            return []
        try:
            trades = self._trade_source.get_closed_trades(symbol, since)
        except Exception:
            logger.exception("Failed to fetch closed trades")
            return []
        return [
            TradeRecord(
                symbol=t.symbol,
                market=t.market,
                side=t.side,
                close_date=ensure_utc(t.close_date),
                profit_ratio=t.profit_ratio,
                profit_abs=t.profit_abs,
                exit_reason=t.exit_reason,
            )
            for t in trades
        ]

    @staticmethod
    def _within(trades: list[TradeRecord], now: datetime, minutes: int) -> list[TradeRecord]:
        start = now - timedelta(minutes=minutes)
        return [t for t in trades if t.close_date >= start]

    def _upsert_lock(self, result: ProtectionResult, now: datetime) -> ActiveLock:
        """将 ProtectionResult 提升为 ActiveLock（按去重键 upsert）。"""
        new_lock = ActiveLock(
            scope=result.scope,
            reason=result.reason,
            protection_type=result.protection_type,
            until=result.until,
            symbol=result.symbol,
            market=result.market,
            side=result.side,
            locked_at=now,
        )
        key = new_lock.dedup_key()
        for existing in self._locks.values():
            if existing.dedup_key() == key and existing.is_active(now):
                existing.until = result.until
                existing.reason = result.reason
                self._persist_lock(existing)
                return existing
        self._locks[new_lock.id] = new_lock
        self._persist_lock(new_lock)
        return new_lock

    def _prune(self, now: datetime) -> None:
        expired = [lid for lid, lk in self._locks.items() if not lk.is_active(now)]
        for lid in expired:
            del self._locks[lid]
            self._persist_delete(lid)

    # ── 评估 ──────────────────────────────────────────────────

    def evaluate(self, now: Optional[datetime] = None) -> list[ProtectionResult]:
        """运行所有启用的全局 + 逐标的规则，创建/刷新活跃锁。"""
        now = ensure_utc(now) if now else utcnow()
        self._prune(now)
        if not self._config.is_active:
            return []

        protections = self._enabled_protections()
        if not protections:
            return []

        all_trades = self._fetch_trades(None, self._widest_since(now))
        symbols = {(t.symbol, t.market) for t in all_trades}

        results: list[ProtectionResult] = []
        for prot in protections:
            lookback = prot.cfg.lookback_minutes
            # cooldown 用 stop_duration 作为窗口
            window = max(lookback, prot.cfg.stop_duration_minutes)

            if prot.has_global_stop:
                sliced = self._within(all_trades, now, lookback)
                res = prot.global_stop(now, sliced, self._starting_balance)
                if res is not None:
                    self._upsert_lock(res, now)
                    results.append(res)

            if prot.has_symbol_stop:
                for symbol, market in symbols:
                    sym_trades = [
                        t for t in self._within(all_trades, now, window)
                        if t.symbol == symbol and t.market == market
                    ]
                    if not sym_trades:
                        continue
                    res = prot.stop_per_symbol(
                        symbol, market, now, sym_trades, self._starting_balance
                    )
                    if res is not None:
                        self._upsert_lock(res, now)
                        results.append(res)

        return results

    # ── 前置查询 ──────────────────────────────────────────────

    def check_entry(
        self,
        symbol: str,
        market: str,
        now: Optional[datetime] = None,
        starting_balance: float = 0.0,
    ) -> Optional[ProtectionResult]:
        """
        返回阻止入场的锁（全局优先，其次标的级），否则 None。
        先查活跃锁（廉价），再评估规则。
        """
        now = ensure_utc(now) if now else utcnow()
        if not self._config.is_active:
            return None
        if starting_balance:
            self._starting_balance = starting_balance

        # 先查现有活跃锁
        glock = self.is_globally_locked(now)
        if glock is not None:
            return glock.to_result()
        slock = self.is_symbol_locked(symbol, market, now)
        if slock is not None:
            return slock.to_result()

        # 再评估规则（可能产生新锁）
        self.evaluate(now)
        glock = self.is_globally_locked(now)
        if glock is not None:
            return glock.to_result()
        slock = self.is_symbol_locked(symbol, market, now)
        if slock is not None:
            return slock.to_result()
        return None

    # ── 锁查询 / 管理 ─────────────────────────────────────────

    def active_locks(self, now: Optional[datetime] = None) -> list[ActiveLock]:
        now = ensure_utc(now) if now else utcnow()
        self._prune(now)
        return sorted(self._locks.values(), key=lambda lk: lk.locked_at, reverse=True)

    def clear_lock(self, lock_id: str) -> bool:
        if lock_id in self._locks:
            del self._locks[lock_id]
            self._persist_delete(lock_id)
            return True
        return False

    def is_globally_locked(self, now: datetime) -> Optional[ActiveLock]:
        for lk in self._locks.values():
            if lk.scope == LockScope.GLOBAL and lk.is_active(now):
                return lk
        return None

    def is_symbol_locked(
        self, symbol: str, market: str, now: datetime
    ) -> Optional[ActiveLock]:
        for lk in self._locks.values():
            if (
                lk.scope == LockScope.SYMBOL
                and lk.symbol == symbol
                and lk.market == market
                and lk.is_active(now)
            ):
                return lk
        return None


# ── 全局单例 ──────────────────────────────────────────────────

_manager: Optional[ProtectionManager] = None


def get_protection_manager() -> ProtectionManager:
    global _manager
    if _manager is None:
        _manager = ProtectionManager()
    return _manager


def init_protection_manager(
    config: Optional[ProtectionsConfig] = None,
    trade_source: Optional[TradeSource] = None,
    starting_balance: float = 0.0,
    redis_client=None,
) -> ProtectionManager:
    global _manager
    _manager = ProtectionManager(
        config=config,
        trade_source=trade_source,
        starting_balance=starting_balance,
        redis_client=redis_client,
    )
    return _manager
