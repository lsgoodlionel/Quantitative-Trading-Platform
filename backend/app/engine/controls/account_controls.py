"""账户级控制器 — Wave L-a / L1

杠杆类约束**事后才能判定**（下单时不知道最终成交价与成交量），
因此这些控制器在每次产生成交之后由券商触发，而不是下单前。

已知边界（契约「不做」清单）：这里只做事后校验，不做事前的买入力/保证金拦截。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.engine.controls.base import (
    ON_ERROR_LOG,
    AccountControl,
    ControlContext,
    ControlViolation,
)


def _is_after(now: datetime, deadline: datetime) -> bool:
    """
    时点比较，容忍 naive / aware 混用。

    回测 bar 时间常带 UTC 时区，而使用方写 deadline 时往往是裸 datetime；
    直接比较会抛 TypeError 把回测炸掉。缺时区的一侧按 UTC 解释。
    """
    if (now.tzinfo is None) != (deadline.tzinfo is None):
        now = now if now.tzinfo else now.replace(tzinfo=UTC)
        deadline = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
    return now > deadline


class MaxLeverage(AccountControl):
    """账户总杠杆上限，成交后校验。"""

    def __init__(self, max_leverage: float, on_error: str = ON_ERROR_LOG) -> None:
        super().__init__(on_error)
        if max_leverage < 0:
            raise ValueError("max_leverage 不能为负")
        self.max_leverage = max_leverage

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        if ctx.leverage > self.max_leverage:
            return self._violation(
                f"账户杠杆 {ctx.leverage:.4f} 超过上限 {self.max_leverage:.4f}"
            )
        return None


class MinLeverage(AccountControl):
    """
    指定时点之后账户杠杆不得低于下限（资金必须真正被用起来）。

    `deadline` 之前一律放行 —— 建仓期杠杆天然偏低，那不是违规。
    """

    def __init__(
        self,
        min_leverage: float,
        deadline: datetime,
        on_error: str = ON_ERROR_LOG,
    ) -> None:
        super().__init__(on_error)
        if min_leverage < 0:
            raise ValueError("min_leverage 不能为负")
        self.min_leverage = min_leverage
        self.deadline = deadline

    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        if not _is_after(ctx.now, self.deadline):
            return None
        if ctx.leverage < self.min_leverage:
            return self._violation(
                f"{self.deadline.date()} 之后账户杠杆 {ctx.leverage:.4f} "
                f"低于下限 {self.min_leverage:.4f}"
            )
        return None
