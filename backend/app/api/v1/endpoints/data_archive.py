"""
本地数据归档 API（M1）

- POST   /data/archive/download                     提交 Celery 下载任务
- GET    /data/archive                              列出已归档 key 与覆盖区间
- DELETE /data/archive/{market}/{frequency}/{symbol} 删除单个归档

⚠️ `symbol` 会进文件路径且来自入参，路径穿越防护在 `ArchiveKey` 构造时统一做，
   这里把 `InvalidSymbolError` 翻成 400，不让它变成 500。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from app.data.archive import (
    ArchiveError,
    ArchiveKey,
    InvalidSymbolError,
    available_engine,
    create_archive,
)
from app.data.models import Frequency, Market

router = APIRouter()

MAX_DOWNLOAD_SYMBOLS = 200


# ── Schemas ───────────────────────────────────────────────────
class ArchiveDownloadRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_DOWNLOAD_SYMBOLS)
    market: Market = Market.US
    frequency: Frequency = Frequency.DAY_1
    start: date | None = Field(None, description="起始日（含）；留空=一年前")
    end: date | None = Field(None, description="结束日（含）；留空=今天")


class ArchiveDownloadResponse(BaseModel):
    task_id: str
    symbols: int


class ArchiveEntry(BaseModel):
    symbol: str
    market: str
    frequency: str
    start: str | None = None
    end: str | None = None


class ArchiveListResponse(BaseModel):
    backend: str
    root: str
    enabled: bool
    count: int
    entries: list[ArchiveEntry]


# ── 提交下载任务 ──────────────────────────────────────────────
@router.post("/archive/download", response_model=ArchiveDownloadResponse)
async def submit_download(body: ArchiveDownloadRequest) -> ArchiveDownloadResponse:
    """提交归档下载任务。入参在这里同步校验完，别让用户轮询半天才看到代码非法。"""
    if body.start and body.end and body.end < body.start:
        raise HTTPException(400, f"end {body.end} 早于 start {body.start}")

    try:
        for symbol in body.symbols:
            ArchiveKey(symbol=symbol, market=body.market, frequency=body.frequency)
    except InvalidSymbolError as exc:
        raise HTTPException(400, str(exc)) from exc

    from app.tasks.archive import download_archive

    try:
        task = download_archive.delay(
            symbols=body.symbols,
            market=body.market.value,
            frequency=body.frequency.value,
            start=body.start.isoformat() if body.start else None,
            end=body.end.isoformat() if body.end else None,
        )
    except Exception as exc:  # broker 不可用
        raise HTTPException(503, f"任务队列不可用: {exc}") from exc

    return ArchiveDownloadResponse(task_id=task.id, symbols=len(body.symbols))


# ── 列出归档 ──────────────────────────────────────────────────
@router.get("/archive", response_model=ArchiveListResponse)
async def list_archive() -> ArchiveListResponse:
    """列出全部已归档条目与各自的覆盖区间。"""
    from app.core.config import settings

    archive = _archive_or_503()
    entries: list[ArchiveEntry] = []
    for key in archive.list_keys():
        coverage = archive.coverage(key)
        entries.append(
            ArchiveEntry(
                symbol=key.symbol,
                market=key.market.value,
                frequency=key.frequency.value,
                start=coverage[0].isoformat() if coverage else None,
                end=coverage[1].isoformat() if coverage else None,
            )
        )

    return ArchiveListResponse(
        backend=available_engine() or "npz",
        root=str(archive.root),
        enabled=settings.archive_enabled,
        count=len(entries),
        entries=entries,
    )


# ── 删除归档 ──────────────────────────────────────────────────
@router.delete("/archive/{market}/{frequency}/{symbol}", status_code=204)
async def delete_archive(
    market: Annotated[Market, Path(description="市场")],
    frequency: Annotated[Frequency, Path(description="K 线周期")],
    symbol: Annotated[str, Path(description="标的代码")],
) -> None:
    """删除单个归档文件。"""
    archive = _archive_or_503()
    try:
        key = ArchiveKey(symbol=symbol, market=market, frequency=frequency)
    except InvalidSymbolError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not archive.delete(key):
        raise HTTPException(404, f"归档不存在: {market.value}/{frequency.value}/{symbol}")


def _archive_or_503():
    try:
        return create_archive()
    except ArchiveError as exc:
        raise HTTPException(503, f"归档后端不可用: {exc}") from exc
