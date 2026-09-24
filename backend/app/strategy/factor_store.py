"""因子策略的命名存储（V3 Wave A-a / G1）

**为什么必须独立存完整 spec，而不是只存实验 id**：
实验记录器有 `MAX_RECORDS = 500` 的滚动淘汰（`app/quant/experiments/recorder.py`）。
一条被提升为策略的实验记录随时可能被后来的实验挤掉；如果策略只存了实验 id，
淘汰发生的那一刻策略就成了孤儿，既跑不了回测也说不清自己是什么。
所以这里存的是完整 `FactorStrategySpec`，`source_experiment_id` 仅作溯源线索，
**加载路径不依赖它**。

Redis 布局：
  strategies:factor:{name}   → 记录 JSON（SET）
  strategies:factor:index    → 名称集合（SET）
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.strategy.factor_strategy import FactorStrategySpec

logger = logging.getLogger(__name__)

_RECORD_KEY = "strategies:factor"
_INDEX_KEY = "strategies:factor:index"

#: 策略名长度上限
MAX_NAME_LENGTH = 120
#: 允许的策略名字符：中英文、数字、空格与 `_-.`。
#: 冒号被排除是有意的 —— 名称要拼进 Redis key，放行冒号等于允许键空间注入。
_NAME_PATTERN = re.compile(r"^[\w一-鿿 .\-]+$")


@dataclass(frozen=True)
class FactorStrategyRecord:
    """一条命名因子策略。"""

    name: str
    spec: FactorStrategySpec
    note: str = ""
    #: 来源实验（仅溯源用；记录被淘汰后本条策略依然完整可用）
    source_experiment_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "spec": self.spec.to_dict(),
            "note": self.note,
            "source_experiment_id": self.source_experiment_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def normalize_name(name: str) -> str:
    """校验并归一化策略名（名称会成为 Redis key 的一部分，属系统边界）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("策略名不能为空")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"策略名最长 {MAX_NAME_LENGTH} 字符，收到 {len(cleaned)}")
    if not _NAME_PATTERN.match(cleaned):
        raise ValueError(f"策略名含非法字符: {cleaned}（只允许中英文、数字、空格与 _-.）")
    return cleaned


def _record_key(name: str) -> str:
    return f"{_RECORD_KEY}:{name}"


async def save_factor_strategy(
    redis: aioredis.Redis,
    name: str,
    spec: FactorStrategySpec,
    note: str = "",
    source_experiment_id: str | None = None,
) -> FactorStrategyRecord:
    """新建或覆盖一条命名因子策略（同名视为更新，保留原始创建时间）。"""
    key_name = normalize_name(name)
    now = time.time()
    existing = await get_factor_strategy(redis, key_name)

    record = FactorStrategyRecord(
        name=key_name,
        spec=spec,
        note=note,
        source_experiment_id=source_experiment_id,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    pipe = redis.pipeline()
    pipe.set(_record_key(key_name), json.dumps(record.to_dict(), ensure_ascii=False))
    pipe.sadd(_INDEX_KEY, key_name)
    await pipe.execute()
    return record


async def get_factor_strategy(
    redis: aioredis.Redis, name: str
) -> FactorStrategyRecord | None:
    """按名字加载；不存在或数据损坏时返回 None。"""
    raw = await redis.get(_record_key(normalize_name(name)))
    return _parse_record(raw) if raw else None


async def list_factor_strategies(redis: aioredis.Redis) -> list[FactorStrategyRecord]:
    """列出全部命名因子策略，最新创建的在前。"""
    names = await redis.smembers(_INDEX_KEY)
    if not names:
        return []
    raws = await redis.mget([_record_key(_decoded(n)) for n in sorted(names)])
    records = [_parse_record(raw) for raw in raws if raw]
    return sorted(
        (r for r in records if r is not None),
        key=lambda r: r.created_at,
        reverse=True,
    )


async def delete_factor_strategy(redis: aioredis.Redis, name: str) -> bool:
    """删除一条命名策略，返回它是否存在过。"""
    key_name = normalize_name(name)
    existed = await redis.exists(_record_key(key_name))
    pipe = redis.pipeline()
    pipe.delete(_record_key(key_name))
    pipe.srem(_INDEX_KEY, key_name)
    await pipe.execute()
    return bool(existed)


def _decoded(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _parse_record(raw) -> FactorStrategyRecord | None:
    """
    反序列化一条记录。

    损坏的记录只跳过、不抛错：一条坏数据不应该让整个策略列表 500，
    但必须留下日志，否则就是静默吞错。
    """
    try:
        data = json.loads(_decoded(raw))
        return FactorStrategyRecord(
            name=data["name"],
            spec=FactorStrategySpec.from_dict(data["spec"]),
            note=data.get("note", ""),
            source_experiment_id=data.get("source_experiment_id"),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("因子策略记录损坏，已跳过: %s", exc)
        return None


__all__ = [
    "MAX_NAME_LENGTH",
    "FactorStrategyRecord",
    "delete_factor_strategy",
    "get_factor_strategy",
    "list_factor_strategies",
    "normalize_name",
    "save_factor_strategy",
]
