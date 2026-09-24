"""
投研产物库 —— 元数据仓储（V4 · M4）

**存储介质：TimescaleDB / PostgreSQL**（与回测历史同库，见
`app/data/storage/backtest_history.py`）。元数据要能按类别 / 名称 / 时间查询筛选，
这是关系库的活；内容（pickle 字节流）在 `content.py` 走文件系统。

建表：随 `infra/init-db/04_lab_artifacts.sql` 初始化；对已存在的部署，
仓储首次使用时会执行一次幂等的 `ensure_schema()`（项目未引入 alembic 迁移流程）。

容量：**无上限**，清理走手动删除接口，见 `base.CAPACITY_POLICY`。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.quant.lab.base import (
    ArtifactFilter,
    ArtifactKind,
    ArtifactMeta,
    validate_artifact_id,
)

logger = get_logger(__name__)

# 列表接口单页上限
MAX_PAGE_SIZE = 200

_DDL = """
CREATE TABLE IF NOT EXISTS lab_artifacts (
    artifact_id UUID          PRIMARY KEY,
    kind        VARCHAR(20)   NOT NULL,
    name        VARCHAR(300)  NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    size_bytes  BIGINT        NOT NULL DEFAULT 0,
    tags        JSONB         NOT NULL DEFAULT '{}',
    checksum    VARCHAR(64)   NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lab_artifacts_created ON lab_artifacts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lab_artifacts_kind    ON lab_artifacts(kind, created_at DESC);
"""

_INSERT_SQL = text("""
    INSERT INTO lab_artifacts (artifact_id, kind, name, created_at, size_bytes, tags, checksum)
    VALUES (CAST(:artifact_id AS UUID), :kind, :name, :created_at, :size_bytes,
            CAST(:tags AS JSONB), :checksum)
""")

_SELECT_COLUMNS = "artifact_id, kind, name, created_at, size_bytes, tags, checksum"

_DELETE_SQL = text("DELETE FROM lab_artifacts WHERE artifact_id = CAST(:artifact_id AS UUID)")


def _as_tags(value: Any) -> dict[str, str]:
    """asyncpg 返回 dict，其他驱动可能返回字符串 —— 两种都要能解。"""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Malformed tags column in lab_artifacts")
            return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _row_to_meta(row: Any) -> ArtifactMeta:
    created = row.created_at
    return ArtifactMeta(
        artifact_id=str(row.artifact_id),
        kind=ArtifactKind(row.kind),
        name=row.name or "",
        created_at=created if isinstance(created, datetime) else datetime.now(UTC),
        size_bytes=int(row.size_bytes),
        tags=_as_tags(row.tags),
        checksum=row.checksum or "",
    )


def _build_where(filters: ArtifactFilter) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.kind is not None:
        clauses.append("kind = :kind")
        params["kind"] = filters.kind.value
    if filters.name_contains:
        clauses.append("name ILIKE :name_pattern")
        params["name_pattern"] = f"%{filters.name_contains}%"
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


class PostgresArtifactMetaStore:
    """基于 TimescaleDB / PostgreSQL 的产物元数据仓储。"""

    _schema_ready = False

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_schema(self) -> None:
        """幂等建表（项目无 alembic 迁移流程，首次使用时补齐）。"""
        if PostgresArtifactMetaStore._schema_ready:
            return
        for statement in filter(None, (s.strip() for s in _DDL.split(";"))):
            await self._session.execute(text(statement))
        PostgresArtifactMetaStore._schema_ready = True

    async def save(self, meta: ArtifactMeta) -> ArtifactMeta:
        await self.ensure_schema()
        await self._session.execute(
            _INSERT_SQL,
            {
                "artifact_id": validate_artifact_id(meta.artifact_id),
                "kind": meta.kind.value,
                "name": meta.name,
                "created_at": meta.created_at,
                "size_bytes": meta.size_bytes,
                "tags": json.dumps(meta.tags, ensure_ascii=False),
                "checksum": meta.checksum,
            },
        )
        await self._session.commit()
        logger.info("Saved lab artifact meta", artifact_id=meta.artifact_id, kind=meta.kind.value)
        return meta

    async def get(self, artifact_id: str) -> ArtifactMeta | None:
        await self.ensure_schema()
        row = (
            await self._session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM lab_artifacts "
                    "WHERE artifact_id = CAST(:artifact_id AS UUID)"
                ),
                {"artifact_id": validate_artifact_id(artifact_id)},
            )
        ).fetchone()
        return _row_to_meta(row) if row else None

    async def list(
        self, filters: ArtifactFilter, limit: int, offset: int
    ) -> tuple[list[ArtifactMeta], int]:
        await self.ensure_schema()
        where, params = _build_where(filters)
        total_row = (
            await self._session.execute(
                text(f"SELECT COUNT(*) AS n FROM lab_artifacts {where}"), params
            )
        ).fetchone()
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM lab_artifacts {where} "
                    "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).fetchall()
        return [_row_to_meta(r) for r in rows], int(total_row.n) if total_row else 0

    async def delete(self, artifact_id: str) -> bool:
        await self.ensure_schema()
        result = await self._session.execute(
            _DELETE_SQL, {"artifact_id": validate_artifact_id(artifact_id)}
        )
        await self._session.commit()
        return bool(result.rowcount)
