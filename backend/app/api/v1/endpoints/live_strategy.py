"""
实盘策略管理 API

提供策略实例的完整生命周期管理：
  POST   /live-strategies/start    — 启动策略实例
  POST   /live-strategies/{id}/stop — 停止策略实例
  GET    /live-strategies/          — 列出所有运行中的策略
  GET    /live-strategies/{id}      — 查询单个策略状态
  DELETE /live-strategies/{id}      — 删除已停止的策略记录

策略状态机:
  idle → running → stopped/error

风控集成:
  启动前检查策略名称是否合法
  每次下单前经过 RiskEngine 前置检查
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.data.service import DataService
from app.strategy.engine import StrategyState, get_strategy_engine
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()
logger = get_logger(__name__)


# ── Schema ──────────────────────────────────────────────────

class StartStrategyRequest(BaseModel):
    strategy_name: str = Field(..., description="策略名称，见 /strategies/presets")
    symbol: str = Field(..., min_length=1, max_length=20, description="标的代码")
    market: str = Field("US", pattern="^(US|HK|A)$", description="市场")
    frequency: str = Field("1d", description="K 线周期")
    params: dict = Field(default_factory=dict, description="策略参数")
    warmup_days: int = Field(120, ge=20, le=730, description="历史预热天数")
    sim_days: int = Field(60, ge=7, le=365, description="模拟回测天数（最近 N 天）")
    instance_id: str | None = Field(None, description="自定义实例 ID（默认自动生成）")


class StrategyInstanceResponse(BaseModel):
    instance_id: str
    strategy_name: str
    symbol: str
    market: str
    frequency: str
    params: dict
    state: str
    error: str | None
    bars_processed: int
    orders_placed: int
    started_at: str | None
    stopped_at: str | None
    paper: dict | None = None   # 纸面交易模拟结果


# ── Deps ────────────────────────────────────────────────────

def get_svc(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# ── 端点 ─────────────────────────────────────────────────────

@router.get("/", response_model=list[StrategyInstanceResponse])
async def list_live_strategies() -> list[StrategyInstanceResponse]:
    """列出所有策略实例（含运行中、已停止、报错）。"""
    engine = get_strategy_engine()
    return [StrategyInstanceResponse(**inst) for inst in engine.list_instances()]


@router.post("/start", response_model=StrategyInstanceResponse, status_code=status.HTTP_201_CREATED)
async def start_live_strategy(
    body: StartStrategyRequest,
    svc: Annotated[DataService, Depends(get_svc)],
) -> StrategyInstanceResponse:
    """
    启动一个策略实例进入实盘/模拟执行模式。

    策略将订阅实时 K 线，每根 K 线触发一次 on_bar 回调，
    信号经过风控检查后通过 OMS 提交订单。
    """
    if body.strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown strategy '{body.strategy_name}'. "
                f"Available: {sorted(STRATEGY_REGISTRY.keys())}"
            ),
        )

    instance_id = body.instance_id or f"{body.strategy_name}:{body.symbol}:{uuid.uuid4().hex[:8]}"

    engine = get_strategy_engine()

    # 检查是否已有同 ID 运行中的实例
    existing = engine.get_instance(instance_id)
    if existing and existing.state == StrategyState.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Strategy instance '{instance_id}' is already running",
        )

    try:
        inst = await engine.start_strategy(
            instance_id=instance_id,
            strategy_name=body.strategy_name,
            symbol=body.symbol.upper(),
            market=body.market.upper(),
            frequency=body.frequency,
            params=body.params,
            data_service=svc,
            warmup_days=body.warmup_days,
            sim_days=body.sim_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Failed to start strategy", instance_id=instance_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start strategy: {e}",
        )

    logger.info("Live strategy started", instance_id=instance_id, strategy=body.strategy_name)
    return StrategyInstanceResponse(**inst.to_dict())


@router.post("/{instance_id}/stop", response_model=StrategyInstanceResponse)
async def stop_live_strategy(instance_id: str) -> StrategyInstanceResponse:
    """停止指定策略实例。"""
    engine = get_strategy_engine()
    inst = engine.get_instance(instance_id)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy instance '{instance_id}' not found",
        )

    if inst.state != StrategyState.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strategy '{instance_id}' is not running (state={inst.state.value})",
        )

    try:
        inst = await engine.stop_strategy(instance_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop strategy: {e}",
        )

    logger.info("Live strategy stopped", instance_id=instance_id)
    return StrategyInstanceResponse(**inst.to_dict())


@router.get("/{instance_id}", response_model=StrategyInstanceResponse)
async def get_live_strategy(instance_id: str) -> StrategyInstanceResponse:
    """查询单个策略实例状态。"""
    engine = get_strategy_engine()
    inst = engine.get_instance(instance_id)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy instance '{instance_id}' not found",
        )
    return StrategyInstanceResponse(**inst.to_dict())


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_strategy(instance_id: str) -> None:
    """删除已停止/错误的策略实例记录。"""
    engine = get_strategy_engine()
    inst = engine.get_instance(instance_id)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy instance '{instance_id}' not found",
        )
    if inst.state == StrategyState.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a running strategy. Stop it first.",
        )
    engine._instances.pop(instance_id, None)
