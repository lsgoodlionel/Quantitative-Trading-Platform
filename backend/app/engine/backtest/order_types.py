"""订单类型体系 — K3

把「一张订单在某根 bar 上是否成交、成交在什么价位」这件事从券商里剥出来，
做成无状态的纯函数，便于单测与后续组合引擎复用。

设计参考 Lean（Apache-2.0）的 OrderType / TimeInForce 语义，独立实现。

防未来函数（look-ahead）三条铁律：
1. 判定只用「订单挂出之后」那根 bar 的 OHLC —— 由调用方保证（next-bar 撮合）。
2. 触发判定用 high / low，不用 close 反推 open。
3. 成交价一律取「开盘价」与「委托价」中对下单方更不利的一侧：
   跳空越过委托价时以开盘价成交，而不是白拿委托价的便宜。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.data.models import Bar


class OrderType(str, Enum):
    MARKET = "MARKET"                    # 现状行为：下一根 bar 开盘价成交
    LIMIT = "LIMIT"                      # 价格触及 limit_price 才成交
    STOP_MARKET = "STOP_MARKET"          # 触发后转市价
    STOP_LIMIT = "STOP_LIMIT"            # 触发后转限价
    LIMIT_IF_TOUCHED = "LIMIT_IF_TOUCHED"  # STOP_LIMIT 的镜像：反向触发后转限价
    TRAILING_STOP = "TRAILING_STOP"      # 跟随极值回撤触发
    MARKET_ON_OPEN = "MARKET_ON_OPEN"    # 显式开盘价（= MARKET，语义更清晰）
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"  # 当根 bar 收盘价成交


class TimeInForce(str, Enum):
    GTC = "GTC"   # 一直有效（默认，= 现状挂单不过期行为）
    DAY = "DAY"   # 当日有效，跨日未成交则撤单
    GTD = "GTD"   # 指定日期前有效


#: 需要 stop_price（或由 trailing 推导）的类型
STOP_ORDER_TYPES = frozenset(
    {OrderType.STOP_MARKET, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP}
)
#: 触发之后按限价撮合的类型
LIMIT_ORDER_TYPES = frozenset(
    {OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED}
)


@dataclass(frozen=True)
class MatchResult:
    """一次撮合判定的结果（不可变）。"""

    filled: bool
    price: float | None = None
    #: STOP 类订单在本根 bar 结束时的触发状态（供调用方回写订单）
    triggered: bool = False


def validate_order_spec(
    order_type: OrderType,
    limit_price: float | None,
    stop_price: float | None,
    trailing_pct: float | None,
    trigger_price: float | None = None,
) -> str | None:
    """
    校验订单参数是否自洽。返回错误描述；None 表示合法。

    在系统边界快速失败，避免把半成品订单塞进挂单队列后才在撮合时崩溃。

    `trigger_price` 是后加的 LIMIT_IF_TOUCHED 参数，放在最后并带默认值，
    既有的四参数调用点不受影响。
    """
    if order_type in LIMIT_ORDER_TYPES and limit_price is None:
        return f"{order_type.value} 订单必须提供 limit_price"
    if order_type is OrderType.STOP_MARKET and stop_price is None:
        return "STOP_MARKET 订单必须提供 stop_price"
    if order_type is OrderType.STOP_LIMIT and stop_price is None:
        return "STOP_LIMIT 订单必须提供 stop_price"
    if order_type is OrderType.LIMIT_IF_TOUCHED and trigger_price is None:
        return "LIMIT_IF_TOUCHED 订单必须提供 trigger_price"
    if order_type is OrderType.TRAILING_STOP:
        if trailing_pct is None or trailing_pct <= 0 or trailing_pct >= 1:
            return "TRAILING_STOP 订单的 trailing_pct 必须落在 (0, 1) 区间"
    if limit_price is not None and limit_price <= 0:
        return "limit_price 必须为正数"
    if stop_price is not None and stop_price <= 0:
        return "stop_price 必须为正数"
    if trigger_price is not None and trigger_price <= 0:
        return "trigger_price 必须为正数"
    return None


def seed_trailing_extreme(extreme: float | None, bar_open: float) -> float:
    """
    首次看到订单时用「本根 bar 的开盘价」播种极值。

    用开盘价而不是 high/low 播种，是因为开盘价在 bar 开始时即已知，
    用它做首根 bar 的触发判定不构成偷看。
    """
    return bar_open if extreme is None else extreme


def update_trailing_extreme(extreme: float, bar: Bar, is_buy: bool) -> float:
    """
    用本根 bar 的极值刷新跟踪极点（在本根 bar 判定完成之后调用）。

    is_buy=True（空头止损）跟踪最低价；is_buy=False（多头止损）跟踪最高价。
    """
    return min(extreme, bar.low) if is_buy else max(extreme, bar.high)


def trailing_trigger_price(extreme: float, trailing_pct: float, is_buy: bool) -> float:
    """由极值与回撤比例推导触发价。"""
    return extreme * (1 + trailing_pct) if is_buy else extreme * (1 - trailing_pct)


def match_order(
    order_type: OrderType,
    *,
    is_buy: bool,
    bar: Bar,
    limit_price: float | None = None,
    stop_price: float | None = None,
    trigger_price: float | None = None,
    triggered: bool = False,
) -> MatchResult:
    """
    判定订单在这根 bar 上是否成交，以及成交价（未经滑点）。

    TRAILING_STOP 由调用方先把极值折算成 stop_price 再传入，
    因此这里与 STOP_MARKET 共用一条判定分支。
    """
    if order_type in (OrderType.MARKET, OrderType.MARKET_ON_OPEN):
        return MatchResult(filled=True, price=bar.open, triggered=True)
    if order_type is OrderType.MARKET_ON_CLOSE:
        return MatchResult(filled=True, price=bar.close, triggered=True)
    if order_type is OrderType.LIMIT:
        return _match_limit(is_buy, bar, limit_price, triggered=True)
    if order_type in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP):
        return _match_stop_market(is_buy, bar, stop_price)
    if order_type is OrderType.STOP_LIMIT:
        return _match_stop_limit(is_buy, bar, limit_price, stop_price, triggered)
    if order_type is OrderType.LIMIT_IF_TOUCHED:
        return _match_limit_if_touched(
            is_buy, bar, limit_price, trigger_price, triggered
        )
    raise ValueError(f"未支持的订单类型: {order_type}")


def _match_limit(
    is_buy: bool, bar: Bar, limit_price: float | None, *, triggered: bool
) -> MatchResult:
    if limit_price is None:
        raise ValueError("LIMIT 撮合缺少 limit_price")
    if is_buy:
        if bar.low > limit_price:
            return MatchResult(filled=False, triggered=triggered)
        return MatchResult(filled=True, price=min(bar.open, limit_price), triggered=triggered)
    if bar.high < limit_price:
        return MatchResult(filled=False, triggered=triggered)
    return MatchResult(filled=True, price=max(bar.open, limit_price), triggered=triggered)


def _match_stop_market(is_buy: bool, bar: Bar, stop_price: float | None) -> MatchResult:
    if stop_price is None:
        raise ValueError("STOP 撮合缺少 stop_price")
    if is_buy:
        if bar.high < stop_price:
            return MatchResult(filled=False, triggered=False)
        return MatchResult(filled=True, price=max(bar.open, stop_price), triggered=True)
    if bar.low > stop_price:
        return MatchResult(filled=False, triggered=False)
    return MatchResult(filled=True, price=min(bar.open, stop_price), triggered=True)


def _match_stop_limit(
    is_buy: bool,
    bar: Bar,
    limit_price: float | None,
    stop_price: float | None,
    triggered: bool,
) -> MatchResult:
    """
    先按 STOP 判触发，触发后当作 LIMIT 处理（同根 bar 内可连续触发 + 成交）。

    已知简化：触发之后的限价判定用的是**整根 bar** 的 high/low，而不是触发时点
    之后那一小段。日线粒度下拿不到 bar 内时序，这是所有 bar 级回测的共同妥协；
    需要严格无偷看时应改用分钟级 bar。
    """
    if not triggered:
        if stop_price is None:
            raise ValueError("STOP_LIMIT 撮合缺少 stop_price")
        breached = bar.high >= stop_price if is_buy else bar.low <= stop_price
        if not breached:
            return MatchResult(filled=False, triggered=False)
    return _match_limit(is_buy, bar, limit_price, triggered=True)


def _match_limit_if_touched(
    is_buy: bool,
    bar: Bar,
    limit_price: float | None,
    trigger_price: float | None,
    triggered: bool,
) -> MatchResult:
    """
    LIMIT_IF_TOUCHED：STOP_LIMIT 的镜像 —— 同样是「触发 → 转限价」两段结构，
    只把触发方向的比较符反过来（BUY 等价格**跌到** trigger 才挂限价买，用于回落抄底；
    SELL 等价格**涨到** trigger 才挂限价卖，用于反弹摸顶）。

    成交价一律走 `_match_limit`：取开盘价与限价中对下单方更不利的一侧。
    **不能用触发价直接成交** —— 触发价是「碰到过」的极值，
    拿它当成交价等于假设自己总能吃在最优点，是偷看。

    与 STOP_LIMIT 共享同一处已知简化：触发后的限价判定用整根 bar 的 high/low。
    """
    if not triggered:
        if trigger_price is None:
            raise ValueError("LIMIT_IF_TOUCHED 撮合缺少 trigger_price")
        touched = bar.low <= trigger_price if is_buy else bar.high >= trigger_price
        if not touched:
            return MatchResult(filled=False, triggered=False)
    return _match_limit(is_buy, bar, limit_price, triggered=True)
