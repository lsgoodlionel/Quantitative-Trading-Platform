from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

StrategyStatus = Literal["draft", "backtesting", "paper", "live", "stopped", "error"]

# 内置预设策略列表 — Phase 2 实现具体策略类
# name = snake_case 策略 ID（前端路由用）; description = 中文显示名
PRESET_STRATEGIES = [
    {"name": "double_ma",          "description": "双均线趋势 — 短期均线穿越长期均线触发买卖信号"},
    {"name": "bollinger",          "description": "布林带均值回归 — 价格触碰通道边界时反向交易"},
    {"name": "macd",               "description": "MACD 动量 — 利用 MACD 柱与信号线交叉捕捉趋势"},
    {"name": "rsi_mean_reversion", "description": "RSI 均值回归 — 超买超卖区域的反向修复策略"},
    {"name": "momentum_rotation",  "description": "动量轮动 ETF — 持有近期表现最强的 ETF 组合"},
    {"name": "grid_trading",       "description": "网格交易 — 在价格区间内自动挂出买卖网格订单"},
    {"name": "pairs_trading",      "description": "配对统计套利 — 基于协整关系的多空配对策略"},
    {"name": "multi_factor",       "description": "多因子选股 — 综合价值/动量/质量因子排名选股"},
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
