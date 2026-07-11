"""
高级订单算法 — 共享模型与工具

定义父级算法单（AlgoOrder）、子单切片（ChildSlice）及数量分配工具。
三种算法（TWAP/VWAP/Iceberg）的 planner 均产出 List[ChildSlice]，
executor 依据切片的累计延迟逐一将子单提交到现有 OMS.submit_order。

参考: refs/vnpy/vnpy/trader/converter.py 的父单→子单拆分思想（仅参考，未复制代码）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


class AlgoType(str, Enum):
    TWAP = "TWAP"          # 时间加权：等量、等间隔拆分
    VWAP = "VWAP"          # 成交量加权：按日内成交量曲线加权拆分
    ICEBERG = "ICEBERG"    # 冰山：每次仅露出固定显示量


class AlgoStatus(str, Enum):
    PENDING = "pending"       # 已创建，尚未开始调度
    RUNNING = "running"       # 正在逐片提交子单
    COMPLETED = "completed"   # 所有切片已提交完毕
    CANCELLED = "cancelled"   # 被用户撤销
    FAILED = "failed"         # 全部切片提交失败


class SliceStatus(str, Enum):
    SCHEDULED = "scheduled"   # 已排程，等待到点提交
    SUBMITTED = "submitted"   # 已提交到 OMS
    FILLED = "filled"         # 子单已成交
    REJECTED = "rejected"     # 子单被拒 / 提交异常
    SKIPPED = "skipped"       # 算法撤销后未提交


@dataclass
class ChildSlice:
    """一个子单切片：在父单启动后 delay_seconds 秒提交 qty 股。"""

    index: int
    qty: int
    delay_seconds: float

    status: SliceStatus = SliceStatus.SCHEDULED
    child_order_id: Optional[str] = None
    filled_qty: int = 0
    avg_fill_price: Optional[float] = None
    error: Optional[str] = None
    submitted_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "qty": self.qty,
            "delay_seconds": round(self.delay_seconds, 3),
            "status": self.status.value,
            "child_order_id": self.child_order_id,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "error": self.error,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
        }


@dataclass
class AlgoOrder:
    """父级算法单，聚合一组子单切片的完整生命周期。"""

    symbol: str
    market: str
    side: str                      # BUY / SELL
    total_qty: int
    algo_type: AlgoType

    order_type: str = "MARKET"     # 子单下单类型 MARKET / LIMIT
    limit_price: Optional[float] = None
    strategy_id: Optional[str] = None

    # 算法参数（不同算法用到的子集不同）
    duration_seconds: float = 300.0
    slice_count: int = 6
    display_qty: Optional[int] = None   # 仅 Iceberg

    algo_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: AlgoStatus = AlgoStatus.PENDING
    slices: list[ChildSlice] = field(default_factory=list)

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── 派生量 ────────────────────────────────────────────────
    @property
    def filled_qty(self) -> int:
        return sum(s.filled_qty for s in self.slices)

    @property
    def submitted_qty(self) -> int:
        return sum(s.qty for s in self.slices if s.status in (
            SliceStatus.SUBMITTED, SliceStatus.FILLED, SliceStatus.REJECTED,
        ))

    @property
    def progress_pct(self) -> float:
        if self.total_qty <= 0:
            return 0.0
        return round(min(self.filled_qty / self.total_qty, 1.0) * 100, 2)

    @property
    def avg_fill_price(self) -> Optional[float]:
        filled = [(s.filled_qty, s.avg_fill_price) for s in self.slices
                  if s.filled_qty > 0 and s.avg_fill_price is not None]
        total = sum(q for q, _ in filled)
        if total <= 0:
            return None
        return round(sum(q * p for q, p in filled) / total, 6)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "algo_id": self.algo_id,
            "algo_type": self.algo_type.value,
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side,
            "total_qty": self.total_qty,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "strategy_id": self.strategy_id,
            "duration_seconds": self.duration_seconds,
            "slice_count": len(self.slices),
            "display_qty": self.display_qty,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "submitted_qty": self.submitted_qty,
            "avg_fill_price": self.avg_fill_price,
            "progress_pct": self.progress_pct,
            "slices": [s.to_dict() for s in self.slices],
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "updated_at": self.updated_at.isoformat(),
        }


# ── 数量分配工具 ──────────────────────────────────────────────

def distribute_qty(total: int, weights: list[float]) -> list[int]:
    """
    按权重把 total 股整数分配到 len(weights) 个切片，保证：
      - 各片 >= 0 且总和 == total
      - 用「最大余数法」分配零头，避免系统性偏差

    权重非法（空 / 全零 / 含负）时退化为等分。
    """
    n = len(weights)
    if n == 0 or total <= 0:
        return []
    clean = [w for w in weights if w >= 0]
    wsum = sum(clean)
    if len(clean) != n or wsum <= 0:
        weights = [1.0] * n
        wsum = float(n)

    raw = [total * w / wsum for w in weights]
    floors = [int(x) for x in raw]
    remainder = total - sum(floors)

    # 余数按小数部分从大到小逐一 +1
    frac_order = sorted(range(n), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in frac_order[:remainder]:
        floors[i] += 1
    return floors


def u_shaped_weights(n: int, edge_ratio: float = 2.6) -> list[float]:
    """
    生成 n 个「U 型」日内成交量权重（开盘/收盘高、盘中低）。

    经验形态：w(t) = 1 + (edge_ratio-1) * (2t-1)^2, t ∈ [0,1)
    edge_ratio 控制两端相对盘中的倍率（默认 ~2.6x）。
    """
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    out: list[float] = []
    for i in range(n):
        t = i / (n - 1)               # 0 → 1
        shape = (2.0 * t - 1.0) ** 2   # 端点 1，中点 0
        out.append(1.0 + (edge_ratio - 1.0) * shape)
    return out
