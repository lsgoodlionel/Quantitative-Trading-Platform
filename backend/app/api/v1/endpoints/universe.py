"""
市值/成分股宇宙 API（M7）

- GET /universe/market-cap        按市值取前 N（返回体带 as_of）
- GET /universe/indexes           受支持的指数列表
- GET /universe/index/{code}      指数成分股（返回体带 as_of）

`as_of` 是**数据快照时间**而非请求时间：市值榜带 6 小时缓存，
调用方必须能看出手上这份数据有多旧。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.data.models import Market
from app.data.universe import (
    MAX_TOP_N,
    SUPPORTED_INDEXES,
    ComponentsFetchError,
    HistoricalComponentsUnavailableError,
    IndexNotSupportedError,
    index_components_snapshot,
    top_by_market_cap,
)

router = APIRouter()


class UniverseItemOut(BaseModel):
    symbol: str
    market: str
    name: str = ""
    market_cap: float | None = None
    market_cap_yi: float | None = None
    rank: int | None = None
    as_of: str = ""


class MarketCapResponse(BaseModel):
    market: str
    as_of: str
    count: int
    symbols: list[str]
    items: list[UniverseItemOut]


class IndexSpecOut(BaseModel):
    code: str
    name: str
    market: str


class IndexComponentsResponse(BaseModel):
    index_code: str
    index_name: str
    as_of: str | None
    count: int
    symbols: list[str]


@router.get("/market-cap", response_model=MarketCapResponse)
async def get_market_cap_universe(
    market: Annotated[Market, Query(description="市场: US / HK / A")] = Market.US,
    top_n: Annotated[int, Query(ge=1, le=MAX_TOP_N, description="取前 N")] = 50,
    exclude: Annotated[str, Query(description="逗号分隔的排除代码")] = "",
) -> MarketCapResponse:
    """按总市值取前 N 只标的。"""
    blocked = {s.strip() for s in exclude.split(",") if s.strip()}
    try:
        items = await top_by_market_cap(market, top_n, exclude=blocked)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"市值榜单采集失败: {exc}") from exc

    return MarketCapResponse(
        market=market.value,
        as_of=items[0].as_of if items else "",
        count=len(items),
        symbols=[it.symbol for it in items],
        items=[UniverseItemOut(**it.to_dict()) for it in items],
    )


@router.get("/indexes", response_model=list[IndexSpecOut])
async def list_supported_indexes() -> list[IndexSpecOut]:
    """列出当前有成分股数据源的指数。"""
    return [
        IndexSpecOut(code=spec.code, name=spec.name, market=spec.market.value)
        for spec in sorted(SUPPORTED_INDEXES.values(), key=lambda s: s.code)
    ]


@router.get("/index/{index_code}", response_model=IndexComponentsResponse)
async def get_index_components(
    index_code: Annotated[str, Path(description="指数代码或别名，如 000300 / HS300")],
    on: Annotated[date | None, Query(description="取该日的成分；留空=最新")] = None,
) -> IndexComponentsResponse:
    """
    取指数成分股。

    `on` 早于数据源快照日期时返回 **422** 而不是静默给最新成分 ——
    用历史区间回测配今天的成分股是典型的幸存者偏差。
    """
    try:
        snapshot = await index_components_snapshot(index_code, on=on)
    except IndexNotSupportedError as exc:
        raise HTTPException(404, str(exc)) from exc
    except HistoricalComponentsUnavailableError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ComponentsFetchError as exc:
        raise HTTPException(503, str(exc)) from exc

    return IndexComponentsResponse(
        index_code=snapshot.index_code,
        index_name=snapshot.index_name,
        as_of=snapshot.as_of.isoformat() if snapshot.as_of else None,
        count=len(snapshot.symbols),
        symbols=list(snapshot.symbols),
    )
