"""
实验记录器 + 因子排行榜（B7）

移植 qlib `workflow/record_temp.py`（SignalRecord/SigAnaRecord）的**记录结构**：把每次
因子分析 / 遗传挖掘 / 因子库扫描的参数与评价指标（IC / RankIC / ICIR / 适应度）留痕，
形成可复现、可横向对比的实验档案与排行榜。

与 qlib 的差异：
  - 存储用 Redis（仿 broker_config 的持久化）而非本地 mlflow/pickle，契合本平台基础设施。
  - 只记录标量指标与元数据（轻量），不落大对象（信号时序仍由前端按需重算）。

Redis 布局：
  experiments:record:{id}     → 记录 JSON（SET）
  experiments:zset:score      → ZSET member=id score=leaderboard_score（排行榜）
  experiments:zset:time       → ZSET member=id score=created_at（时间线 / 容量裁剪）
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

import redis.asyncio as aioredis

_KEY_PREFIX = "experiments"
_RECORD_KEY = f"{_KEY_PREFIX}:record"
_ZSET_SCORE = f"{_KEY_PREFIX}:zset:score"
_ZSET_TIME = f"{_KEY_PREFIX}:zset:time"

# 排行榜容量上限：超出后按时间裁剪最旧记录，防止 Redis 无界增长
MAX_RECORDS = 500

ExperimentKind = Literal["factor_analysis", "formula_factor", "genetic_mining", "factor_library"]


@dataclass(frozen=True)
class ExperimentMetrics:
    """标量评价指标（缺失以 None 表示）。"""

    ic_mean: float | None = None
    rank_ic_mean: float | None = None
    icir: float | None = None
    fitness: float | None = None
    mean_net_return: float | None = None


@dataclass(frozen=True)
class ExperimentRecord:
    """一次实验的完整档案（不可变）。"""

    id: str
    kind: str
    name: str
    market: str
    symbols: list[str]
    tokens: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    metrics: ExperimentMetrics = field(default_factory=ExperimentMetrics)
    note: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


def _record_key(record_id: str) -> str:
    return f"{_RECORD_KEY}:{record_id}"


def _leaderboard_score(metrics: ExperimentMetrics) -> float:
    """排行榜排序键：优先适应度，其次 |RankIC|，再 |IC|，用于跨类型可比排序。"""
    if metrics.fitness is not None:
        return float(metrics.fitness)
    if metrics.rank_ic_mean is not None:
        return abs(float(metrics.rank_ic_mean))
    if metrics.ic_mean is not None:
        return abs(float(metrics.ic_mean))
    return 0.0


def build_record(
    kind: str,
    name: str,
    market: str,
    symbols: list[str],
    metrics: ExperimentMetrics,
    tokens: list[str] | None = None,
    params: dict | None = None,
    note: str = "",
) -> ExperimentRecord:
    """构造一条带 id / 时间戳的实验记录（不落库）。"""
    return ExperimentRecord(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        name=name,
        market=market,
        symbols=[s.upper() for s in symbols],
        tokens=list(tokens or []),
        params=dict(params or {}),
        metrics=metrics,
        note=note,
        created_at=time.time(),
    )


async def save_experiment(redis: aioredis.Redis, record: ExperimentRecord) -> ExperimentRecord:
    """持久化实验记录并登记到排行榜 / 时间线两个 ZSET。"""
    payload = json.dumps(record.to_dict(), ensure_ascii=False)
    score = _leaderboard_score(record.metrics)

    pipe = redis.pipeline()
    pipe.set(_record_key(record.id), payload)
    pipe.zadd(_ZSET_SCORE, {record.id: score})
    pipe.zadd(_ZSET_TIME, {record.id: record.created_at})
    await pipe.execute()

    await _enforce_capacity(redis)
    return record


async def _enforce_capacity(redis: aioredis.Redis) -> None:
    """超过 MAX_RECORDS 时按时间删除最旧记录（含两个 ZSET 与主键）。"""
    total = await redis.zcard(_ZSET_TIME)
    overflow = int(total) - MAX_RECORDS
    if overflow <= 0:
        return
    stale_ids = await redis.zrange(_ZSET_TIME, 0, overflow - 1)
    if not stale_ids:
        return
    pipe = redis.pipeline()
    for rid in stale_ids:
        pipe.delete(_record_key(rid))
        pipe.zrem(_ZSET_SCORE, rid)
        pipe.zrem(_ZSET_TIME, rid)
    await pipe.execute()


async def list_experiments(
    redis: aioredis.Redis,
    sort_by: Literal["score", "time"] = "score",
    kind: str | None = None,
    limit: int = 50,
) -> list[ExperimentRecord]:
    """列出实验记录（排行榜或时间线），可按 kind 过滤。"""
    zset = _ZSET_SCORE if sort_by == "score" else _ZSET_TIME
    # 过滤会缩减结果，故多取一些候选再截断
    fetch = max(limit * 3, limit) if kind else limit
    ids = await redis.zrevrange(zset, 0, fetch - 1)
    records = await _load_records(redis, ids)
    if kind:
        records = [r for r in records if r.kind == kind]
    return records[:limit]


async def get_experiment(redis: aioredis.Redis, record_id: str) -> ExperimentRecord | None:
    raw = await redis.get(_record_key(record_id))
    return _parse_record(raw) if raw else None


async def delete_experiment(redis: aioredis.Redis, record_id: str) -> bool:
    """删除一条记录及其排行榜/时间线登记，返回是否存在过。"""
    existed = await redis.exists(_record_key(record_id))
    pipe = redis.pipeline()
    pipe.delete(_record_key(record_id))
    pipe.zrem(_ZSET_SCORE, record_id)
    pipe.zrem(_ZSET_TIME, record_id)
    await pipe.execute()
    return bool(existed)


async def _load_records(redis: aioredis.Redis, ids: list[str]) -> list[ExperimentRecord]:
    if not ids:
        return []
    raws = await redis.mget([_record_key(rid) for rid in ids])
    records = [_parse_record(raw) for raw in raws if raw]
    return [r for r in records if r is not None]


def _parse_record(raw: str) -> ExperimentRecord | None:
    try:
        data = json.loads(raw)
        metrics = ExperimentMetrics(**data.get("metrics", {}))
        return ExperimentRecord(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            market=data["market"],
            symbols=data.get("symbols", []),
            tokens=data.get("tokens", []),
            params=data.get("params", {}),
            metrics=metrics,
            note=data.get("note", ""),
            created_at=float(data.get("created_at", 0.0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
