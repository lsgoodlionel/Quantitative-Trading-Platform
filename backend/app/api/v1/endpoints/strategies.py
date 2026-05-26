from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

StrategyStatus = Literal["draft", "backtesting", "paper", "live", "stopped", "error"]

# 内置预设策略列表 — Phase 2 实现具体策略类
PRESET_STRATEGIES = [
    {"id": "double_ma", "name": "双均线趋势", "type": "trend", "markets": ["US", "HK"]},
    {"id": "bollinger", "name": "布林带均值回归", "type": "mean_reversion", "markets": ["US", "HK"]},
    {"id": "macd", "name": "MACD动量", "type": "momentum", "markets": ["US", "HK"]},
    {"id": "rsi_mean_reversion", "name": "RSI均值回归", "type": "mean_reversion", "markets": ["US", "HK"]},
    {"id": "momentum_rotation", "name": "动量轮动ETF", "type": "rotation", "markets": ["US"]},
    {"id": "grid_trading", "name": "网格交易", "type": "grid", "markets": ["US", "HK"]},
    {"id": "pairs_trading", "name": "配对统计套利", "type": "arbitrage", "markets": ["HK", "US"]},
    {"id": "multi_factor", "name": "多因子选股", "type": "factor", "markets": ["US"]},
]


class StrategyCreate(BaseModel):
    name: str
    description: str | None = None
    preset: str | None = None        # 使用预设策略
    code: str | None = None          # 或自定义代码
    config: dict = {}
    markets: list[str] = []


class StrategyResponse(BaseModel):
    id: UUID
    name: str
    status: StrategyStatus
    preset: str | None
    markets: list[str]
    config: dict


@router.get("/presets")
async def list_presets() -> list[dict]:
    """获取所有内置预设策略"""
    return PRESET_STRATEGIES


@router.get("", response_model=list[StrategyResponse])
async def list_strategies() -> list[StrategyResponse]:
    # TODO: 从数据库查询
    return []


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(body: StrategyCreate) -> StrategyResponse:
    if body.preset and body.preset not in {p["id"] for p in PRESET_STRATEGIES}:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {body.preset}")
    if not body.preset and not body.code:
        raise HTTPException(status_code=400, detail="Either preset or code is required")
    # TODO: 持久化到数据库
    raise HTTPException(status_code=501, detail="Phase 2 implementation pending")


@router.post("/{strategy_id}/start")
async def start_strategy(
    strategy_id: UUID,
    gateway: str = "alpaca_paper",
) -> dict:
    # TODO Phase 3: 接入实盘引擎
    return {"status": "pending", "message": f"Strategy {strategy_id} queued for gateway {gateway}"}


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: UUID) -> dict:
    # TODO Phase 3
    return {"status": "stopped", "strategy_id": str(strategy_id)}
