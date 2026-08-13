"""执行模型（Wave K-d 骨架 / Wave L-b 模型）

`ExecutionModel` 是三段式框架的最后一段：把风控修正后的**目标持仓**变成
**增量订单**。已有 100 股、目标 150 股 → 买 50 股，而不是买 150 股。

可用模型
--------
- `ImmediateExecutionModel` — 立即以市价单补齐差额（默认）
- `VolumeWeightedAveragePriceExecution` — 按成交量占比拆单，残量顺延
- `StandardDeviationExecution` — 价格朝有利方向偏离 N 个标准差才成交
- `SpreadExecution` — 价差收窄到阈值内才成交（OHLCV 近似，见其 docstring）

执行层 vs 撮合层（契约 §3.1）
-----------------------------
`VolumeWeightedAveragePriceExecution` 是**执行层**的主动拆单；
K6 的 `VolumeShareSlippage.fill_limit()` 是**撮合层**的被动上限（券商侧
「这根 bar 最多成交这么多」）。两者叠加是正常且预期的：执行模型先把大单拆小，
撮合层再对拆出来的每一张施加上限，残量重新挂回队列。

实盘复用（本期只做接口对齐）
----------------------------
本层只依赖 `PortfolioContext`，**不 import 任何 `app.oms` 模块** —— 同一个
`FrameworkStrategy` 必须能在回测与实盘跑，实盘由 `ctx.submit` 路由到 OMS。

实盘接线（本 Wave 的独立步骤，L-a/L-b/L-c 合入后进行）落地时，本层的拆单参数
可直接映射到 `app/oms/algos/` 的同名算法：

- `VolumeWeightedAveragePriceExecution.max_order_percent_volume` → `oms/algos/vwap.py`
  的参与率；按时间等分的变体走 `oms/algos/twap.py`
- 单笔显露量的上限语义 → `oms/algos/iceberg.py`

映射由 `oms/algos/executor.py` 承接，本层不做任何 OMS 侧的假设。
"""

from app.engine.framework.execution.base import (
    ExecutionLeg,
    ExecutionModel,
    ImmediateExecutionModel,
    build_plan,
    to_order,
)
from app.engine.framework.execution.spread import SpreadExecution
from app.engine.framework.execution.std_dev import StandardDeviationExecution
from app.engine.framework.execution.vwap import VolumeWeightedAveragePriceExecution

__all__ = [
    "ExecutionLeg",
    "ExecutionModel",
    "ImmediateExecutionModel",
    "SpreadExecution",
    "StandardDeviationExecution",
    "VolumeWeightedAveragePriceExecution",
    "build_plan",
    "to_order",
]
