"""
策略基类

所有回测/实盘策略继承此类，实现 on_bar() 方法。
设计参考: refs/backtrader/backtrader/strategy.py Strategy 的事件驱动接口，
适配为 asyncio 友好的同步回调（回测引擎在单线程内驱动）。

> **许可证**：K4 风险闸门（L-b）与 L4 仓位调整 / L5 下单确认钩子的**语义**
> 设计参考自 freqtrade（GPL-3.0）的 `IStrategy`。此处仅阅读其设计思路后
> **独立实现**，未复制其任何代码。QuantBot 不受 GPL 传染。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.core.errors import StrategyContractError
from app.engine.backtest.trade import ExitRules

#: re-export：策略作者从 `app.strategy.base` 拿更自然。
#: 定义放在 `app/core/errors.py` 这个中立位置，是为了避免引擎↔策略成环
#: —— 引擎侧的 `broker_order_hooks` 也要抛它，而它不能 import `app.strategy`。
__all__ = ["ORDER_HOOK_NAMES", "PortfolioStrategyBase", "StrategyBase", "StrategyContractError"]

if TYPE_CHECKING:
    from app.engine.backtest.trade import Trade
    from app.strategy.context import PortfolioContext, StrategyContext

#: L5 的四个下单钩子。`has_order_hooks()` 用它判断策略是否覆盖过其中任何一个
ORDER_HOOK_NAMES = (
    "confirm_entry",
    "confirm_exit",
    "custom_entry_price",
    "custom_exit_price",
)


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

    # ── K4 类级声明式退出配置（全部不配置 = 现状行为，风险闸门为 no-op）──
    #
    # 语义定义见 app/engine/backtest/trade.py（设计参考自 freqtrade，独立实现）。
    stoploss: float | None = None                      # -0.10 = 本笔亏 10% 离场
    trailing_stop: bool = False
    trailing_stop_positive: float | None = None        # 追踪回撤距离
    trailing_stop_positive_offset: float = 0.0         # 峰值达到此值才启用追踪
    minimal_roi: dict[int, float] | None = None        # {持仓分钟数: 目标收益率}

    # ── L4 仓位调整（默认关闭 = 整条路径 no-op）────────────────
    #: 是否允许在已有持仓上追加/减少。关掉时引擎完全不进入调整循环
    position_adjustment_enable: bool = False
    #: 单笔交易最多允许的调整次数（防止策略把一笔交易无限加仓）
    max_position_adjustments: int = 10

    # ── L5 挂单超时（None = 不超时，沿用 TimeInForce）──────────
    #: 开仓挂单多少分钟未成交即撤单。与 TimeInForce **同时配置时取更早者**
    entry_timeout_minutes: int | None = None
    #: 平仓挂单多少分钟未成交即撤单。与 TimeInForce **同时配置时取更早者**
    exit_timeout_minutes: int | None = None

    def __init__(self, params: dict | None = None) -> None:
        self._params: dict = params or {}

    def param(self, key: str, default=None):
        """获取策略参数，带默认值。"""
        return self._params.get(key, default)

    # ── 生命周期钩子 ─────────────────────────────────────────

    def on_start(self, ctx: StrategyContext) -> None:
        """回测/实盘启动时调用一次。子类可覆盖以初始化状态变量。"""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> None:
        """
        每根 K 线推送时调用。策略主逻辑在此编写。

        通过 ctx 访问:
        - ctx.bar           当前 bar 数据
        - ctx.history       历史 bar DataFrame
        - ctx.position(sym) 当前持仓
        - ctx.cash          可用现金
        - ctx.buy(sym, qty) / ctx.sell(sym, qty)  下单
        """

    def on_stop(self, ctx: StrategyContext) -> None:
        """回测/实盘结束时调用一次。子类可覆盖以清仓或打印统计。"""

    # ── K4 风险闸门 ──────────────────────────────────────────

    def exit_rules(self) -> ExitRules:
        """把类级配置收敛成一个不可变的规则对象，供引擎在 `on_bar` 之前判定。"""
        return ExitRules(
            stoploss=self.stoploss,
            trailing_stop=self.trailing_stop,
            trailing_stop_positive=self.trailing_stop_positive,
            trailing_stop_positive_offset=self.trailing_stop_positive_offset,
            minimal_roi=self.minimal_roi,
        )

    def custom_stoploss(
        self, ctx: StrategyContext | PortfolioContext, trade: Trade, current_profit: float
    ) -> float | None:
        """
        运行期止损覆盖。返回 None = 沿用类级 `stoploss`；返回负数 = 本次改用该阈值。

        典型用法：按 ATR 或持仓时长动态收紧止损。
        """
        return None

    def custom_roi(
        self, ctx: StrategyContext | PortfolioContext, trade: Trade, current_profit: float
    ) -> float | None:
        """运行期 ROI 覆盖。返回 None = 沿用类级 `minimal_roi` 阶梯。"""
        return None

    # ── L4 仓位调整钩子 ──────────────────────────────────────

    def adjust_position(
        self,
        ctx: StrategyContext | PortfolioContext | None,
        trade: Trade,
        current_profit: float,
    ) -> float | None:
        """
        对一笔**已开仓**的交易追加或减少仓位。返回本次调整的**增量金额**
        （币种同 `initial_cash`）：

        - 正数 = 加仓（DCA）
        - 负数 = 部分平仓
        - None（默认）= 不调整

        返回负数且绝对值 >= 当前持仓市值时视为全平，绝不产生反向持仓。

        仅在 `position_adjustment_enable=True` 时被调用，且调用点在
        **风险闸门之后、`on_bar` 之前** —— 已经该止损的仓位不会再被加仓。
        单笔交易的调整次数受 `max_position_adjustments` 限制，超限会被忽略并记
        WARNING（不会静默丢弃）。

        `ctx` 在该标的本时点停牌（没有 bar）时为 None。
        """
        return None

    # ── L5 下单确认与价格自定义钩子 ──────────────────────────

    def confirm_entry(
        self,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        qty: int,
        price: float,
        entry_tag: str | None,
    ) -> bool:
        """
        开仓单最后一道确认。返回 False 否决本次开仓（不会产生任何 Fill）。默认 True。

        `price` 是**最近一次已知收盘价**（引擎尚未推进任何 bar 时为 0.0），
        不是成交价 —— 订单仍按 next-bar 语义撮合。
        """
        return True

    def confirm_exit(
        self,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        qty: int,
        price: float,
        exit_reason: str,
    ) -> bool:
        """
        平仓单最后一道确认。返回 False 否决本次平仓。默认 True。

        ⚠️ **否决风险闸门产生的平仓（stop_loss / roi / trailing_stop_loss）是危险
        操作**：止损被否决后仓位会一直留着，风险闸门下一根 bar 会再次尝试、再次
        被否决，仓位可能一路亏到底。引擎会为这种否决打 WARNING 日志（同一标的
        同一原因只在首次用 WARNING，之后降为 DEBUG，避免逐 bar 刷屏）。
        除非确知自己在做什么，否则不要否决 `RISK_EXIT_REASONS` 内的平仓。
        """
        return True

    def custom_entry_price(
        self,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        proposed: float,
    ) -> float | None:
        """
        自定义开仓价。返回 None（默认）= 沿用 `proposed`，订单类型不变。

        返回一个正数意味着「我要在这个价位成交」，引擎会把订单**转成 LIMIT 挂单**
        （`order_type=LIMIT` + `limit_price=返回值`），而不是用该价格立即市价成交
        —— 后者等于偷看未来价。返回非正数直接抛错，不静默降级。
        """
        return None

    def custom_exit_price(
        self,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        proposed: float,
        exit_reason: str,
    ) -> float | None:
        """自定义平仓价。语义同 `custom_entry_price`（同样产出 LIMIT 单）。"""
        return None

    def has_order_hooks(self) -> bool:
        """
        本策略是否覆盖了任一 L5 钩子 / 超时配置。

        引擎用它做零开销快速路径：全部为默认时**根本不把钩子绑到券商上**，
        `submit_order` 的热路径只多一次 `is None` 判断。
        """
        if self.entry_timeout_minutes is not None or self.exit_timeout_minutes is not None:
            return True
        cls = type(self)
        return any(
            getattr(cls, name, None) is not getattr(StrategyBase, name)
            for name in ORDER_HOOK_NAMES
        )

    # ── 工具方法 ─────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(params={self._params})"


class PortfolioStrategyBase(StrategyBase):
    """
    多标的组合策略基类（Wave K-c / K1）。

    与 `StrategyBase` 的唯一区别：回调是**每个时点一次**（`on_bars`）而不是
    每标的一次。组合调仓天然需要同时看到所有标的，逐标的回调无法表达
    「卖掉 A 去买 B」这类跨标的决策。

    生命周期钩子 `on_start` / `on_stop` 收到的同样是 `PortfolioContext`。
    """

    name: str = "unnamed_portfolio_strategy"

    @abstractmethod
    def on_bars(self, ctx: PortfolioContext) -> None:
        """每个时点调用一次。ctx.bars 只含本时点有行情的标的。"""

    def on_bar(self, ctx: StrategyContext) -> None:
        """
        组合策略不走单标的回调。这里显式抛错而非静默 no-op —— 一个被误当作
        单标的策略跑起来的组合策略会安静地不下任何单，那种「回测跑完但零成交」
        的假象比直接报错难查得多。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 是组合策略，请用 PortfolioBacktestEngine 驱动 on_bars()"
        )
