"""持仓维度的控制器 — Wave L-a / L1

判定口径统一为「**本单成交后**的持仓」（`ctx.projected_qty`），
与 zipline 的 `MaxPositionSize` / `LongOnly` 一致：控制的是结果而不是委托本身。
"""

from __future__ import annotations

from app.engine.controls.base import (
    ON_ERROR_LOG,
    ControlContext,
    ControlViolation,
    TradingControl,
    validate_size_limits,
)


class MaxPositionSize(TradingControl):
    """
    单标的持仓上限（含本单成交后）。

    与 `MaxOrderSize` 一样：配了金额上限却拿不到参考价时拒单，不静默放行。
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
        projected = abs(ctx.projected_qty)
        if self.max_shares is not None and projected > self.max_shares:
            return self._violation(
                f"{ctx.symbol} 成交后持仓 {projected} 股超过上限 {self.max_shares} 股"
            )
        if self.max_notional is None:
            return None

        price = ctx.reference_price
        if price is None:
            return self._violation(
                f"配置了持仓金额上限 {self.max_notional:.2f} 但缺少参考价，无法校验，按拒单处理"
            )
        notional = projected * price
        if notional > self.max_notional:
            return self._violation(
                f"{ctx.symbol} 成交后持仓金额 {notional:.2f} 超过上限 {self.max_notional:.2f}"
            )
        return None


class LongOnly(TradingControl):
    """
    禁止净持仓为负。

    与券商的 `allow_short` **正交**：`allow_short=True` 保留做空*能力*，
    是否允许开空由本控制器决定。因此两者同时生效时开空仍会被拦下。
    """

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        projected = ctx.projected_qty
        if projected < 0:
            return self._violation(
                f"{ctx.symbol} 成交后净持仓为 {projected} 股（禁止做空）"
            )
        return None
