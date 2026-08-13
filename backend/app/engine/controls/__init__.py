"""交易控制器 — Wave L-a / L1

回测（`SimulatedBroker.submit_order`）与实盘（`OrderManager.submit_order`）
共用同一批控制器实例，判定逻辑与 `on_error` 语义各只有一份实现。

用法::

    from app.engine.controls import LongOnly, MaxOrderSize

    cfg = BacktestConfig(controls=[MaxOrderSize(max_shares=1000), LongOnly()])
    oms = OrderManager(controls=[MaxOrderSize(max_shares=1000), LongOnly()])
"""

from app.engine.controls.account_controls import MaxLeverage, MinLeverage
from app.engine.controls.base import (
    ON_ERROR_FAIL,
    ON_ERROR_LOG,
    AccountControl,
    ControlContext,
    ControlViolation,
    TradingControl,
    TradingControlViolationError,
    run_controls,
)
from app.engine.controls.order_controls import (
    AssetDateBounds,
    MaxOrderCount,
    MaxOrderSize,
    RestrictedList,
)
from app.engine.controls.position_controls import LongOnly, MaxPositionSize

__all__ = [
    "ON_ERROR_FAIL",
    "ON_ERROR_LOG",
    "AccountControl",
    "AssetDateBounds",
    "ControlContext",
    "ControlViolation",
    "LongOnly",
    "MaxLeverage",
    "MaxOrderCount",
    "MaxOrderSize",
    "MaxPositionSize",
    "MinLeverage",
    "RestrictedList",
    "TradingControl",
    "TradingControlViolationError",
    "run_controls",
]
