"""
声明式因子库 API 端点（Wave-2c / B2）

  GET  /quant/factor/library          — 列出配置生成的因子库（元数据 + 分组汇总）
  POST /quant/factor/library/analyze  — 在 universe 上批量计算因子库横截面 IC 排行

风格对齐 endpoints/factor_processors.py：Pydantic v2 请求模型 + try/except →
HTTPException(400/422/503)，数据层复用 AsyncSessionLocal + DataService，
重型模块在 handler 内惰性 import。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["Factor Library"])

# 单标的最少 bar 数
_MIN_BARS = 60
# 单日横截面最少标的数（低于该数的日期不参与 IC）
_MIN_NAMES = 3


# ── 请求模型 ──────────────────────────────────────────────────────

class LibraryAnalyzeRequest(BaseModel):
    symbols: list[str] = Field(min_length=3, max_length=60)
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    forward_period: int = Field(default=10, ge=1, le=60)
    groups: list[str] | None = Field(default=None, description="仅分析这些分组（None=全部）")
    windows: list[int] | None = Field(default=None, description="仅生成这些窗口（None=默认集合）")
    method: Literal["rank_ic", "ic"] = "rank_ic"
    top_k: int = Field(default=30, ge=1, le=240)


# ── 数据拉取（本工作流自包含，避免跨端点耦合）─────────────────────

async def _fetch_universe(
    symbols: list[str],
    market: str,
    frequency: str,
    start: str | None,
    end: str | None,
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
                continue  # 单标的失败不作硬错误，剔除即可
            if len(bars) >= _MIN_BARS:
                bars_by_symbol[sym] = bars

    if len(bars_by_symbol) < 3:
        raise HTTPException(
            status_code=400,
            detail=f"有效标的不足（横截面 IC 需 ≥ 3，实得 {len(bars_by_symbol)}；每标的需 ≥ {_MIN_BARS} 根 bar）",
        )
    return bars_by_symbol


def _build_specs(groups: list[str] | None, windows: list[int] | None):
    from app.quant.factor_lib.loader import generate_factor_library

    win = tuple(windows) if windows else None
    grp = tuple(groups) if groups else None
    try:
        return generate_factor_library(windows=win, groups=grp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 端点 ──────────────────────────────────────────────────────────

@router.get("/factor/library")
async def get_factor_library(
    group: str | None = Query(default=None, description="按分组过滤（可选）"),
) -> dict:
    """返回配置生成的因子库目录（元数据 + 分组汇总，供前端浏览与筛选）。"""
    from app.quant.factor_lib.loader import (
        DEFAULT_WINDOWS, generate_factor_library, library_group_meta,
    )

    grp = (group,) if group else None
    try:
        specs = generate_factor_library(groups=grp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "n_factors": len(specs),
        "windows": list(DEFAULT_WINDOWS),
        "groups": library_group_meta(specs),
        "factors": [s.to_meta() for s in specs],
    }


@router.post("/factor/library/analyze")
async def analyze_factor_library(req: LibraryAnalyzeRequest) -> dict:
    """批量计算因子库在 universe 上的横截面 IC，并按 IC/RankIC 排行。"""
    from app.quant.factor_lib.loader import build_feature_fn
    from app.quant.factor_lib.ranking import rank_factor_library
    from app.quant.panel import attach_forward_label, bars_to_panel

    specs = _build_specs(req.groups, req.windows)
    bars_by_symbol = await _fetch_universe(
        req.symbols, req.market, req.frequency, req.start, req.end,
    )

    feature_fn = build_feature_fn(specs)
    try:
        panel = bars_to_panel(bars_by_symbol, feature_fn=feature_fn)
    except Exception as e:  # noqa: BLE001 — 因子计算错误映射为 400
        raise HTTPException(status_code=400, detail=f"因子计算失败: {e}") from e

    labeled = attach_forward_label(panel, req.forward_period, label_field="forward_return")

    try:
        ranking = rank_factor_library(
            labeled_panel=labeled,
            specs=specs,
            label_field="forward_return",
            method=req.method,
            top_k=req.top_k,
            min_names=_MIN_NAMES,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"IC 排行计算失败: {e}") from e

    n_dates = int(labeled.index.get_level_values("datetime").nunique())
    ranked = [s.to_dict() for s in ranking]
    return {
        "symbols": list(bars_by_symbol.keys()),
        "market": req.market,
        "forward_period": req.forward_period,
        "method": req.method,
        "n_factors": len(specs),
        "n_symbols": len(bars_by_symbol),
        "n_dates": n_dates,
        "ranking": ranked,
        "best": ranked[0] if ranked else None,
    }
