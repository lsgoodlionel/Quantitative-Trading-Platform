"""股票筛选器 API（Epic A / A3）。

- POST /screener/run       过滤条件 → 匹配标的列表
- GET  /screener/presets   预设筛选方案
- GET  /screener/movers    涨跌榜（涨幅榜 / 跌幅榜）
- GET  /screener/sectors   可选行业标签
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.data import screener as svc
from app.data.models import Market
from app.data.screener import Candidate, ScreenerCriteria
from app.data.screener_meta import PRESETS, SECTORS

router = APIRouter()

SortKey = Literal["change_pct", "market_cap", "pe", "pb",
                  "dividend_yield", "turnover", "price"]


# ── Schemas ───────────────────────────────────────────────────
class ScreenerFilter(BaseModel):
    market: Market = Field(Market.US, description="市场: US / HK / A")
    min_price: float | None = Field(None, description="最低价")
    max_price: float | None = Field(None, description="最高价")
    min_market_cap_yi: float | None = Field(None, description="最小市值（亿，本币）")
    max_market_cap_yi: float | None = Field(None, description="最大市值（亿，本币）")
    min_pe: float | None = Field(None, description="最小市盈率")
    max_pe: float | None = Field(None, description="最大市盈率")
    min_pb: float | None = Field(None, description="最小市净率")
    max_pb: float | None = Field(None, description="最大市净率")
    min_dividend_yield: float | None = Field(None, description="最低股息率 %")
    min_change_pct: float | None = Field(None, description="最低当日涨跌幅 %")
    max_change_pct: float | None = Field(None, description="最高当日涨跌幅 %")
    min_volume: int | None = Field(None, description="最低成交量")
    sectors: list[str] = Field(default_factory=list, description="行业标签过滤")
    sort_by: SortKey = "change_pct"
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = Field(50, ge=1, le=200)

    def to_criteria(self) -> ScreenerCriteria:
        return ScreenerCriteria(
            market=self.market,
            min_price=self.min_price, max_price=self.max_price,
            min_market_cap_yi=self.min_market_cap_yi,
            max_market_cap_yi=self.max_market_cap_yi,
            min_pe=self.min_pe, max_pe=self.max_pe,
            min_pb=self.min_pb, max_pb=self.max_pb,
            min_dividend_yield=self.min_dividend_yield,
            min_change_pct=self.min_change_pct,
            max_change_pct=self.max_change_pct,
            min_volume=self.min_volume,
            sectors=list(self.sectors),
            sort_by=self.sort_by, sort_dir=self.sort_dir, limit=self.limit,
        )


class CandidateOut(BaseModel):
    symbol: str
    market: str
    name: str
    sector: str
    price: float | None = None
    change_pct: float | None = None
    pe: float | None = None
    pb: float | None = None
    market_cap: float | None = None       # 本币原始单位
    market_cap_yi: float | None = None    # 市值（亿），便于前端展示
    dividend_yield: float | None = None
    volume: int | None = None
    turnover: float | None = None
    turnover_rate: float | None = None

    @classmethod
    def from_candidate(cls, c: Candidate) -> "CandidateOut":
        cap_yi = round(c.market_cap / 1e8, 2) if c.market_cap is not None else None
        return cls(
            symbol=c.symbol, market=c.market, name=c.name, sector=c.sector,
            price=c.price, change_pct=c.change_pct, pe=c.pe, pb=c.pb,
            market_cap=c.market_cap, market_cap_yi=cap_yi,
            dividend_yield=c.dividend_yield, volume=c.volume,
            turnover=c.turnover, turnover_rate=c.turnover_rate,
        )


class ScreenerRunResponse(BaseModel):
    market: str
    generated_at: str
    universe_size: int
    count: int
    candidates: list[CandidateOut]


class PresetOut(BaseModel):
    id: str
    name: str
    desc: str
    criteria: dict


class MoversResponse(BaseModel):
    market: str
    generated_at: str
    gainers: list[CandidateOut]
    losers: list[CandidateOut]


# ── Routes ────────────────────────────────────────────────────
@router.post("/run", response_model=ScreenerRunResponse)
async def run_screener(body: ScreenerFilter) -> ScreenerRunResponse:
    """按条件筛选面板标的，返回匹配列表（已排序、截断）。"""
    try:
        snapshot = await svc.get_snapshot(body.market)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"快照采集失败: {exc}")

    matched = svc.apply_filter(snapshot, body.to_criteria())
    return ScreenerRunResponse(
        market=body.market.value,
        generated_at=datetime.now(timezone.utc).isoformat(),
        universe_size=len(snapshot),
        count=len(matched),
        candidates=[CandidateOut.from_candidate(c) for c in matched],
    )


@router.get("/presets", response_model=list[PresetOut])
async def list_presets() -> list[PresetOut]:
    """返回预设筛选方案（含条件片段，供前端一键套用）。"""
    return [PresetOut(**p) for p in PRESETS]


@router.get("/sectors", response_model=list[str])
async def list_sectors() -> list[str]:
    """返回可用于过滤的行业标签。"""
    return list(SECTORS)


@router.get("/movers", response_model=MoversResponse)
async def get_movers(
    market: Annotated[Market, Query(description="市场: US / HK / A")] = Market.US,
    top: Annotated[int, Query(ge=1, le=30, description="每榜数量")] = 10,
) -> MoversResponse:
    """涨跌榜：从当前快照生成涨幅榜 / 跌幅榜。"""
    try:
        snapshot = await svc.get_snapshot(market)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"快照采集失败: {exc}")

    gainers, losers = svc.get_movers(snapshot, top)
    return MoversResponse(
        market=market.value,
        generated_at=datetime.now(timezone.utc).isoformat(),
        gainers=[CandidateOut.from_candidate(c) for c in gainers],
        losers=[CandidateOut.from_candidate(c) for c in losers],
    )
