"""
遗传因子挖掘 & 实验记录器 API 端点（Wave-3a / B5 + B7）

  POST   /quant/factor/mine        — 遗传/进化因子挖掘（返回高分候选公式）
  GET    /quant/experiments        — 实验排行榜（按适应度 / 时间）
  POST   /quant/experiments        — 手动记录一次实验
  DELETE /quant/experiments/{id}   — 删除一条实验记录

风格对齐 endpoints/factor_processors.py & factor_library.py：Pydantic v2 请求模型 +
try/except → HTTPException(400/422)，数据层复用 AsyncSessionLocal + DataService，重型模块
在 handler 内惰性 import；CPU 密集的进化循环走 run_in_executor 避免阻塞事件循环。
Redis 持久化复用 core.redis.get_redis（仿 broker_config）。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.redis import get_redis

router = APIRouter(tags=["Factor Mining"])

# 单标的最少 bar 数（低于则从 universe 剔除）
_MIN_BARS = 60
# 横截面适应度所需最少标的数
_MIN_SYMBOLS = 3


# ── 请求 / 响应模型 ───────────────────────────────────────────────

class MineRequest(BaseModel):
    symbols: list[str] = Field(min_length=3, max_length=40)
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    forward_period: int = Field(default=5, ge=1, le=60)
    # 遗传算法超参
    population_size: int = Field(default=24, ge=6, le=60)
    generations: int = Field(default=12, ge=2, le=30)
    tournament_size: int = Field(default=3, ge=2, le=8)
    crossover_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    mutation_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    elite_count: int = Field(default=2, ge=0, le=8)
    max_depth: int = Field(default=4, ge=2, le=6)
    top_k: int = Field(default=10, ge=1, le=30)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    # 成本感知适应度覆盖（None → 用默认）
    fee_rate: float | None = Field(default=None, ge=0, le=0.05)
    entry_threshold: float | None = Field(default=None, gt=0, lt=1)
    min_activity: int | None = Field(default=None, ge=0)
    # 是否把最优个体自动写入实验排行榜
    record_best: bool = True


class MetricsModel(BaseModel):
    ic_mean: float | None = None
    rank_ic_mean: float | None = None
    icir: float | None = None
    fitness: float | None = None
    mean_net_return: float | None = None


class RecordRequest(BaseModel):
    kind: Literal["factor_analysis", "formula_factor", "genetic_mining", "factor_library"]
    name: str = Field(min_length=1, max_length=120)
    market: str = Field(min_length=1, max_length=8)
    symbols: list[str] = Field(default_factory=list, max_length=60)
    tokens: list[str] = Field(default_factory=list, max_length=64)
    params: dict = Field(default_factory=dict)
    metrics: MetricsModel = Field(default_factory=MetricsModel)
    note: str = Field(default="", max_length=500)


# ── 数据拉取（自包含，避免跨端点耦合）────────────────────────────

async def _fetch_universe(
    symbols: list[str], market: str, frequency: str,
    start: str | None, end: str | None,
) -> dict[str, list]:
    """按 universe 拉取各标的 bar；剔除失败或 bar 数不足者。"""
    from datetime import date, timedelta

    from app.core.database import AsyncSessionLocal
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService

    end_date = date.fromisoformat(end) if end else date.today()
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=365 * 2)

    try:
        market_enum = MarketEnum(market)
        freq_enum = FreqEnum(frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    bars_by_symbol: dict[str, list] = {}
    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        for raw in symbols:
            sym = raw.strip().upper()
            if not sym:
                continue
            try:
                bars = await svc.get_bars(sym, market_enum, freq_enum, start_date, end_date)
            except Exception:
                continue  # 单标的失败不作硬错误
            if len(bars) >= _MIN_BARS:
                bars_by_symbol[sym] = bars

    if len(bars_by_symbol) < _MIN_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"有效标的不足（横截面挖掘需 ≥ {_MIN_SYMBOLS}，实得 {len(bars_by_symbol)}；"
                   f"每标的需 ≥ {_MIN_BARS} 根 bar）",
        )
    return bars_by_symbol


def _build_panels(bars_by_symbol: dict[str, list], forward_period: int):
    """由 bars 构建：每标的 OHLCV 帧 + 前瞻收益面板 + 流动性面板。"""
    from app.quant.panel import _bars_to_ohlcv, attach_forward_label, bars_to_panel

    ohlcv_by_symbol = {sym: _bars_to_ohlcv(bars) for sym, bars in bars_by_symbol.items()}
    base_panel = bars_to_panel(bars_by_symbol)
    labeled = attach_forward_label(base_panel, forward_period, label_field="forward_return")
    forward_return_panel = labeled[["forward_return"]].copy()
    liquidity_panel = (labeled["close"] * labeled["volume"]).to_frame("liquidity")
    return ohlcv_by_symbol, forward_return_panel, liquidity_panel


def _make_fitness_config(req: MineRequest):
    from app.quant.factor_fitness import FitnessConfig

    defaults = FitnessConfig()
    return FitnessConfig(
        fee_rate=req.fee_rate if req.fee_rate is not None else defaults.fee_rate,
        entry_threshold=req.entry_threshold if req.entry_threshold is not None else defaults.entry_threshold,
        min_activity=req.min_activity if req.min_activity is not None else defaults.min_activity,
    )


# ── 端点：遗传因子挖掘 ────────────────────────────────────────────

@router.post("/factor/mine")
async def mine_factors(
    req: MineRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """在 universe 上运行遗传算法搜索高适应度因子公式（B5）。"""
    from app.quant.mining.genetic import GAConfig, evolve

    bars_by_symbol = await _fetch_universe(
        req.symbols, req.market, req.frequency, req.start, req.end,
    )
    try:
        ohlcv_by_symbol, fwd_panel, liq_panel = _build_panels(bars_by_symbol, req.forward_period)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"面板构建失败: {e}") from e

    ga_config = GAConfig(
        population_size=req.population_size,
        generations=req.generations,
        tournament_size=req.tournament_size,
        crossover_rate=req.crossover_rate,
        mutation_rate=req.mutation_rate,
        elite_count=req.elite_count,
        max_depth=req.max_depth,
        top_k=req.top_k,
        seed=req.seed,
    )
    fitness_config = _make_fitness_config(req)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, evolve, ohlcv_by_symbol, fwd_panel, liq_panel, fitness_config, ga_config,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"进化搜索失败: {e}") from e

    recorded_id = None
    if req.record_best and result.best is not None:
        recorded_id = await _record_best(redis, req, result.best, bars_by_symbol)

    payload = result.to_dict()
    payload.update({
        "symbols": list(bars_by_symbol.keys()),
        "market": req.market,
        "forward_period": req.forward_period,
        "recorded_id": recorded_id,
    })
    return payload


async def _record_best(redis, req: MineRequest, best, bars_by_symbol) -> str:
    """把最优个体写入实验排行榜，返回记录 id。"""
    from app.quant.experiments.recorder import (
        ExperimentMetrics, build_record, save_experiment,
    )

    metrics = ExperimentMetrics(
        ic_mean=best.ic_mean, rank_ic_mean=best.rank_ic_mean, icir=best.icir,
        fitness=best.fitness, mean_net_return=best.mean_net_return,
    )
    record = build_record(
        kind="genetic_mining",
        name=best.expr[:120],
        market=req.market,
        symbols=list(bars_by_symbol.keys()),
        metrics=metrics,
        tokens=list(best.tokens),
        params={
            "forward_period": req.forward_period,
            "population_size": req.population_size,
            "generations": req.generations,
            "seed": req.seed,
        },
        note="遗传挖掘最优个体（自动记录）",
    )
    saved = await save_experiment(redis, record)
    return saved.id


# ── 端点：实验排行榜 ──────────────────────────────────────────────

@router.get("/experiments")
async def get_experiments(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    sort_by: Literal["score", "time"] = Query(default="score"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """列出实验记录排行榜（按适应度/RankIC 评分或按时间）。"""
    from app.quant.experiments.recorder import list_experiments

    records = await list_experiments(redis, sort_by=sort_by, kind=kind, limit=limit)
    return {
        "sort_by": sort_by,
        "kind": kind,
        "count": len(records),
        "records": [r.to_dict() for r in records],
    }


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    req: RecordRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """手动记录一次实验（如把某个因子分析结果收藏进排行榜）。"""
    from app.quant.experiments.recorder import (
        ExperimentMetrics, build_record, save_experiment,
    )

    metrics = ExperimentMetrics(**req.metrics.model_dump())
    record = build_record(
        kind=req.kind, name=req.name, market=req.market,
        symbols=req.symbols, metrics=metrics,
        tokens=req.tokens, params=req.params, note=req.note,
    )
    saved = await save_experiment(redis, record)
    return saved.to_dict()


@router.delete("/experiments/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_experiment(
    record_id: str,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> None:
    """删除一条实验记录。"""
    from app.quant.experiments.recorder import delete_experiment

    existed = await delete_experiment(redis, record_id)
    if not existed:
        raise HTTPException(status_code=404, detail="实验记录不存在")
