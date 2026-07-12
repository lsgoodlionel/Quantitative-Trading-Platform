"""
多源数据通道 API — 状态探活 + 多源配置

- GET  /data-sources/status  实时探活每市场每个源（真实拉取一根最新K线，测延迟）
- GET  /data-sources/config  当前配置（顺序/禁用/强制）+ 源目录元数据
- PUT  /data-sources/config  更新某市场配置，持久化 Redis + 热重载注册表

配合前端设置页：实时状态点 + 延迟 + 启停 + 排序 + 强制使用（pin）。
真实源全部不可用时，DataService 仍以合成演示源兜底，平台永不断供。
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.rbac import Role, require_role
from app.core.redis import get_redis
from app.data.models import Frequency, Market
from app.data.source_registry import (
    SOURCE_CATALOG,
    DataSourceRegistry,
    MarketSourceConfig,
)

router = APIRouter()

# 各市场探活用的代表标的
_PROBE_SYMBOL = {"US": "AAPL", "HK": "00700", "A": "000001"}
_MARKET_LABEL = {"US": "🇺🇸 美股", "HK": "🇭🇰 港股", "A": "🇨🇳 沪深 A 股"}
_PROBE_TIMEOUT = 8.0


# ── Schemas ──────────────────────────────────────────────────────

class SourceStatus(BaseModel):
    id: str
    name: str
    requires: str | None
    realtime: bool
    note: str
    enabled: bool
    pinned: bool
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


class MarketSources(BaseModel):
    market: str
    label: str
    sources: list[SourceStatus]
    active_source: str | None            # 首个探活成功的源 id（当前实际生效）
    has_realtime: bool


class StatusResponse(BaseModel):
    markets: dict[str, MarketSources]


class SourceMetaOut(BaseModel):
    id: str
    name: str
    requires: str | None
    realtime: bool
    note: str


class MarketConfigOut(BaseModel):
    order: list[str]
    disabled: list[str]
    pinned: str | None


class ConfigResponse(BaseModel):
    catalog: dict[str, list[SourceMetaOut]]
    config: dict[str, MarketConfigOut]


class ConfigUpdateRequest(BaseModel):
    market: Literal["US", "HK", "A"]
    order: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    pinned: str | None = None


# ── 探活 ─────────────────────────────────────────────────────────

async def _probe_source(feed, market: str) -> tuple[bool, int | None, str | None]:
    """真实拉取一根最新日线，返回 (ok, latency_ms, error)。"""
    if feed is None:
        return False, None, "数据源不可用"
    symbol = _PROBE_SYMBOL[market]
    t0 = time.monotonic()
    try:
        bar = await asyncio.wait_for(
            feed.get_latest_bar(symbol, Frequency.DAY_1), timeout=_PROBE_TIMEOUT
        )
        latency = int((time.monotonic() - t0) * 1000)
        if bar is None:
            return False, latency, "返回空数据"
        return True, latency, None
    except asyncio.TimeoutError:
        return False, int(_PROBE_TIMEOUT * 1000), "探活超时"
    except Exception as e:  # noqa: BLE001
        err = str(e)
        if "Connection refused" in err or "ECONNREFUSED" in err:
            err = "连接被拒（服务未运行）"
        elif len(err) > 100:
            err = err[:100] + "…"
        return False, None, err


async def _probe_market(reg: DataSourceRegistry, market: str) -> MarketSources:
    sources = list(reg.iter_sources(market))
    # 并发探活所有源
    results = await asyncio.gather(
        *[_probe_source(feed, market) for _sid, _meta, feed, _flags in sources]
    )
    out: list[SourceStatus] = []
    active: str | None = None
    has_rt = False
    for (sid, meta, _feed, flags), (ok, latency, err) in zip(sources, results):
        out.append(SourceStatus(
            id=sid, name=meta.name, requires=meta.requires, realtime=meta.realtime,
            note=meta.note, enabled=flags["enabled"], pinned=flags["pinned"],
            ok=ok, latency_ms=latency, error=err,
        ))
        if ok and flags["enabled"] and active is None:
            active = sid
        if ok and meta.realtime:
            has_rt = True
    return MarketSources(
        market=market, label=_MARKET_LABEL[market],
        sources=out, active_source=active, has_realtime=has_rt,
    )


# ── 端点 ─────────────────────────────────────────────────────────

@router.get("/status", response_model=StatusResponse)
async def get_sources_status() -> StatusResponse:
    """并发探活全部市场全部数据源（真实拉取，含延迟）。"""
    reg = DataSourceRegistry.instance()
    markets = await asyncio.gather(*[_probe_market(reg, m) for m in SOURCE_CATALOG])
    return StatusResponse(markets={m.market: m for m in markets})


@router.get("/config", response_model=ConfigResponse)
async def get_sources_config(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> ConfigResponse:
    """返回源目录 + 当前多源配置。"""
    reg = DataSourceRegistry.instance()
    await reg.load_config(redis)
    cfg = reg.get_config()
    return ConfigResponse(
        catalog={
            m: [SourceMetaOut(id=s.id, name=s.name, requires=s.requires,
                              realtime=s.realtime, note=s.note) for s in metas]
            for m, metas in SOURCE_CATALOG.items()
        },
        config={
            m: MarketConfigOut(order=c.order, disabled=c.disabled, pinned=c.pinned)
            for m, c in cfg.items()
        },
    )


@router.put("/config", response_model=MarketConfigOut)
async def update_sources_config(
    body: ConfigUpdateRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    _user: Annotated[object, Depends(require_role(Role.TRADER))],
) -> MarketConfigOut:
    """更新某市场多源配置（顺序/禁用/强制），持久化并热重载。需 Trader 及以上。"""
    valid = {s.id for s in SOURCE_CATALOG.get(body.market, [])}
    bad = [x for x in body.order + body.disabled if x not in valid]
    if bad:
        raise HTTPException(400, detail=f"未知数据源: {bad}（{body.market} 可用: {sorted(valid)}）")
    if body.pinned is not None and body.pinned not in valid:
        raise HTTPException(400, detail=f"强制源 {body.pinned} 不在 {body.market} 可用源中")

    reg = DataSourceRegistry.instance()
    await reg.load_config(redis)
    # 补全 order（漏掉的源追加末尾，禁用去重）
    order = [x for x in body.order if x in valid]
    for sid in valid:
        if sid not in order:
            order.append(sid)
    reg.set_market_config(body.market, MarketSourceConfig(
        order=order, disabled=list(dict.fromkeys(body.disabled)), pinned=body.pinned,
    ))
    await reg.save_config(redis)
    c = reg.get_config()[body.market]
    return MarketConfigOut(order=c.order, disabled=c.disabled, pinned=c.pinned)
