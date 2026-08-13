"""单笔委托维度的控制器 — Wave L-a / L1

语义改编自 zipline `zipline/finance/controls.py`（Apache-2.0，版权头见 `base.py`）。
全部为纯函数式：只读 `ControlContext`，不持有 broker / OMS。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from app.engine.controls.base import (
    ON_ERROR_LOG,
    ControlContext,
    ControlViolation,
    TradingControl,
    validate_size_limits,
)


class MaxOrderCount(TradingControl):
    """单日下单数上限。当日已下单数由调用方（券商 / OMS）经 context 传入。"""

    def __init__(self, max_count: int, on_error: str = ON_ERROR_LOG) -> None:
        super().__init__(on_error)
        if max_count < 0:
            raise ValueError("max_count 不能为负")
        self.max_count = max_count

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        if ctx.orders_today >= self.max_count:
            return self._violation(
                f"当日下单数已达上限 {self.max_count}（今日已下 {ctx.orders_today} 单）"
            )
        return None


class MaxOrderSize(TradingControl):
    """
    单笔委托的数量 / 金额上限。

    配置了 `max_notional` 却拿不到参考价时**拒单**而不是放行 ——
    金额上限是风控约束，无法校验时静默放行等于没配。
    """

    def __init__(
        self,
        max_shares: int | None = None,
        max_notional: float | None = None,
        on_error: str = ON_ERROR_LOG,
    ) -> None:
        super().__init__(on_error)
        validate_size_limits(max_shares, max_notional)
        self.max_shares = max_shares
        self.max_notional = max_notional

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        shares = abs(ctx.qty)
        if self.max_shares is not None and shares > self.max_shares:
            return self._violation(f"单笔数量 {shares} 超过上限 {self.max_shares}")
        if self.max_notional is None:
            return None

        price = ctx.reference_price
        if price is None:
            return self._violation(
                f"配置了单笔金额上限 {self.max_notional:.2f} 但缺少参考价，无法校验，按拒单处理"
            )
        notional = shares * price
        if notional > self.max_notional:
            return self._violation(
                f"单笔金额 {notional:.2f} 超过上限 {self.max_notional:.2f}"
            )
        return None


class RestrictedList(TradingControl):
    """黑名单标的：命中即拒单。"""

    def __init__(
        self, restricted: Iterable[str], on_error: str = ON_ERROR_LOG
    ) -> None:
        super().__init__(on_error)
        # 复制成 frozenset：外部后续改动传入的集合不会偷偷改变控制器行为
        self.restricted = frozenset(restricted)

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        if ctx.symbol in self.restricted:
            return self._violation(f"{ctx.symbol} 在黑名单中，禁止交易")
        return None


class AssetDateBounds(TradingControl):
    """
    标的上市 / 退市边界：不在 [start, end] 区间内的下单一律拒绝。

    未登记边界的标的不受约束（缺数据不等于该拒单）。
    """

    def __init__(
        self,
        bounds: Mapping[str, tuple[date, date]],
        on_error: str = ON_ERROR_LOG,
    ) -> None:
        super().__init__(on_error)
        for symbol, window in bounds.items():
            start, end = window
            if start > end:
                raise ValueError(f"{symbol} 的上市日 {start} 晚于退市日 {end}")
        self.bounds = dict(bounds)

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        window = self.bounds.get(ctx.symbol)
        if window is None:
            return None
        start, end = window
        today = ctx.now.date()
        if today < start:
            return self._violation(f"{ctx.symbol} 于 {start} 才上市，{today} 不可交易")
        if today > end:
            return self._violation(f"{ctx.symbol} 已于 {end} 退市，{today} 不可交易")
        return None
