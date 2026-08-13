"""
回测历史持久化仓储（V3 · H5）

**存储介质：TimescaleDB / PostgreSQL**（与行情同库，见 `timescale.py`）。
刻意不用实验记录器那套 Redis + `MAX_RECORDS=500` 滚动淘汰 —— 本特性的目标正是
「消除刷新即失」，一个会悄悄丢历史的介质等于没做。这里没有容量上限，
清理走 H5 的手动删除接口。

**净值曲线体积策略：降采样后整条落库**（不是「只存指标 + rerun 重算」）。
理由：历史列表的核心价值就是「打开就能看到当时那条曲线」，依赖 rerun 重算意味着
数据源变动或复权调整后曲线会漂移，历史记录就不再是历史。为控制体积，写入前统一
降采样到最多 `MAX_CURVE_POINTS` 个点（首尾必留）：3 年日线 750 点原样保留，
分钟级几万点压到 1000 点以内，单条记录的 JSONB 稳定在百 KB 量级。
完整配置（策略/参数/区间/频率/初始资金）同时落库，因此 rerun 仍可精确复现。

建表：随 `infra/init-db/03_backtest_history.sql` 初始化；对已存在的部署，
仓储首次使用时会执行一次幂等的 `ensure_schema()`（项目未引入 alembic 迁移流程）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# 单条记录落库的净值曲线最大点数（首尾必留）
MAX_CURVE_POINTS = 1000
# 列表接口单页上限
MAX_PAGE_SIZE = 100
# 对比接口最多叠加的记录数
MAX_COMPARE_ITEMS = 8


@dataclass(frozen=True)
class BacktestHistoryRecord:
    """一条回测历史（不可变）。"""

    id: str
    name: str
    strategy_name: str
    symbol: str
    market: str
    frequency: str
    start_date: str
    end_date: str
    initial_cash: float
    final_value: float
    params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    equity_curve: list[dict] = field(default_factory=list)
    curve_points: int = 0
    curve_downsampled: bool = False
    note: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """列表视图用的轻量投影（不含净值曲线）。"""
        data = self.to_dict()
        data.pop("equity_curve", None)
        return data


def downsample_curve(curve: list[dict], max_points: int = MAX_CURVE_POINTS) -> tuple[list[dict], bool]:
    """
    等步长降采样净值曲线，首尾点必定保留。

    Returns:
        (降采样后的曲线, 是否发生了降采样)
    """
    if max_points < 2:
        raise ValueError("max_points 至少为 2")
    if len(curve) <= max_points:
        return list(curve), False
    stride = len(curve) / (max_points - 1)
    picked = [curve[min(int(i * stride), len(curve) - 1)] for i in range(max_points - 1)]
    picked.append(curve[-1])
    return picked, True


def build_record(
    strategy_name: str,
    symbol: str,
    market: str,
    frequency: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    final_value: float,
    params: dict,
    metrics: dict,
    equity_curve: list[dict],
    name: str = "",
    note: str = "",
) -> BacktestHistoryRecord:
    """构造一条带 id / 时间戳的历史记录（曲线已降采样，尚未落库）。"""
    curve, downsampled = downsample_curve(equity_curve)
    normalized_symbol = symbol.upper()
    return BacktestHistoryRecord(
        id=str(uuid.uuid4()),
        name=name or f"{strategy_name} · {normalized_symbol} · {start_date}~{end_date}",
        strategy_name=strategy_name,
        symbol=normalized_symbol,
        market=market.upper(),
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        initial_cash=float(initial_cash),
        final_value=float(final_value),
        params=dict(params),
        metrics=dict(metrics),
        equity_curve=curve,
        curve_points=len(curve),
        curve_downsampled=downsampled,
        note=note,
        created_at=datetime.now(UTC).isoformat(),
    )


@dataclass(frozen=True)
class HistoryFilter:
    """列表筛选条件（全部可选）。"""

    strategy_name: str | None = None
    symbol: str | None = None
    market: str | None = None
    start_after: date | None = None
    end_before: date | None = None


class BacktestHistoryStore(Protocol):
    """回测历史仓储接口（便于测试替换为内存实现）。"""

    async def save(self, record: BacktestHistoryRecord) -> BacktestHistoryRecord: ...

    async def list(
        self, filters: HistoryFilter, limit: int, offset: int
    ) -> tuple[list[BacktestHistoryRecord], int]: ...

    async def get(self, record_id: str) -> BacktestHistoryRecord | None: ...

    async def get_many(self, record_ids: list[str]) -> list[BacktestHistoryRecord]: ...

    async def delete(self, record_id: str) -> bool: ...


# ── SQL ───────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS backtest_history (
    id                UUID           PRIMARY KEY,
    name              VARCHAR(300)   NOT NULL DEFAULT '',
    strategy_name     VARCHAR(100)   NOT NULL,
    symbol            VARCHAR(30)    NOT NULL,
    market            VARCHAR(10)    NOT NULL,
    frequency         VARCHAR(10)    NOT NULL,
    start_date        DATE           NOT NULL,
    end_date          DATE           NOT NULL,
    initial_cash      NUMERIC(18, 4) NOT NULL,
    final_value       NUMERIC(18, 4) NOT NULL,
    params            JSONB          NOT NULL DEFAULT '{}',
    metrics           JSONB          NOT NULL DEFAULT '{}',
    equity_curve      JSONB          NOT NULL DEFAULT '[]',
    curve_points      INTEGER        NOT NULL DEFAULT 0,
    curve_downsampled BOOLEAN        NOT NULL DEFAULT FALSE,
    note              TEXT           NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bt_history_created ON backtest_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bt_history_lookup  ON backtest_history(strategy_name, symbol, created_at DESC);
"""

_INSERT_SQL = text("""
    INSERT INTO backtest_history (
        id, name, strategy_name, symbol, market, frequency,
        start_date, end_date, initial_cash, final_value,
        params, metrics, equity_curve, curve_points, curve_downsampled, note, created_at
    ) VALUES (
        :id, :name, :strategy_name, :symbol, :market, :frequency,
        :start_date, :end_date, :initial_cash, :final_value,
        CAST(:params AS JSONB), CAST(:metrics AS JSONB), CAST(:equity_curve AS JSONB),
        :curve_points, :curve_downsampled, :note, :created_at
    )
""")

_SELECT_COLUMNS = """
    id, name, strategy_name, symbol, market, frequency,
    start_date, end_date, initial_cash, final_value,
    params, metrics, equity_curve, curve_points, curve_downsampled, note, created_at
"""

_DELETE_SQL = text("DELETE FROM backtest_history WHERE id = CAST(:id AS UUID)")


def _row_to_record(row: Any, with_curve: bool = True) -> BacktestHistoryRecord:
    created = row.created_at
    return BacktestHistoryRecord(
        id=str(row.id),
        name=row.name,
        strategy_name=row.strategy_name,
        symbol=row.symbol,
        market=row.market,
        frequency=row.frequency,
        start_date=row.start_date.isoformat(),
        end_date=row.end_date.isoformat(),
        initial_cash=float(row.initial_cash),
        final_value=float(row.final_value),
        params=_as_json(row.params, {}),
        metrics=_as_json(row.metrics, {}),
        equity_curve=_as_json(row.equity_curve, []) if with_curve else [],
        curve_points=int(row.curve_points),
        curve_downsampled=bool(row.curve_downsampled),
        note=row.note or "",
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
    )


def _as_json(value: Any, fallback: Any) -> Any:
    """asyncpg 返回 dict/list，其他驱动可能返回字符串 —— 两种都要能解。"""
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON column in backtest_history")
            return fallback
    return value


class PostgresBacktestHistoryStore:
    """基于 TimescaleDB / PostgreSQL 的回测历史仓储。"""

    _schema_ready = False

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_schema(self) -> None:
        """幂等建表（项目无 alembic 迁移流程，首次使用时补齐）。"""
        if PostgresBacktestHistoryStore._schema_ready:
            return
        for statement in filter(None, (s.strip() for s in _DDL.split(";"))):
            await self._session.execute(text(statement))
        PostgresBacktestHistoryStore._schema_ready = True

    async def save(self, record: BacktestHistoryRecord) -> BacktestHistoryRecord:
        await self.ensure_schema()
        await self._session.execute(_INSERT_SQL, _insert_params(record))
        await self._session.commit()
        logger.info("Saved backtest history", record_id=record.id, symbol=record.symbol)
        return record

    async def list(
        self, filters: HistoryFilter, limit: int, offset: int,
    ) -> tuple[list[BacktestHistoryRecord], int]:
        await self.ensure_schema()
        where, params = _build_where(filters)
        total_row = (
            await self._session.execute(
                text(f"SELECT COUNT(*) AS n FROM backtest_history {where}"), params
            )
        ).fetchone()
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM backtest_history {where} "
                    "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).fetchall()
        records = [_row_to_record(r, with_curve=False) for r in rows]
        return records, int(total_row.n) if total_row else 0

    async def get(self, record_id: str) -> BacktestHistoryRecord | None:
        await self.ensure_schema()
        row = (
            await self._session.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM backtest_history WHERE id = CAST(:id AS UUID)"),
                {"id": record_id},
            )
        ).fetchone()
        return _row_to_record(row) if row else None

    async def get_many(self, record_ids: list[str]) -> list[BacktestHistoryRecord]:
        await self.ensure_schema()
        if not record_ids:
            return []
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM backtest_history "
                    "WHERE id = ANY(CAST(:ids AS UUID[]))"
                ),
                {"ids": record_ids},
            )
        ).fetchall()
        by_id = {str(r.id): _row_to_record(r) for r in rows}
        return [by_id[rid] for rid in record_ids if rid in by_id]

    async def delete(self, record_id: str) -> bool:
        await self.ensure_schema()
        result = await self._session.execute(_DELETE_SQL, {"id": record_id})
        await self._session.commit()
        return bool(result.rowcount)


def _insert_params(record: BacktestHistoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "strategy_name": record.strategy_name,
        "symbol": record.symbol,
        "market": record.market,
        "frequency": record.frequency,
        "start_date": date.fromisoformat(record.start_date),
        "end_date": date.fromisoformat(record.end_date),
        "initial_cash": record.initial_cash,
        "final_value": record.final_value,
        "params": json.dumps(record.params, ensure_ascii=False),
        "metrics": json.dumps(record.metrics, ensure_ascii=False),
        "equity_curve": json.dumps(record.equity_curve, ensure_ascii=False, default=str),
        "curve_points": record.curve_points,
        "curve_downsampled": record.curve_downsampled,
        "note": record.note,
        "created_at": datetime.fromisoformat(record.created_at),
    }


def _build_where(filters: HistoryFilter) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.strategy_name:
        clauses.append("strategy_name = :strategy_name")
        params["strategy_name"] = filters.strategy_name
    if filters.symbol:
        clauses.append("symbol = :symbol")
        params["symbol"] = filters.symbol.upper()
    if filters.market:
        clauses.append("market = :market")
        params["market"] = filters.market.upper()
    if filters.start_after:
        clauses.append("start_date >= :start_after")
        params["start_after"] = filters.start_after
    if filters.end_before:
        clauses.append("end_date <= :end_before")
        params["end_before"] = filters.end_before
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params
