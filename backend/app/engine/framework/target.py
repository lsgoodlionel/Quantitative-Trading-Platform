"""PortfolioTarget — 组合构建的输出、执行模型的输入（Wave K-d / K5）

翻译自 Lean 的 `Common/Algorithm/Framework/Portfolio/PortfolioTarget.cs`（Apache-2.0）。

关键语义：`quantity` 是**目标持仓**（绝对数量，带符号表示方向），不是增量委托。
「已有 100 股、目标 150 股 → 买 50 股」的 diff 由 `ExecutionModel` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioTarget:
    """某标的的目标持仓。"""

    symbol: str
    quantity: int                        # 目标持仓数量（负数 = 目标空头）
    tag: str = ""
    #: 继承自产生它的 Insight；同组目标必须整组执行或整组放弃
    group_id: str | None = None


__all__ = ["PortfolioTarget"]
