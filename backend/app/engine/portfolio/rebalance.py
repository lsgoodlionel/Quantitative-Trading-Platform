"""
组合再平衡计划（V3 Wave A-b · G2）

**唯一一份**「目标权重 → 股数级增量订单」算法：
- 回测：`PortfolioContext.target_weight()` 调用本模块
- 实盘：`/api/v1/portfolio/rebalance/preview` 调用本模块

刻意做成不依赖 context 的纯函数 —— 只吃 dict，不吃 broker/engine，
这样回测与实盘不会漂移出两套取整/排序规则。

与既有 `app.risk.portfolio.compute_rebalance()` 的分工：
那个算**金额**（`delta_value`）供展示，本模块算**股数**供真实下单。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 权重和校验容差（浮点求和误差；1/3 三等分累加约 1e-16 量级）
WEIGHT_SUM_TOLERANCE = 1e-6

#: 单笔权重的合法上限（含做空），超出视为输入错误而非杠杆意图
_MAX_ABS_WEIGHT = 10.0

_REASON_OPEN = "open"
_REASON_CLOSE = "close"
_REASON_INCREASE = "increase"
_REASON_DECREASE = "decrease"


@dataclass(frozen=True)
class RebalanceLeg:
    """单个标的的调仓腿。frozen —— 计划一旦算出就不该被下游改写。"""

    symbol: str
    current_qty: int
    target_qty: int
    delta_qty: int          # 正 = 买入，负 = 卖出
    price: float
    delta_value: float
    reason: str             # open / close / increase / decrease

    @property
    def side(self) -> str:
        """委托方向。delta_qty 恒不为 0（为 0 的腿不会出现在计划里）。"""
        return "BUY" if self.delta_qty > 0 else "SELL"

    @property
    def abs_qty(self) -> int:
        return abs(self.delta_qty)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_qty": self.current_qty,
            "target_qty": self.target_qty,
            "delta_qty": self.delta_qty,
            "price": round(self.price, 6),
            "delta_value": round(self.delta_value, 2),
            "reason": self.reason,
            "side": self.side,
        }


def validate_target_weights(
    weights: Mapping[str, float],
    *,
    allow_short: bool = False,
    tolerance: float = WEIGHT_SUM_TOLERANCE,
) -> None:
    """
    校验目标权重，不合法直接抛 `ValueError`。

    刻意**不做静默归一化** —— 权重和不是 1 通常意味着调用方漏传了标的，
    悄悄归一化会把「漏了一只票」变成「其余票各自超配」，在实盘上是真金白银。

    注：本函数供 API 边界使用；回测侧 `target_weight()` 保留自身较宽松的
    历史校验（允许留现金，仅在总敞口 > 1 时告警），两者刻意不合并。
    """
    if not weights:
        raise ValueError("目标权重不能为空")

    illegal = sorted(s for s, w in weights.items() if not math.isfinite(w))
    if illegal:
        raise ValueError(f"非法权重（NaN/Inf）: {illegal}")

    oversized = sorted(s for s, w in weights.items() if abs(w) > _MAX_ABS_WEIGHT)
    if oversized:
        raise ValueError(f"权重绝对值超过 {_MAX_ABS_WEIGHT}: {oversized}")

    if not allow_short:
        negative = sorted(s for s, w in weights.items() if w < 0)
        if negative:
            raise ValueError(f"出现负权重 {negative}，但未开启做空（allow_short）")

    total = math.fsum(weights.values())
    if abs(total - 1.0) > tolerance:
        raise ValueError(
            f"权重和必须为 1，实际为 {total:.6f}；"
            "请补齐缺失标的或显式传入权重 0，本接口不做静默归一化"
        )


def plan_rebalance(
    target_weights: Mapping[str, float],
    current_qty: Mapping[str, int],
    prices: Mapping[str, float],
    portfolio_value: float,
    *,
    min_trade_value: float = 0.0,
    lot_size: int = 1,
) -> list[RebalanceLeg]:
    """
    目标权重 → 增量调仓腿列表。

    目标股数 = 组合净值 × 权重 / 价格，向零取整到 `lot_size` 整数倍；
    委托量 = 目标 - 当前持仓。已有 100 股、目标 150 股 ⇒ +50 股。

    只处理出现在 `target_weights` 里的标的：**不会**自动清掉未列出的持仓
    （与回测 `target_weight()` 语义一致；调用方要清仓请显式传权重 0）。

    返回顺序固定为**先卖后买**（各自按 symbol 升序），
    让卖出释放的现金能被同一批买单用上。
    """
    if lot_size < 1:
        raise ValueError(f"lot_size 必须 ≥ 1，收到 {lot_size}")
    if min_trade_value < 0:
        raise ValueError(f"min_trade_value 不能为负，收到 {min_trade_value}")

    sells: list[RebalanceLeg] = []
    buys: list[RebalanceLeg] = []

    for symbol in sorted(target_weights):
        leg = _build_leg(
            symbol=symbol,
            weight=target_weights[symbol],
            held=int(current_qty.get(symbol, 0)),
            price=prices.get(symbol),
            portfolio_value=portfolio_value,
            min_trade_value=min_trade_value,
            lot_size=lot_size,
        )
        if leg is None:
            continue
        (buys if leg.delta_qty > 0 else sells).append(leg)

    return sells + buys


def _build_leg(
    *,
    symbol: str,
    weight: float,
    held: int,
    price: float | None,
    portfolio_value: float,
    min_trade_value: float,
    lot_size: int,
) -> RebalanceLeg | None:
    """构造单条腿；无需交易或无有效价格时返回 None。"""
    if price is None or price <= 0 or not math.isfinite(price):
        logger.warning("%s 无可用价格（%r），再平衡跳过该标的", symbol, price)
        return None

    target_qty = _truncate_to_lot(portfolio_value * weight / price, lot_size)
    delta_qty = target_qty - held
    if delta_qty == 0:
        return None

    delta_value = delta_qty * price
    if abs(delta_value) < min_trade_value:
        logger.debug(
            "%s 调仓金额 %.2f 低于门槛 %.2f，跳过", symbol, delta_value, min_trade_value
        )
        return None

    return RebalanceLeg(
        symbol=symbol,
        current_qty=held,
        target_qty=target_qty,
        delta_qty=delta_qty,
        price=price,
        delta_value=delta_value,
        reason=_classify(held, target_qty),
    )


def _truncate_to_lot(raw_qty: float, lot_size: int) -> int:
    """向零取整到整手。`lot_size=1` 时等价于 `int(raw_qty)`（保守暴露）。"""
    return int(raw_qty / lot_size) * lot_size


def _classify(current_qty: int, target_qty: int) -> str:
    if current_qty == 0:
        return _REASON_OPEN
    if target_qty == 0:
        return _REASON_CLOSE
    return _REASON_INCREASE if abs(target_qty) > abs(current_qty) else _REASON_DECREASE
