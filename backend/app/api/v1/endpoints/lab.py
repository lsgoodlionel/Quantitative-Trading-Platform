"""
投研产物库 API（V4 · M4）

- GET    /lab/artifacts               列表（按类别/名称筛选 + 分页），附容量策略
- GET    /lab/artifacts/{id}          元数据详情
- GET    /lab/artifacts/{id}/preview  数据集/信号的前若干行预览
- GET    /lab/artifacts/{id}/detail   模型产物的 `detail()`（特征重要性/训练曲线/超参）
- DELETE /lab/artifacts/{id}          删除（元数据 + 内容文件）

**刻意不提供上传端点。** 产物只由服务端的训练/回测流程写入 —— 内容是 pickle，
接受外部上传等于开一个远程代码执行的口子（见 `app/quant/lab/base.py` 模块 docstring）。
将来真要支持导入外部模型，先换成非 pickle 的序列化格式再说。

容量策略：**无上限、仅手动删除**，通过 `capacity_policy` 字段明示给前端 ——
不重蹈实验记录器「悄悄丢数据」的覆辙。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.quant.lab.base import (
    CAPACITY_POLICY,
    ArtifactKind,
    ArtifactKindMismatchError,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
    LabError,
)
from app.quant.lab.content import FileSystemContentStore
from app.quant.lab.metadata import MAX_PAGE_SIZE, PostgresArtifactMetaStore
from app.quant.lab.store import DEFAULT_LIST_LIMIT, LabStore

router = APIRouter()

# 预览返回的最大行数
MAX_PREVIEW_ROWS = 50

CAPACITY_NOTE = "产物不设容量上限，也不会被自动淘汰；清理请手动调用 DELETE 接口。"


class ArtifactListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[dict]
    capacity_policy: str = Field(CAPACITY_POLICY, description="容量策略标识")
    capacity_note: str = Field(CAPACITY_NOTE, description="面向用户的容量策略说明")


def get_store(session: AsyncSession = Depends(get_db)) -> LabStore:
    return LabStore(PostgresArtifactMetaStore(session), FileSystemContentStore())


StoreDep = Annotated[LabStore, Depends(get_store)]


def _to_http_error(exc: LabError) -> HTTPException:
    """产物库异常 → HTTP 状态码。checksum 失败是 409：内容与元数据对不上。"""
    from app.quant.lab.base import ArtifactChecksumError

    if isinstance(exc, InvalidArtifactIdError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ArtifactNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ArtifactChecksumError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ArtifactKindMismatchError):
        return HTTPException(400, str(exc))
    return HTTPException(500, f"产物库错误: {exc}")


@router.get("/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    store: StoreDep,
    kind: ArtifactKind | None = Query(None, description="按类别筛选"),
    name_contains: str | None = Query(None, description="名称模糊匹配"),
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> ArtifactListResponse:
    """列出产物元数据（按创建时间倒序）。"""
    try:
        items, total = await store.list(
            kind=kind, name_contains=name_contains, limit=limit, offset=offset
        )
    except LabError as exc:
        raise _to_http_error(exc) from exc
    return ArtifactListResponse(
        total=total, limit=limit, offset=offset, items=[m.to_dict() for m in items]
    )


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, store: StoreDep) -> dict:
    """产物元数据详情（不读取内容文件）。"""
    try:
        meta = await store.get(artifact_id)
    except LabError as exc:
        raise _to_http_error(exc) from exc
    if meta is None:
        raise HTTPException(404, f"产物不存在: {artifact_id}")
    return meta.to_dict()


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(
    artifact_id: str,
    store: StoreDep,
    rows: int = Query(10, ge=1, le=MAX_PREVIEW_ROWS),
) -> dict:
    """数据集 / 信号的前若干行预览。模型产物请用 `/detail`。"""
    try:
        meta = await store.get(artifact_id)
        if meta is None:
            raise HTTPException(404, f"产物不存在: {artifact_id}")
        if meta.kind is ArtifactKind.MODEL:
            raise HTTPException(400, "模型产物没有行预览，请使用 /detail 端点")
        content = (
            await store.load_dataset(artifact_id)
            if meta.kind is ArtifactKind.DATASET
            else await store.load_signal(artifact_id)
        )
    except LabError as exc:
        raise _to_http_error(exc) from exc

    head = content.head(rows)
    frame = head.to_frame() if meta.kind is ArtifactKind.SIGNAL else head
    return {
        "artifact_id": meta.artifact_id,
        "kind": meta.kind.value,
        "columns": [str(c) for c in frame.columns],
        "rows": [
            {"index": str(idx), **{str(k): _jsonable(v) for k, v in row.items()}}
            for idx, row in frame.iterrows()
        ],
        "total_rows": int(len(content)),
    }


@router.get("/artifacts/{artifact_id}/detail")
async def model_detail(artifact_id: str, store: StoreDep) -> dict:
    """加载模型产物并返回其 `detail()`（特征重要性 / 训练曲线 / 超参）。"""
    try:
        model = await store.load_model(artifact_id)
    except LabError as exc:
        raise _to_http_error(exc) from exc
    detail = getattr(model, "detail", None)
    if not callable(detail):
        raise HTTPException(400, "该模型产物未实现 detail()")
    return {"artifact_id": artifact_id, "detail": detail()}


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str, store: StoreDep) -> dict:
    """删除产物（元数据 + 内容文件）。这是唯一的清理途径 —— 系统不会自动淘汰。"""
    try:
        removed = await store.delete(artifact_id)
    except LabError as exc:
        raise _to_http_error(exc) from exc
    if not removed:
        raise HTTPException(404, f"产物不存在: {artifact_id}")
    return {"artifact_id": artifact_id, "deleted": True}


def _jsonable(value: Any) -> Any:
    """numpy / pandas 标量转成能 JSON 化的原生类型。"""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):
            return str(value)
    return value if isinstance(value, (int, float, str, bool, type(None))) else str(value)
