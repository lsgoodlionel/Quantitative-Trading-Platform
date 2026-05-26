"""
策略基类

所有回测/实盘策略继承此类，实现 on_bar() 方法。
设计参考: refs/backtrader/backtrader/strategy.py Strategy 的事件驱动接口，
适配为 asyncio 友好的同步回调（回测引擎在单线程内驱动）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.strategy.context import StrategyContext


class StrategyBase(ABC):
    """
    策略抽象基类。

    生命周期:
    1. __init__(params)  — 策略初始化，设置参数
    2. on_start(ctx)     — 回测/实盘启动，可初始化状态
    3. on_bar(ctx)       — 每根 K 线回调，核心逻辑在此
    4. on_stop(ctx)      — 回测/实盘结束，收尾清仓等
    """

    # 子类可覆盖：策略名称和描述（用于 API 显示）
    name: str = "unnamed_strategy"
    description: str = ""

    def __init__(self, params: dict | None = None) -> None:
        self._params: dict = params or {}

    def param(self, key: str, default=None):
        """获取策略参数，带默认值。"""
        return self._params.get(key, default)

    # ── 生命周期钩子 ─────────────────────────────────────────

    def on_start(self, ctx: "StrategyContext") -> None:
        """回测/实盘启动时调用一次。子类可覆盖以初始化状态变量。"""

    @abstractmethod
    def on_bar(self, ctx: "StrategyContext") -> None:
        """
        每根 K 线推送时调用。策略主逻辑在此编写。

        通过 ctx 访问:
        - ctx.bar           当前 bar 数据
        - ctx.history       历史 bar DataFrame
        - ctx.position(sym) 当前持仓
        - ctx.cash          可用现金
        - ctx.buy(sym, qty) / ctx.sell(sym, qty)  下单
        """

    def on_stop(self, ctx: "StrategyContext") -> None:
        """回测/实盘结束时调用一次。子类可覆盖以清仓或打印统计。"""

    # ── 工具方法 ─────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(params={self._params})"
