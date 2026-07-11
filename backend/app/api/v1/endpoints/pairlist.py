"""动态标的池 API（Epic E / E5）。

- POST /screener/pairlist              规则链 → 可交易 universe
- GET  /screener/pairlist/saved        列出已保存的标的池
- PUT  /screener/pairlist/saved        新建 / 更新一个标的池（存 Redis）
- DELETE /screener/pairlist/saved/{id} 删除一个标的池

区别于 /screener/run（一次性筛选）：标的池是可保存、可被策略引用的规则链。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.core.redis import get_redis
from app.data import pairlist as svc
from app.data.models import Market
from app.data.pairlist import PairlistRule

router = APIRouter()

_SAVED_KEY = "screener:pairlist:saved"   # Redis hash: id → json
_MAX_RULES = 12
_MAX_SAVED = 50

RuleKind = Literal["volume", "price", "market_cap", "volatility", "performance", "spread"]


# ── Schemas ───────────────────────────────────────────────────
class PairlistRuleModel(BaseModel):
    kind: RuleKind = Field(description="过滤维度")
    min_value: float | None = Field(None, description="下界（市值单位=亿；波动/表现/价差=%）")
    max_value: float | None = Field(None, description="上界")
    sort: Literal["asc", "desc"] | None = Field(None, description="按该维度排序")
    top: int | None = Field(None, ge=1, le=200, description="保留头部 N 个")

    def to_rule(self) -> PairlistRule:
        return PairlistRule(
            kind=self.kind, min_value=self.min_value, max_value=self.max_value,
            sort=self.sort, top=self.top,
        )


class PairlistRunRequest(BaseModel):
    market: Market = Field(Market.US, description="市场: US / HK / A")
    rules: list[PairlistRuleModel] = Field(default_factory=list, max_length=_MAX_RULES)
    lookback_days: int = Field(20, ge=2, le=120, description="波动/表现/价差回看天数")


class PairMetricsOut(BaseModel):
    symbol: str
    market: str
    name: str
    sector: str
    price: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    turnover: float | None = None
    market_cap: float | None = None
    market_cap_yi: float | None = None
    volatility: float | None = None
    performance: float | None = None
    spread_proxy: float | None = None


class PairlistRunResponse(BaseModel):
    market: str
    generated_at: str
    lookback_days: int
    universe_size: int
    count: int
    symbols: list[str]
    items: list[PairMetricsOut]


class SavedPairlist(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=60)
    market: Market
    rules: list[PairlistRuleModel] = Field(default_factory=list, max_length=_MAX_RULES)
    lookback_days: int = Field(20, ge=2, le=120)
    created_at: str | None = None
    updated_at: str | None = None


class SavePairlistRequest(BaseModel):
    id: str | None = Field(None, description="留空=新建；提供=更新")
    name: str = Field(min_length=1, max_length=60)
    market: Market = Market.US
    rules: list[PairlistRuleModel] = Field(default_factory=list, max_length=_MAX_RULES)
    lookback_days: int = Field(20, ge=2, le=120)


# ── 运行规则链 ────────────────────────────────────────────────
@router.post("/pairlist", response_model=PairlistRunResponse)
async def run_pairlist(body: PairlistRunRequest) -> PairlistRunResponse:
    """按规则链构建可交易 universe（有序链式过滤，复用筛选器快照）。"""
    lookback = svc.clamp_lookback(body.lookback_days)
    rules = [r.to_rule() for r in body.rules]
    try:
        universe = await svc.build_universe(body.market, rules, lookback)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"标的池数据采集失败: {exc}")

    matched = svc.apply_chain(universe, rules)
    items = [PairMetricsOut(**svc.metrics_to_dict(m)) for m in matched]
    return PairlistRunResponse(
        market=body.market.value,
        generated_at=datetime.now(timezone.utc).isoformat(),
        lookback_days=lookback,
        universe_size=len(universe),
        count=len(matched),
        symbols=[m.symbol for m in matched],
        items=items,
    )


# ── 已保存标的池（Redis）──────────────────────────────────────
def _parse_saved(raw: str) -> SavedPairlist | None:
    try:
        return SavedPairlist(**json.loads(raw))
    except Exception:  # noqa: BLE001
        return None


@router.get("/pairlist/saved", response_model=list[SavedPairlist])
async def list_saved(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> list[SavedPairlist]:
    """列出全部已保存标的池（按更新时间倒序）。"""
    try:
        raw_map = await redis.hgetall(_SAVED_KEY)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"读取标的池失败: {exc}")

    saved = [p for p in (_parse_saved(v) for v in raw_map.values()) if p is not None]
    saved.sort(key=lambda p: p.updated_at or "", reverse=True)
    return saved


@router.put("/pairlist/saved", response_model=SavedPairlist)
async def upsert_saved(
    body: SavePairlistRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> SavedPairlist:
    """新建或更新一个标的池。id 留空则新建。"""
    now = datetime.now(timezone.utc).isoformat()
    pid = body.id or uuid.uuid4().hex[:12]

    created_at = now
    if body.id:
        existing = await redis.hget(_SAVED_KEY, body.id)
        if existing is None:
            raise HTTPException(status_code=404, detail="标的池不存在")
        prev = _parse_saved(existing)
        if prev and prev.created_at:
            created_at = prev.created_at
    else:
        count = await redis.hlen(_SAVED_KEY)
        if count >= _MAX_SAVED:
            raise HTTPException(status_code=400, detail=f"已保存标的池达上限（{_MAX_SAVED}）")

    saved = SavedPairlist(
        id=pid, name=body.name, market=body.market, rules=body.rules,
        lookback_days=body.lookback_days, created_at=created_at, updated_at=now,
    )
    await redis.hset(_SAVED_KEY, pid, saved.model_dump_json())
    return saved


@router.delete("/pairlist/saved/{pid}", status_code=204)
async def delete_saved(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    pid: Annotated[str, Path(description="标的池 ID")],
) -> None:
    """删除一个已保存标的池。"""
    removed = await redis.hdel(_SAVED_KEY, pid)
    if not removed:
        raise HTTPException(status_code=404, detail="标的池不存在")
