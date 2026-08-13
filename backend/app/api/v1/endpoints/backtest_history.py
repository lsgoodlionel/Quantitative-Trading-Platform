"""
回测历史 API（V3 · H5）

- POST   /backtests/history            保存一次回测结果
- GET    /backtests/history            列表（分页 + 按策略/标的/市场/日期筛选）
- GET    /backtests/history/compare    多条记录对比（曲线对齐 + 指标并列）
- GET    /backtests/history/{id}       详情（含净值曲线）
- DELETE /backtests/history/{id}       删除
- POST   /backtests/history/{id}/rerun 用原配置重跑

存储介质与净值曲线体积策略见 `app/data/storage/backtest_history.py` 模块 docstring：
落 TimescaleDB、无容量上限、曲线降采样至 1000 点后整条落库、配置完整保存以支持精确重跑。

⚠️ 路由顺序：`/history/compare` 必须注册在 `/history/{record_id}` 之前，
否则 "compare" 会被当成 record_id 吞掉。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Frequency, Market
from app.data.service import DataService
from app.data.storage.backtest_history import (
    MAX_COMPARE_ITEMS,
    MAX_PAGE_SIZE,
    BacktestHistoryStore,
    HistoryFilter,
    PostgresBacktestHistoryStore,
    build_record,
)
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.history_compare import build_comparison
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()

_A_ALLOWED_FREQS = {Frequency.DAY_1, Frequency.WEEK_1}
_MIN_BARS = 5


# ── Schemas ──────────────────────────────────────────────────────

class SaveHistoryRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(..., ge=1000)
    final_value: float
    params: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    equity_curve: list[dict] = Field(default_factory=list, description="[{time, value}]，落库前会降采样")
    name: str = ""
    note: str = ""


class HistoryListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[dict]
    storage: str = Field("timescaledb", description="存储介质：无容量上限，历史不会被自动淘汰")


# ── 依赖注入 ─────────────────────────────────────────────────────

def get_store(session: AsyncSession = Depends(get_db)) -> BacktestHistoryStore:
    return PostgresBacktestHistoryStore(session)


def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


def _parse_uuid(record_id: str) -> str:
    try:
        return str(uuid.UUID(record_id))
    except ValueError:
        raise HTTPException(400, f"非法记录 ID: {record_id}") from None


# ── 端点：保存 / 列表 ─────────────────────────────────────────────

@router.post("/history", status_code=201)
async def save_history(
    body: SaveHistoryRequest,
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
) -> dict:
    """保存一次回测结果（配置 + 指标 + 降采样净值曲线）。"""
    if body.strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"未知策略 '{body.strategy_name}'")
    record = build_record(
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        market=body.market,
        frequency=body.frequency,
        start_date=body.start_date.isoformat(),
        end_date=body.end_date.isoformat(),
        initial_cash=body.initial_cash,
        final_value=body.final_value,
        params=body.params,
        metrics=body.metrics,
        equity_curve=body.equity_curve,
        name=body.name,
        note=body.note,
    )
    try:
        saved = await store.save(record)
    except Exception as e:
        raise HTTPException(503, f"保存回测历史失败: {e}") from e
    return saved.to_dict()


@router.get("/history", response_model=HistoryListResponse)
async def list_history(
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
    strategy_name: str | None = None,
    symbol: str | None = None,
    market: str | None = None,
    start_after: date | None = None,
    end_before: date | None = None,
    limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> HistoryListResponse:
    """分页列出回测历史（倒序），支持按策略/标的/市场/日期筛选。列表不含净值曲线。"""
    filters = HistoryFilter(
        strategy_name=strategy_name, symbol=symbol, market=market,
        start_after=start_after, end_before=end_before,
    )
    try:
        records, total = await store.list(filters, limit, offset)
    except Exception as e:
        raise HTTPException(503, f"读取回测历史失败: {e}") from e
    return HistoryListResponse(
        total=total, limit=limit, offset=offset,
        items=[r.summary() for r in records],
    )


# ── 端点：对比（必须在 /{record_id} 之前） ────────────────────────

@router.get("/history/compare")
async def compare_history(
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
    ids: str = Query(..., description=f"逗号分隔的记录 ID，最多 {MAX_COMPARE_ITEMS} 条"),
) -> dict:
    """对比多条历史：净值曲线对齐到公共时间轴 + 关键指标并列。"""
    wanted = [_parse_uuid(i) for i in (s.strip() for s in ids.split(",")) if i]
    if not wanted:
        raise HTTPException(400, "ids 不能为空")
    if len(wanted) > MAX_COMPARE_ITEMS:
        raise HTTPException(400, f"最多对比 {MAX_COMPARE_ITEMS} 条，当前 {len(wanted)} 条")
    try:
        records = await store.get_many(wanted)
    except Exception as e:
        raise HTTPException(503, f"读取回测历史失败: {e}") from e
    if not records:
        raise HTTPException(404, "未找到任何指定的回测历史")
    missing = [rid for rid in wanted if all(r.id != rid for r in records)]
    return {**build_comparison(records), "missing_ids": missing}


# ── 端点：详情 / 删除 / 重跑 ──────────────────────────────────────

@router.get("/history/{record_id}")
async def get_history(
    record_id: str,
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
) -> dict:
    """回测历史详情（含降采样后的净值曲线与完整配置）。"""
    record = await store.get(_parse_uuid(record_id))
    if record is None:
        raise HTTPException(404, f"回测历史不存在: {record_id}")
    return record.to_dict()


@router.delete("/history/{record_id}", status_code=204)
async def delete_history(
    record_id: str,
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
) -> None:
    """删除一条回测历史（本期只做手动删除，无自动清理策略）。"""
    if not await store.delete(_parse_uuid(record_id)):
        raise HTTPException(404, f"回测历史不存在: {record_id}")


@router.post("/history/{record_id}/rerun")
async def rerun_history(
    record_id: str,
    store: Annotated[BacktestHistoryStore, Depends(get_store)],
    svc: Annotated[DataService, Depends(get_service)],
) -> dict:
    """
    用原配置重跑回测。

    配置（策略/参数/市场/频率/区间/初始资金）完整落库，因此在同一份行情下
    重跑结果与原记录一致；若指标出现差异，说明底层行情或复权数据发生了变化，
    响应里的 `metrics_changed` 会置为 true。
    """
    record = await store.get(_parse_uuid(record_id))
    if record is None:
        raise HTTPException(404, f"回测历史不存在: {record_id}")

    market, bars = await _fetch_bars_for(record, svc)
    strategy_cls = STRATEGY_REGISTRY.get(record.strategy_name)
    if strategy_cls is None:
        raise HTTPException(400, f"策略已下线，无法重跑: {record.strategy_name}")

    engine = BacktestEngine(BacktestConfig(initial_cash=record.initial_cash, market=market))
    try:
        result = engine.run(strategy_cls(params=record.params), bars)
    except Exception as e:
        raise HTTPException(500, f"回测引擎错误: {e}") from e

    metrics = result.report["metrics"]
    return {
        "source_id": record.id,
        "config": {
            "strategy_name": record.strategy_name,
            "symbol": record.symbol,
            "market": record.market,
            "frequency": record.frequency,
            "start_date": record.start_date,
            "end_date": record.end_date,
            "initial_cash": record.initial_cash,
            "params": record.params,
        },
        "final_value": result.final_value,
        "metrics": metrics,
        "equity_curve": result.report["equity_curve"],
        "metrics_changed": _metrics_differ(record.metrics, metrics),
    }


async def _fetch_bars_for(record, svc: DataService):
    """按历史记录里的原配置取行情。"""
    try:
        market = Market(record.market.upper())
        frequency = Frequency(record.frequency)
    except ValueError as e:
        raise HTTPException(400, f"历史记录配置非法: {e}") from e
    if market == Market.A and frequency not in _A_ALLOWED_FREQS:
        raise HTTPException(400, f"A股不支持频率: {record.frequency}")
    try:
        bars = await svc.get_bars(
            symbol=record.symbol, market=market, frequency=frequency,
            start=date.fromisoformat(record.start_date),
            end=date.fromisoformat(record.end_date),
        )
    except Exception as e:
        raise HTTPException(503, f"获取行情失败: {e}") from e
    if len(bars) < _MIN_BARS:
        raise HTTPException(422, f"数据不足：仅获取到 {len(bars)} 根 K 线，无法重跑。")
    return market, bars


def _metrics_differ(original: dict, current: dict) -> bool:
    """比较关键指标是否变化（浮点按 1e-6 容差）。"""
    for key, old in original.items():
        new = current.get(key)
        if isinstance(old, int | float) and isinstance(new, int | float):
            if abs(float(old) - float(new)) > 1e-6:
                return True
        elif old != new:
            return True
    return False
