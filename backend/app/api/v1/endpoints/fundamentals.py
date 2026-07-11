"""
基本面数据 API 端点（Wave-2a / A2）

  GET /fundamentals/{symbol}  — 利润表/资产负债/现金流/财务比率/关键指标（分节）

query 参数:
  market   : US / HK / A（默认 US）
  limit    : 返回最近 N 期报表（默认 5，范围 1-20）
  sections : 逗号分隔，裁剪返回节，如 "income,metrics"；缺省返回全部

风格对齐 endpoints/quant.py：Pydantic 响应模型 + try/except → HTTPException。
数据层复用 app.data.providers.FundamentalsService（内部并发拉取 + 派生比率）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.logging import get_logger
from app.data.providers import FundamentalsService
from app.data.providers.models import FundamentalsBundle

logger = get_logger(__name__)

router = APIRouter(tags=["Fundamentals"])

_ALL_SECTIONS = {"income", "balance", "cashflow", "ratios", "metrics"}


def _parse_sections(sections: str | None) -> set[str] | None:
    """解析 sections query；非法节名 → 400。None/空 → 全部。"""
    if not sections:
        return None
    wanted = {s.strip().lower() for s in sections.split(",") if s.strip()}
    invalid = wanted - _ALL_SECTIONS
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"未知分节: {', '.join(sorted(invalid))}；可选 {', '.join(sorted(_ALL_SECTIONS))}",
        )
    return wanted


def _apply_sections(bundle: FundamentalsBundle, wanted: set[str] | None) -> FundamentalsBundle:
    """按 wanted 裁剪返回节（未选中的清空/置 None）。"""
    if wanted is None:
        return bundle
    return bundle.model_copy(
        update={
            "income": bundle.income if "income" in wanted else [],
            "balance": bundle.balance if "balance" in wanted else [],
            "cashflow": bundle.cashflow if "cashflow" in wanted else [],
            "ratios": bundle.ratios if "ratios" in wanted else [],
            "metrics": bundle.metrics if "metrics" in wanted else None,
        }
    )


@router.get("/{symbol}", response_model=FundamentalsBundle)
async def get_fundamentals(
    symbol: str = Path(..., description="标的代码，如 AAPL / 00700 / 600519"),
    market: Literal["US", "HK", "A"] = Query("US", description="市场"),
    limit: int = Query(5, ge=1, le=20, description="返回最近 N 期报表"),
    sections: str | None = Query(None, description="逗号分隔裁剪返回节，缺省返回全部"),
) -> FundamentalsBundle:
    """获取单标的基本面数据（分节）。"""
    wanted = _parse_sections(sections)
    try:
        bundle = await FundamentalsService().get_fundamentals(
            symbol=symbol, market=market, limit=limit
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — 数据源整体不可用
        logger.error("fundamentals fetch failed", symbol=symbol, market=market, error=str(e))
        raise HTTPException(status_code=503, detail=f"基本面数据源不可用: {e}") from e

    return _apply_sections(bundle, wanted)
