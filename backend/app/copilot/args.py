"""Copilot 工具的参数模型（V3 Wave B-c / I1）

**一条硬规矩：能复用既有端点的 Pydantic 模型就直接复用，不另写一份。**
`draft_order` 用的就是 `orders.SubmitOrderRequest`、`run_backtest` 用的就是
`backtests.BacktestRequest` —— 端点加一个必填字段，工具声明立刻跟着变，
模型生成的参数不会突然在调用时 422。

只有三个端点是「Query 参数」而非请求体，没有现成模型可复用（`/bars/latest`、
`/positions`、`/backtests/history/{id}`）。这里补的三个模型刻意复用端点同款枚举
（`Market` / `Frequency`），枚举值不可能漂移；剩下的只是一个字符串标识。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 复用既有端点的请求模型 —— 这几个 import 就是「不漂移」的全部保证
from app.api.v1.endpoints.backtests import BacktestRequest
from app.api.v1.endpoints.orders import SubmitOrderRequest
from app.api.v1.endpoints.rebalance import RebalancePreviewRequest
from app.api.v1.endpoints.screener import ScreenerFilter
from app.data.models import Frequency, Market

__all__ = [
    "BacktestIdArgs",
    "BacktestRequest",
    "MarketArgs",
    "QuoteArgs",
    "RebalancePreviewRequest",
    "ScreenerFilter",
    "SubmitOrderRequest",
]


class QuoteArgs(BaseModel):
    """`get_quote` 的参数 —— 对应 `GET /bars/latest` 的 Query 参数。"""

    symbol: str = Field(min_length=1, max_length=32, description="标的代码，如 AAPL / 00700 / 000001")
    market: Market = Field(Market.US, description="市场：US / HK / A")
    frequency: Frequency = Field(Frequency.DAY_1, description="K 线周期，默认日线")


class MarketArgs(BaseModel):
    """`get_positions` / `get_account` 的参数 —— 对应 `GET /positions` 的 Query 参数。"""

    market: Market = Field(Market.US, description="市场：US / HK / A")


class BacktestIdArgs(BaseModel):
    """`explain_backtest` 的参数 —— 对应 `GET /backtests/history/{record_id}`。"""

    backtest_id: str = Field(
        min_length=1, max_length=64, description="回测历史记录 ID（UUID）"
    )
