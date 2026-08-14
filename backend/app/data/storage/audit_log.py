"""
审计日志持久层（V3 · J3）

建表见 `infra/init-db/06_audit_log.sql`；对已存在的部署，仓储首次使用时执行一次
幂等的 `ensure_schema()`（项目未引入 alembic 迁移流程）。

## 为什么是「双写」而不是只留一个

* **Redis（`audit:log` stream）= 快速查询层。** maxlen 10000 近似裁剪，
  `GET /api/v1/audit` 的倒序分页扫描就靠它，延迟稳定且有界。
* **Postgres（本表）= 持久层。** 无容量上限，重启不丢，超过 1 万条也还在。

两者都写。读优先走 Redis，Redis 不可用或没命中时回落到 Postgres ——
「Redis 挂了就查不到审计」与「审计悄悄丢了」是同一种失败，只是发生的时刻不同。

## 不可变

只提供 `append()` 与 `list()`。**没有 update / delete**，API 层同样不暴露 ——
一个能被删除的审计日志，在真正需要它的那一刻就是空的。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 单次查询返回上限
MAX_PAGE_SIZE = 500


@dataclass(frozen=True)
class AuditEntry:
    """一条审计记录（不可变）。"""

    ts: str
    action: str
    actor: str
    detail: dict[str, Any]
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "action": self.action,
            "actor": self.actor,
            "detail": self.detail,
        }


class AuditLogStore(Protocol):
    """审计持久层接口。**刻意只有 append / list**（记录不可变）。"""

    async def append(self, entry: AuditEntry) -> None: ...

    async def list(
        self,
        *,
        action: str | None = None,
        actor: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditEntry], int]: ...


def build_entry(
    action: str, actor: str, detail: dict[str, Any] | None = None
) -> AuditEntry:
    """构造一条待落库的审计记录（时间戳取当前 UTC）。"""
    return AuditEntry(
        ts=datetime.now(UTC).isoformat(),
        action=action,
        actor=actor or "system",
        detail=dict(detail or {}),
    )


# ── SQL ───────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL     PRIMARY KEY,
    ts          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    action      VARCHAR(64)   NOT NULL,
    actor       VARCHAR(255)  NOT NULL DEFAULT 'system',
    detail      JSONB         NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts     ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor  ON audit_log(actor, ts DESC)
"""

_INSERT_SQL = text("""
    INSERT INTO audit_log (ts, action, actor, detail)
    VALUES (:ts, :action, :actor, CAST(:detail AS JSONB))
""")

_COLUMNS = "id, ts, action, actor, detail"


def _row_to_entry(row: Any) -> AuditEntry:
    raw_detail = row.detail
    if isinstance(raw_detail, str):
        try:
            raw_detail = json.loads(raw_detail)
        except json.JSONDecodeError:
            logger.error("audit_log.detail 不是合法 JSON，id=%s", row.id)
            raw_detail = {"raw": row.detail}
    ts = row.ts
    return AuditEntry(
        id=str(row.id),
        ts=ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        action=row.action,
        actor=row.actor or "system",
        detail=raw_detail if isinstance(raw_detail, dict) else {"value": raw_detail},
    )


class PostgresAuditLogStore:
    """基于 PostgreSQL / TimescaleDB 的审计持久层（append-only）。"""

    _schema_ready = False

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_schema(self) -> None:
        if PostgresAuditLogStore._schema_ready:
            return
        for statement in filter(None, (s.strip() for s in _DDL.split(";"))):
            await self._session.execute(text(statement))
        await self._session.commit()
        PostgresAuditLogStore._schema_ready = True

    async def append(self, entry: AuditEntry) -> None:
        """插入一条记录。**没有对应的 update/delete，这是刻意的。**"""
        await self.ensure_schema()
        await self._session.execute(
            _INSERT_SQL,
            {
                "ts": datetime.fromisoformat(entry.ts),
                "action": entry.action,
                "actor": entry.actor,
                "detail": json.dumps(entry.detail, ensure_ascii=False, default=str),
            },
        )
        await self._session.commit()

    async def list(
        self,
        *,
        action: str | None = None,
        actor: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditEntry], int]:
        """倒序分页查询（最新在前）。"""
        await self.ensure_schema()
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if action:
            clauses.append("action = :action")
            params["action"] = action
        if actor:
            clauses.append("actor ILIKE :actor")
            params["actor"] = f"%{actor}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        total_row = (
            await self._session.execute(
                text(f"SELECT COUNT(*) AS n FROM audit_log {where}"), params
            )
        ).fetchone()
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM audit_log {where} "
                    "ORDER BY ts DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": min(limit, MAX_PAGE_SIZE), "offset": offset},
            )
        ).fetchall()
        return [_row_to_entry(r) for r in rows], int(total_row.n) if total_row else 0
