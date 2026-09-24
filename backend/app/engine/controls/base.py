#
# Copyright 2014 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# 本文件的控制器抽象改编自 zipline 的 `zipline/finance/controls.py`（Apache-2.0）。
# 改动：validate() 由「命中即抛错」改为「返回 ControlViolation」，
# 使同一个控制器实例既能用于回测（拒单）也能用于实盘（拒单 + 事件）。
"""交易控制器基础抽象 — Wave L-a / L1

本模块的**唯一存在理由**是让回测与实盘共用同一套控制器：

- 控制器 **不持有** broker / OMS 引用，全部输入经 `ControlContext` 传入；
- 判定逻辑是纯函数，同一实例 + 等价 context 在两条路径上给出相同结果；
- 执行由共享的 `run_controls()` 完成，`on_error` 语义因此也只有一份实现。

契约：`docs/contracts/waveLa-trading-controls.md`
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from app.data.models import Market

if TYPE_CHECKING:
    from app.engine.backtest.order_types import OrderType


def _default_order_type() -> OrderType:
    """延迟到实例化时才取 `OrderType.MARKET`。

    运行期不能在模块顶层 import：`app.engine.backtest.order_types` 会先执行
    `app.engine.backtest.__init__`，后者 import `broker.py`，而 broker 又
    import 本模块 —— 成环，`import app.engine.controls` 单独跑就 ImportError。
    见 `tests/test_import_cycles.py`。
    """
    from app.engine.backtest.order_types import OrderType

    return OrderType.MARKET

logger = logging.getLogger(__name__)

#: 命中控制器时抛错中断（回测直接崩、实盘抛给调用方）
ON_ERROR_FAIL = "fail"
#: 命中控制器时记警告并拒掉这一单，流程继续（默认）
ON_ERROR_LOG = "log"

_VALID_ON_ERROR = frozenset({ON_ERROR_FAIL, ON_ERROR_LOG})

#: 买方向标识（`ControlContext.side` 取值，与回测/实盘的 side 枚举 value 一致）
SIDE_BUY = "BUY"


@dataclass(frozen=True)
class ControlViolation:
    """一次控制器拦截的结果（不可变）。"""

    control: str   # 控制器名，用于日志与拒单原因
    reason: str    # 面向用户的中文说明

    def as_reject_reason(self) -> str:
        """统一的拒单原因格式，回测与实盘两侧共用。"""
        return f"[{self.control}] {self.reason}"


@dataclass(frozen=True)
class ControlContext:
    """
    回测与实盘的**公共视图** —— 共用同一套控制器的关键。

    字段全部带默认值，因为账户级校验（`AccountControl`）没有「这一单」可言，
    只会填 now / portfolio_value / cash / leverage 几项。各控制器只读取
    自己关心的字段，其余保持默认不影响判定。

    `portfolio_value` / `cash` 目前没有任何内置控制器读取，保留是为了让
    自定义控制器不必再改这个 dataclass；实盘侧未接账户快照时为 0.0。
    """

    symbol: str = ""
    market: Market | None = None
    side: str = SIDE_BUY                      # "BUY" / "SELL"
    qty: int = 0
    order_type: OrderType = field(default_factory=_default_order_type)
    limit_price: float | None = None
    price: float | None = None                # 参考价（回测用最后收盘价，实盘用最新报价）
    now: datetime = datetime.min
    current_qty: int = 0                      # 该标的当前持仓（带符号）
    portfolio_value: float = 0.0
    cash: float = 0.0
    orders_today: int = 0                     # 当日已下单数（不含本单）
    leverage: float = 0.0                     # 当前总杠杆

    @property
    def signed_qty(self) -> int:
        """本单对持仓的带符号影响：BUY 为正，SELL 为负。"""
        return self.qty if self.side.upper() == SIDE_BUY else -self.qty

    @property
    def projected_qty(self) -> int:
        """本单成交后的持仓（带符号）。"""
        return self.current_qty + self.signed_qty

    @property
    def reference_price(self) -> float | None:
        """
        金额类校验使用的参考价。

        优先用行情参考价；取不到时退回委托价（限价单下这是可执行价的上界/下界，
        比直接放弃校验更接近真实金额）。两者都没有时返回 None，
        由控制器显式拒单而不是静默放行。
        """
        return self.price if self.price is not None else self.limit_price


class TradingControlViolationError(Exception):
    """`on_error="fail"` 的控制器命中时抛出。"""

    def __init__(self, violation: ControlViolation) -> None:
        super().__init__(violation.as_reject_reason())
        self.violation = violation


class _ControlBase(ABC):
    """TradingControl / AccountControl 的公共部分（on_error 校验与命名）。"""

    def __init__(self, on_error: str = ON_ERROR_LOG) -> None:
        if on_error not in _VALID_ON_ERROR:
            raise ValueError(
                f"on_error 只能是 {ON_ERROR_FAIL!r} 或 {ON_ERROR_LOG!r}，收到 {on_error!r}"
            )
        self.on_error = on_error

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def validate(self, ctx: ControlContext) -> ControlViolation | None:
        """返回 None 表示放行，返回 `ControlViolation` 表示拦截。"""

    def _violation(self, reason: str) -> ControlViolation:
        return ControlViolation(control=self.name, reason=reason)

    def __repr__(self) -> str:
        return f"{self.name}(on_error={self.on_error!r})"


class TradingControl(_ControlBase):
    """下单前校验。返回 None 表示放行，返回 ControlViolation 表示拦截。"""


class AccountControl(_ControlBase):
    """账户级校验，在每次成交后检查（杠杆类约束事后才能判定）。"""


def validate_size_limits(max_shares: int | None, max_notional: float | None) -> None:
    """`max_shares` / `max_notional` 的公共参数校验（构造期快速失败）。"""
    if max_shares is None and max_notional is None:
        raise ValueError("max_shares 与 max_notional 至少要给一个")
    if max_shares is not None and max_shares < 0:
        raise ValueError("max_shares 不能为负")
    if max_notional is not None and max_notional < 0:
        raise ValueError("max_notional 不能为负")


def run_controls(
    controls: Sequence[_ControlBase], ctx: ControlContext
) -> ControlViolation | None:
    """
    按顺序执行控制器，**首个命中即短路返回**。

    回测与实盘都走这一个函数 —— `on_error` 语义只有一份实现，
    因此两条路径不可能对同一个控制器给出不同判定。

    `on_error="fail"` 抛 `TradingControlViolationError`（调用方不捕获即中断）；
    `on_error="log"` 记 warning 后把违规返回给调用方去拒单。
    """
    for control in controls:
        violation = control.validate(ctx)
        if violation is None:
            continue
        if control.on_error == ON_ERROR_FAIL:
            raise TradingControlViolationError(violation)
        logger.warning(
            "控制器拦截：%s %s %s 股 → %s",
            ctx.side, ctx.symbol or "-", ctx.qty, violation.as_reject_reason(),
        )
        return violation
    return None
