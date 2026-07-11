"""
因子处理器 & 成本感知适应度 API 端点（Wave-1a）

  GET  /quant/processors/meta      — 处理器注册表（供前端流水线构建器）
  POST /quant/processors/preview   — 运行防泄漏处理流水线并返回前后分布对比（B1）
  POST /quant/factor/fitness       — 计算成本感知因子适应度标量（B4）

风格对齐 endpoints/quant.py：Pydantic v2 请求模型 + try/except → HTTPException(400/422/503)，
数据层复用 AsyncSessionLocal + DataService，处理模块在 handler 内惰性 import。
"""

from __future__ import annotations

from typing import Callable, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Factor Processors"])

# 单标的最少 bar 数
_MIN_BARS = 60


# ── 请求 / 响应模型 ───────────────────────────────────────────────

class ProcessorConfigModel(BaseModel):
    name: str
    params: dict[str, object] = Field(default_factory=dict)


class ProcessorPreviewRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=50)
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    fit_end: str
    base_factor: str = "momentum_20"
    tokens: list[str] | None = Field(default=None, max_length=32)
    infer_processors: list[ProcessorConfigModel] = Field(default_factory=list)
    learn_processors: list[ProcessorConfigModel] = Field(default_factory=list)
    forward_period: int = Field(default=10, ge=1, le=60)


class FactorFitnessRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=50)
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    base_factor: str = "momentum_20"
    tokens: list[str] | None = Field(default=None, min_length=1, max_length=32)
    forward_period: int = Field(default=5, ge=1, le=60)
    fee_rate: float | None = Field(default=None, ge=0, le=0.05)
    max_impact: float | None = Field(default=None, ge=0, le=0.2)
    trade_notional: float | None = Field(default=None, gt=0)
    entry_threshold: float | None = Field(default=None, gt=0, lt=1)
    drawdown_bar: float | None = Field(default=None, gt=0, lt=1)
    drawdown_penalty: float | None = Field(default=None, ge=0)
    min_activity: int | None = Field(default=None, ge=0)


# ── 共享工具 ──────────────────────────────────────────────────────

def _make_feature_fn(base_factor: str, tokens: list[str] | None) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """构造 单标的 OHLCV 帧 → {"factor"} 特征帧 的映射函数。"""
    from app.quant.factor_analysis import _compute_factor
    from app.quant.formula_factor import evaluate_formula

    def fn(ohlcv: pd.DataFrame) -> pd.DataFrame:
        if base_factor == "__formula__":
            series = evaluate_formula(ohlcv, tokens or [])
        else:
            series = _compute_factor(ohlcv, base_factor)
        return series.astype(float).to_frame("factor")

    return fn


async def _fetch_universe(
    symbols: list[str],
    market: str,
    frequency: str,
    start: str | None,
    end: str | None,
) -> dict[str, list]:
    """按 universe 拉取各标的 bar；剔除拉取失败或 bar 数不足的标的。"""
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
                # 单标的拉取失败不作硬错误，剔除即可
                continue
            if len(bars) >= _MIN_BARS:
                bars_by_symbol[sym] = bars

    if len(bars_by_symbol) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"有效标的不足（需 ≥ 2，实得 {len(bars_by_symbol)}；每标的需 ≥ {_MIN_BARS} 根 bar）",
        )

    return bars_by_symbol


def _validate_base_factor(base_factor: str, tokens: list[str] | None) -> None:
    if base_factor == "__formula__":
        if not tokens:
            raise HTTPException(status_code=400, detail="公式因子需提供 tokens")


# ── 端点 ──────────────────────────────────────────────────────────

@router.get("/processors/meta")
async def get_processor_meta() -> list[dict]:
    """返回处理器注册表元数据（供前端流水线构建器）。"""
    from app.quant.processing_pipeline import PROCESSOR_META
    return PROCESSOR_META


@router.post("/processors/preview")
async def preview_processors(req: ProcessorPreviewRequest) -> dict:
    """运行防泄漏处理流水线，返回基础因子处理前/后的分布与样本对比。"""
    from app.quant.panel import (
        attach_forward_label, bars_to_panel, column_cells, column_stats,
    )
    from app.quant.processing_pipeline import ProcessingPipeline, ProcessorConfig
    from app.quant.processors import ProcessorError

    _validate_base_factor(req.base_factor, req.tokens)
    bars_by_symbol = await _fetch_universe(
        req.symbols, req.market, req.frequency, req.start, req.end,
    )

    feature_fn = _make_feature_fn(req.base_factor, req.tokens)
    try:
        full_panel = bars_to_panel(bars_by_symbol, feature_fn=feature_fn)
    except Exception as e:  # noqa: BLE001 — 公式/因子计算错误映射为 400
        raise HTTPException(status_code=400, detail=f"因子计算失败: {e}") from e

    labeled = attach_forward_label(full_panel, req.forward_period, label_field="label")
    proc_panel = labeled[["factor", "label"]].copy()

    raw_stats = column_stats(proc_panel, "factor")
    sample_before = column_cells(proc_panel, "factor", max_rows=500)

    # fit_start 默认取 start（缺省则用面板最早日期）
    lvl = proc_panel.index.get_level_values("datetime")
    fit_start = req.start or str(lvl.min())[:10]

    try:
        infer_cfgs = [ProcessorConfig(c.name, dict(c.params)) for c in req.infer_processors]
        learn_cfgs = [ProcessorConfig(c.name, dict(c.params)) for c in req.learn_processors]
        pipeline = ProcessingPipeline.from_configs(infer_cfgs, learn_cfgs)
        pipeline = pipeline.fit(proc_panel, fit_start, req.fit_end)
        result = pipeline.process(proc_panel, for_infer=False)
    except ProcessorError as e:
        raise HTTPException(status_code=400, detail=f"流水线配置错误: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"处理失败: {e}") from e

    processed = result.panel
    processed_stats = column_stats(processed, "factor")
    sample_after = column_cells(processed, "factor", max_rows=500)
    columns = [c for c in proc_panel.columns if c != "label"]

    return {
        "symbols": list(bars_by_symbol.keys()),
        "market": req.market,
        "fit_end": req.fit_end,
        "n_rows_in": result.n_rows_in,
        "n_rows_out": result.n_rows_out,
        "dropped_rows": result.dropped_rows,
        "fitted_learn": result.fitted_learn,
        "columns": columns,
        "raw_stats": raw_stats,
        "processed_stats": processed_stats,
        "sample_before": sample_before,
        "sample_after": sample_after,
    }


@router.post("/factor/fitness")
async def compute_fitness(req: FactorFitnessRequest) -> dict:
    """计算候选因子在 universe 上的成本感知适应度标量（B4）。"""
    from app.quant.factor_fitness import FitnessConfig, compute_factor_fitness
    from app.quant.panel import attach_forward_label, bars_to_panel

    _validate_base_factor(req.base_factor, req.tokens)
    bars_by_symbol = await _fetch_universe(
        req.symbols, req.market, req.frequency, req.start, req.end,
    )

    feature_fn = _make_feature_fn(req.base_factor, req.tokens)
    try:
        full_panel = bars_to_panel(bars_by_symbol, feature_fn=feature_fn)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"因子计算失败: {e}") from e

    labeled = attach_forward_label(full_panel, req.forward_period, label_field="forward_return")
    factor_panel = labeled[["factor"]].copy()
    forward_return_panel = labeled[["forward_return"]].copy()
    liquidity_panel = (labeled["close"] * labeled["volume"]).to_frame("liquidity")

    defaults = FitnessConfig()
    config = FitnessConfig(
        fee_rate=req.fee_rate if req.fee_rate is not None else defaults.fee_rate,
        max_impact=req.max_impact if req.max_impact is not None else defaults.max_impact,
        trade_notional=req.trade_notional if req.trade_notional is not None else defaults.trade_notional,
        entry_threshold=req.entry_threshold if req.entry_threshold is not None else defaults.entry_threshold,
        drawdown_bar=req.drawdown_bar if req.drawdown_bar is not None else defaults.drawdown_bar,
        drawdown_penalty=req.drawdown_penalty if req.drawdown_penalty is not None else defaults.drawdown_penalty,
        min_activity=req.min_activity if req.min_activity is not None else defaults.min_activity,
    )

    try:
        result = compute_factor_fitness(
            factor_panel=factor_panel,
            forward_return_panel=forward_return_panel,
            liquidity_panel=liquidity_panel,
            config=config,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"适应度计算失败: {e}") from e

    return {
        "symbols": list(bars_by_symbol.keys()),
        "market": req.market,
        "base_factor": req.base_factor,
        "tokens": req.tokens,
        "forward_period": req.forward_period,
        "fitness": result.fitness,
        "mean_net_return": result.mean_net_return,
        "gross_return": result.gross_return,
        "total_cost": result.total_cost,
        "turnover": result.turnover,
        "avg_activity": result.avg_activity,
        "n_big_drawdowns": result.n_big_drawdowns,
        "activity_gate_passed": result.activity_gate_passed,
        "per_instrument_score": result.per_instrument_score,
        "config_used": {
            "fee_rate": config.fee_rate,
            "max_impact": config.max_impact,
            "trade_notional": config.trade_notional,
            "entry_threshold": config.entry_threshold,
            "drawdown_bar": config.drawdown_bar,
            "drawdown_penalty": config.drawdown_penalty,
            "min_activity": config.min_activity,
        },
    }
