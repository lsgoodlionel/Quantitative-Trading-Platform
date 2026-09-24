"""
实盘对账（V3 · G6）

把「券商侧的真实持仓 / 资金」与「本地 OMS 记录」摆在一起比对，有差异发
`RECONCILE_DIFF` 通知。`emit_reconcile_diff` 在 Wave A-c 就建好了，这里只是
第一个真正调用它的地方。

## 四条不可动摇的语义

1. **券商不可达 ≠ 对账通过。**
   拉不到券商数据时 `broker_reachable=False`，`position_diffs` 保持为空 ——
   但**空差异在此时不代表一致**。任何消费方都必须先看 `broker_reachable`
   （或直接用 `is_clean`），绝不能把「零差异」当成「账对上了」。
   这是本模块最重要的正确性约束：一份假阳性的对账报告比没有对账更危险。

2. **无差异不发通知，不可达要发。**
   每次对账都响一下等于训练用户忽略它；但「连不上券商」是需要人介入的状态，
   必须发。判断口径见 `ReconcileResult.needs_attention`。

3. **比的是「本进程 OMS 记录的委托推算出的净持仓」，不是「历史全量持仓」。**
   OMS 的订单簿是**内存态**（`OrderManager._orders`），进程重启即失，
   Celery worker 与 API 进程也各有一份互不相通的订单簿。
   因此本地侧天然只覆盖「本进程下过且已成交的单」，券商侧则是账户全量。
   重启后 / 在空订单簿的进程里跑，必然满屏「本地 0 / 券商 N」的差异 ——
   那是范围差异，不是账目错误。结果体里的 `scope` 与 `local_order_count`
   就是给人判断这件事用的，消费方请一并展示。

4. **只读，绝不自动纠正。**
   本模块只调 `gateway.get_positions()` / `gateway.get_account()`，
   不碰 `submit_order` / `cancel_order`，也不改本地订单簿。
   自动纠正一个还没搞懂原因的差异，是把小问题变成大事故的经典路径。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.data.models import Market
from app.notify.emit import emit_reconcile_diff
from app.oms.order import LiveOrder, LiveOrderSide

# 与 `app/oms/manager.py` 一致用标准库 logging（而非 structlog）：
# 对账失败必须在 caplog / 常规日志采集里都能看见。
logger = logging.getLogger(__name__)

# 通知明细里最多列出的差异条数（避免几百个标的撑爆消息体）
MAX_LISTED_DIFFS = 10

# 资金差异容忍度（绝对值，账户币种单位）。
# 券商侧资金带手续费/利息/汇率尾数，浮点意义上的「完全相等」不存在。
CASH_TOLERANCE = 0.01

# 从 OMS 订单簿拉取的订单数上限。`list_orders` 默认 limit=100，对账要看全量。
_ORDER_SCAN_LIMIT = 100_000

SCOPE_PROCESS_ORDERS = "process_orders"
SCOPE_NOTE = (
    "本地侧 = 本进程 OMS 内存订单簿中已成交委托推算的净持仓，"
    "非账户历史全量持仓；进程重启或跨进程运行时差异属范围差异，不等于账目错误。"
)


class PositionSource(Protocol):
    """本地持仓来源（`OrderManager` 满足此协议）。"""

    def list_orders(
        self,
        strategy_id: str | None = ...,
        status: str | None = ...,
        limit: int = ...,
    ) -> list[LiveOrder]: ...


class BrokerSource(Protocol):
    """券商侧只读查询（`TradingGateway` 满足此协议）。"""

    async def get_positions(self) -> list[Any]: ...

    async def get_account(self) -> Any: ...


@dataclass(frozen=True)
class PositionDiff:
    """单个标的的持仓差异。`delta = broker_qty - local_qty`。"""

    symbol: str
    local_qty: int
    broker_qty: int
    delta: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "local_qty": self.local_qty,
            "broker_qty": self.broker_qty,
            "delta": self.delta,
        }

    def describe(self) -> str:
        return f"{self.symbol}: 本地 {self.local_qty} / 券商 {self.broker_qty} (Δ{self.delta:+d})"


@dataclass(frozen=True)
class ReconcileResult:
    """
    一次对账的结果（不可变）。

    ⚠️ `position_diffs` 为空**不**代表对账通过 —— 券商不可达时它同样为空。
    判断是否「真的对上了」请用 `is_clean`。
    """

    market: Market
    checked_at: datetime
    position_diffs: tuple[PositionDiff, ...]
    cash_diff: float | None
    broker_reachable: bool
    local_order_count: int = 0
    broker_position_count: int = 0
    error: str = ""
    scope: str = SCOPE_PROCESS_ORDERS
    scope_note: str = SCOPE_NOTE
    checked_symbols: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_position_diff(self) -> bool:
        return bool(self.position_diffs)

    @property
    def has_cash_diff(self) -> bool:
        return self.cash_diff is not None and abs(self.cash_diff) > CASH_TOLERANCE

    @property
    def diff_count(self) -> int:
        """差异条数（资金差异算一条）。券商不可达时恒为 0，别拿它判断是否一致。"""
        return len(self.position_diffs) + (1 if self.has_cash_diff else 0)

    @property
    def is_clean(self) -> bool:
        """**唯一**可用于「账对上了吗」的判断：必须够得着券商，且零差异。"""
        return self.broker_reachable and self.diff_count == 0

    @property
    def needs_attention(self) -> bool:
        """是否需要发通知：有差异，或够不着券商（后者同样要人介入）。"""
        return not self.is_clean

    def summary(self) -> str:
        if not self.broker_reachable:
            return f"券商不可达，未完成对账：{self.error or '原因未知'}"
        if self.diff_count == 0:
            return "本进程记录与券商一致"
        parts = [d.describe() for d in self.position_diffs[:MAX_LISTED_DIFFS]]
        if len(self.position_diffs) > MAX_LISTED_DIFFS:
            parts.append(f"…（持仓差异共 {len(self.position_diffs)} 条）")
        if self.has_cash_diff:
            parts.append(f"资金: Δ{self.cash_diff:+.2f}")
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.value,
            "checked_at": self.checked_at.isoformat(),
            "broker_reachable": self.broker_reachable,
            "is_clean": self.is_clean,
            "diff_count": self.diff_count,
            "position_diffs": [d.to_dict() for d in self.position_diffs],
            "cash_diff": self.cash_diff,
            "local_order_count": self.local_order_count,
            "broker_position_count": self.broker_position_count,
            "checked_symbols": list(self.checked_symbols),
            "error": self.error,
            "scope": self.scope,
            "scope_note": self.scope_note,
            "summary": self.summary(),
        }


def local_net_positions(orders: Iterable[LiveOrder], market: Market) -> dict[str, int]:
    """
    从 OMS 订单簿推算本地净持仓（BUY 为正、SELL 为负，净额为 0 的标的剔除）。

    只看 `filled_qty`：挂着没成交的委托不构成持仓。
    """
    net: dict[str, int] = {}
    target = market.value.upper()
    for order in orders:
        if (order.market or "").upper() != target or order.filled_qty <= 0:
            continue
        signed = order.filled_qty if order.side is LiveOrderSide.BUY else -order.filled_qty
        net[order.symbol] = net.get(order.symbol, 0) + signed
    return {symbol: qty for symbol, qty in net.items() if qty != 0}


def diff_positions(local: dict[str, int], broker: dict[str, int]) -> tuple[PositionDiff, ...]:
    """两侧净持仓求差；只保留数量不一致的标的，按 symbol 排序保证输出稳定。"""
    return tuple(
        PositionDiff(
            symbol=symbol,
            local_qty=local.get(symbol, 0),
            broker_qty=broker.get(symbol, 0),
            delta=broker.get(symbol, 0) - local.get(symbol, 0),
        )
        for symbol in sorted(set(local) | set(broker))
        if local.get(symbol, 0) != broker.get(symbol, 0)
    )


async def reconcile(
    market: Market | str,
    oms: PositionSource,
    gateway: BrokerSource,
    *,
    local_cash: float | None = None,
) -> ReconcileResult:
    """
    执行一次只读对账。

    Args:
        market:     市场（US / HK / A）
        oms:        本地订单簿来源（`OrderManager`）
        gateway:    券商只读查询（`TradingGateway`）
        local_cash: 本地记账的可用资金。**默认 None** —— OMS 不维护资金账本，
                    凭空造一个本地资金数只会得出假差异；调用方确有账本时再传。
                    不传时 `cash_diff` 为 None，表示「本期未对资金」而非「资金一致」。

    Returns:
        `ReconcileResult`。券商任一查询抛异常即 `broker_reachable=False`，
        此时 `position_diffs` 为空但**不代表一致**（见模块 docstring 第 1 条）。

    本函数不会提交、撤销或修改任何订单。
    """
    market_enum = market if isinstance(market, Market) else Market(str(market).upper())
    checked_at = datetime.now(UTC)

    orders = list(oms.list_orders(limit=_ORDER_SCAN_LIMIT))
    local = local_net_positions(orders, market_enum)

    try:
        broker_positions = await gateway.get_positions()
    except Exception as exc:
        logger.error(
            "对账失败：券商持仓不可达（market=%s），本次结果不构成「对账通过」：%s",
            market_enum.value, exc,
        )
        return ReconcileResult(
            market=market_enum,
            checked_at=checked_at,
            position_diffs=(),
            cash_diff=None,
            broker_reachable=False,
            local_order_count=len(orders),
            error=f"券商持仓查询失败: {exc}",
            checked_symbols=tuple(sorted(local)),
        )

    broker = _broker_net_positions(broker_positions, market_enum)
    diffs = diff_positions(local, broker)

    cash_diff, cash_error = await _reconcile_cash(gateway, local_cash)
    if cash_error:
        # 持仓拿到了、资金没拿到 —— 仍算「没对完账」，不能报一份看似干净的结果
        logger.error(
            "对账失败：券商资金不可达（market=%s），本次结果不构成「对账通过」：%s",
            market_enum.value, cash_error,
        )
        return ReconcileResult(
            market=market_enum,
            checked_at=checked_at,
            position_diffs=(),
            cash_diff=None,
            broker_reachable=False,
            local_order_count=len(orders),
            broker_position_count=len(broker),
            error=cash_error,
            checked_symbols=tuple(sorted(set(local) | set(broker))),
        )

    return ReconcileResult(
        market=market_enum,
        checked_at=checked_at,
        position_diffs=diffs,
        cash_diff=cash_diff,
        broker_reachable=True,
        local_order_count=len(orders),
        broker_position_count=len(broker),
        checked_symbols=tuple(sorted(set(local) | set(broker))),
    )


def notify_reconcile(result: ReconcileResult) -> dict:
    """
    按对账结果决定是否发 `RECONCILE_DIFF` 通知。

    - 有差异 → 发
    - 券商不可达 → 发（需要人介入）
    - 一致 → **不发**（噪音会让用户学会忽略这个通知）
    """
    if result.is_clean:
        return {"dispatched": 0, "skipped": True}
    return emit_reconcile_diff(
        market=result.market.value,
        diff_count=result.diff_count,
        detail=result.summary(),
        broker_reachable=result.broker_reachable,
    )


async def reconcile_and_notify(
    market: Market | str,
    oms: PositionSource,
    gateway: BrokerSource,
    *,
    local_cash: float | None = None,
) -> ReconcileResult:
    """对账 + 按需通知。通知失败不影响返回结果（`emit_*` 内部已吞异常）。"""
    result = await reconcile(market, oms, gateway, local_cash=local_cash)
    notify_reconcile(result)
    return result


# ── 内部工具 ──────────────────────────────────────────────────────

def _broker_net_positions(positions: Iterable[Any], market: Market) -> dict[str, int]:
    """
    券商持仓列表 → {symbol: qty}。

    券商返回的 `market` 字段各家写法不一（有的干脆不填），因此**只在明确写了
    别的市场时才过滤掉** —— 空值按「属于本次查询的市场」处理，否则会把真实持仓
    悄悄丢掉，得出一份假的零差异。
    """
    target = market.value.upper()
    net: dict[str, int] = {}
    for pos in positions:
        raw_market = (getattr(pos, "market", "") or "").upper()
        if raw_market and raw_market != target:
            continue
        symbol = getattr(pos, "symbol", "")
        if not symbol:
            continue
        net[symbol] = net.get(symbol, 0) + int(getattr(pos, "qty", 0) or 0)
    return {symbol: qty for symbol, qty in net.items() if qty != 0}


async def _reconcile_cash(
    gateway: BrokerSource, local_cash: float | None
) -> tuple[float | None, str]:
    """
    返回 `(cash_diff, error)`。

    未提供 `local_cash` 时跳过资金查询（不产生差异也不产生错误）；
    提供了但券商查不到 → 返回错误，由调用方降级为「券商不可达」。
    """
    if local_cash is None:
        return None, ""
    try:
        account = await gateway.get_account()
    except Exception as exc:
        return None, f"券商资金查询失败: {exc}"
    broker_cash = float(getattr(account, "cash", 0.0) or 0.0)
    return round(broker_cash - float(local_cash), 4), ""
