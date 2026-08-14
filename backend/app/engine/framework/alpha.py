"""AlphaModel 抽象与遗留策略适配器（Wave K-d / K5）

`AlphaModel` 是三段式的第一段：把行情变成 `Insight`。
接口对齐 Lean 的 `IAlphaModel`（Apache-2.0），去掉了本期用不到的 Universe 事件。

`LegacyStrategyAlphaAdapter` 把现有 16 个 preset **原样**包成 AlphaModel：
拦截其 `ctx.buy/sell/short/cover` 调用转成 Insight，策略代码一行不改。
这是验证「三段式抽象是否站得住」的关键用例 —— 如果既有策略必须重写才能接入，
这层抽象就是失败的。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from app.data.models import Market
from app.engine.framework.insight import NEVER_EXPIRES, Insight, InsightDirection
from app.strategy.context import StrategyContext
from app.strategy.precompute import indicator_spec_of, view_from_history

if TYPE_CHECKING:
    from app.engine.backtest.broker import Order
    from app.strategy.base import StrategyBase
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)


class AlphaModel(ABC):
    """行情 → 观点。"""

    name: str = "alpha"

    @abstractmethod
    def update(self, ctx: PortfolioContext) -> list[Insight]:
        """本时点产生的新观点（无观点返回空列表）。"""

    # 下面两个是**可选**生命周期钩子，默认空实现即为正确语义（与
    # StrategyBase.on_start 同理）。加 @abstractmethod 会强制每个 Alpha 都实现，
    # 与设计相悖，故就地豁免 B027。
    def on_start(self, ctx: PortfolioContext) -> None:  # noqa: B027
        """回测/实盘启动时调用一次。子类按需覆盖。"""

    def on_symbols_changed(self, added: list[str], removed: list[str]) -> None:  # noqa: B027
        """标的池变动通知（Universe Selection 落地后由引擎调用）。"""


@dataclass
class _CapturingContext(StrategyContext):
    """
    只记录交易意图、不真正下单的 `StrategyContext`。

    账户状态（`cash` / `qty` / `position`）仍然读**真实**券商，
    否则策略的 `if ctx.qty == 0` 之类的条件会全部失真。
    """

    signals: list[InsightDirection] = field(default_factory=list)

    def buy(self, qty: int, symbol=None, market=None, order_type="MARKET",
            limit_price=None) -> Order | None:
        if qty > 0:
            self.signals.append(InsightDirection.UP)
        return None

    def sell(self, qty: int, symbol=None, market=None, order_type="MARKET",
             limit_price=None) -> Order | None:
        if qty > 0:
            self.signals.append(InsightDirection.FLAT)
        return None

    def sell_all(self, symbol: str | None = None) -> Order | None:
        self.signals.append(InsightDirection.FLAT)
        return None

    def close_all(self, symbol: str | None = None) -> Order | None:
        self.signals.append(InsightDirection.FLAT)
        return None

    def short(self, qty: int, symbol=None, market: Market | None = None) -> Order | None:
        if qty > 0:
            self.signals.append(InsightDirection.DOWN)
        return None

    def cover(self, qty: int, symbol=None, market: Market | None = None,
              exit_reason: str | None = None) -> Order | None:
        if qty > 0:
            self.signals.append(InsightDirection.FLAT)
        return None


class LegacyStrategyAlphaAdapter(AlphaModel):
    """
    把单标的 `StrategyBase` 包成 AlphaModel。

    用法::

        LegacyStrategyAlphaAdapter(MacdStrategy, {"fast": 12})   # 每标的一个实例
        LegacyStrategyAlphaAdapter(MacdStrategy())               # 仅限单标的

    传**类**时会为每个标的各建一个实例 —— preset 普遍在 `self` 上存状态
    （网格、配对的滚动统计等），共用一个实例会让不同标的的状态互相污染。
    传**实例**时只允许单标的，多标的直接报错而不是悄悄共享状态。

    观点有效期默认为「不过期」：传统策略自己负责平仓，观点应当一直有效到
    被反向信号取代为止。给它一个短有效期会让组合在策略还没发出卖出信号时
    就把仓位清掉。
    """

    def __init__(
        self,
        strategy: StrategyBase | type[StrategyBase],
        params: dict | None = None,
        *,
        period: timedelta = NEVER_EXPIRES,
        symbols: list[str] | None = None,
        name: str | None = None,
    ) -> None:
        self._is_class = isinstance(strategy, type)
        if not self._is_class and params:
            raise ValueError("传入策略实例时不能再给 params，请改传策略类")
        self._strategy = strategy
        self._params = params
        self._period = period
        self._symbols = symbols
        self._instances: dict[str, StrategyBase] = {}
        #: E-a：被包策略的指标声明，每标的解析一次（值为 None = 该策略没声明）
        self._specs: dict[str, object] = {}
        self._started: set[str] = set()
        self.name = name or f"legacy:{strategy.name}"

    def update(self, ctx: PortfolioContext) -> list[Insight]:
        targets = [s for s in (self._symbols or ctx.symbols) if s in ctx.bars]
        if not self._is_class and len(targets) > 1:
            raise ValueError(
                f"{self.name} 收到多标的（{targets}）但持有的是策略实例，"
                "状态会互相污染；请改传策略类以便每标的独立实例化"
            )

        insights: list[Insight] = []
        for symbol in targets:
            insights.extend(self._insights_for(ctx, symbol))
        return insights

    def _insights_for(self, ctx: PortfolioContext, symbol: str) -> list[Insight]:
        strategy = self._instance_for(symbol)
        history = ctx.histories[symbol]
        # E-a：被包的 preset 可能声明了预算指标（`ctx.ind`）。这里的指标声明属于
        # **被包的策略**，与外层组合策略的声明无关，所以不能借 `ctx.indicators`
        # —— 只能在这条前缀历史上现算。成本与 preset 改造前相同，且前缀天然无前视。
        capture = _CapturingContext(
            bar=ctx.bars[symbol],
            history=history,
            broker=ctx.broker,
            indicators=view_from_history(self._spec_for(symbol, strategy), history),
        )
        if symbol not in self._started:
            strategy.on_start(capture)
            self._started.add(symbol)

        # 策略异常不在这里吞掉：组合引擎的主循环已经统一捕获并记录，
        # 在这里静默跳过只会得到一份「跑完了但零成交」的假报告。
        strategy.on_bar(capture)

        return [
            Insight(
                symbol=symbol,
                direction=direction,
                period=self._period,
                generated_at=ctx.time,
                source=self.name,
                tag=strategy.name,
            )
            for direction in capture.signals
        ]

    def _spec_for(self, symbol: str, strategy: StrategyBase):
        """指标声明每标的解析一次 —— 逐 bar 重跑 `declare_indicators` 是白搭的分配。"""
        if symbol not in self._specs:
            self._specs[symbol] = indicator_spec_of(strategy)
        return self._specs[symbol]

    def _instance_for(self, symbol: str) -> StrategyBase:
        if not self._is_class:
            return self._strategy          # type: ignore[return-value]
        if symbol not in self._instances:
            self._instances[symbol] = self._strategy(self._params)   # type: ignore[operator]
        return self._instances[symbol]


__all__ = ["AlphaModel", "LegacyStrategyAlphaAdapter"]
