"""
站内通知收件箱（V3 G5 通知中心）

统一事件总线的「站内」渠道：`dispatch_event` 派发的每一条事件都会先落到这里，
Telegram / Webhook 只是额外的外发渠道。因此新事件类型天然「默认只开站内」。

存储沿用实验记录器（`app.quant.experiments.recorder`）的 Redis 布局与容量上限约定：

    notify:inbox:record:{id}   → 通知 JSON（SET）
    notify:inbox:zset:time     → ZSET member=id score=created_at（时间线 / 容量裁剪）

写入路径来自同步的 `dispatch_event`（下单热路径不 await），读取路径来自 FastAPI
异步端点，故同时提供同步写与异步读两套 API。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace

logger = logging.getLogger(__name__)

_KEY_PREFIX = "notify:inbox"
_RECORD_KEY = f"{_KEY_PREFIX}:record"
_ZSET_TIME = f"{_KEY_PREFIX}:zset:time"

# 收件箱容量上限：超出后按时间裁剪最旧记录，防止 Redis 无界增长
MAX_NOTIFICATIONS = 200
# 单次列表查询上限
MAX_LIST_LIMIT = 200


@dataclass(frozen=True)
class Notification:
    """一条站内通知（不可变；标记已读返回新副本）。"""

    id: str
    type: str
    title: str
    symbol: str | None = None
    market: str | None = None
    payload: dict | None = None
    is_read: bool = False
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _record_key(notification_id: str) -> str:
    return f"{_RECORD_KEY}:{notification_id}"


def build_notification(event) -> Notification:
    """由 NotifyEvent 构造一条站内通知（不落库）。"""
    return Notification(
        id=uuid.uuid4().hex[:12],
        type=event.type.value,
        title=event.title,
        symbol=event.symbol,
        market=event.market,
        payload=dict(event.payload or {}),
        is_read=False,
        created_at=event.created_at.timestamp(),
    )


def _dump(notification: Notification) -> str:
    return json.dumps(notification.to_dict(), ensure_ascii=False, default=str)


def parse_notification(raw: str) -> Notification | None:
    """解析存储的 JSON；损坏记录返回 None 而非抛出（读取路径不应因脏数据而 500）。"""
    try:
        data = json.loads(raw)
        return Notification(
            id=data["id"],
            type=data["type"],
            title=data["title"],
            symbol=data.get("symbol"),
            market=data.get("market"),
            payload=data.get("payload") or {},
            is_read=bool(data.get("is_read", False)),
            created_at=float(data.get("created_at", 0.0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("收件箱记录损坏，已跳过")
        return None


# ── 同步写入（供 dispatch_event 调用） ────────────────────────

def record_notification_sync(client, notification: Notification) -> None:
    """同步写入一条通知并裁剪容量。异常由调用方（通知旁路）处理。"""
    pipe = client.pipeline()
    pipe.set(_record_key(notification.id), _dump(notification))
    # 时间线 score 必须与记录里的 created_at 一致，否则排序与展示会对不上
    pipe.zadd(_ZSET_TIME, {notification.id: notification.created_at})
    pipe.execute()
    _enforce_capacity_sync(client)


def _enforce_capacity_sync(client) -> None:
    total = int(client.zcard(_ZSET_TIME) or 0)
    overflow = total - MAX_NOTIFICATIONS
    if overflow <= 0:
        return
    stale_ids = client.zrange(_ZSET_TIME, 0, overflow - 1) or []
    if not stale_ids:
        return
    pipe = client.pipeline()
    for nid in stale_ids:
        pipe.delete(_record_key(nid))
        pipe.zrem(_ZSET_TIME, nid)
    pipe.execute()


# ── 异步读取 / 状态变更（供 API 端点调用） ────────────────────

async def list_notifications(redis, limit: int = 50, unread_only: bool = False) -> list[Notification]:
    """按时间倒序列出通知（最多 MAX_LIST_LIMIT 条）。"""
    capped = max(1, min(limit, MAX_LIST_LIMIT))
    # unread_only 会过滤掉部分结果，先多取候选再截断
    fetch = MAX_LIST_LIMIT if unread_only else capped
    ids = await redis.zrevrange(_ZSET_TIME, 0, fetch - 1)
    records = await _load_records(redis, list(ids or []))
    if unread_only:
        records = [n for n in records if not n.is_read]
    return records[:capped]


async def count_unread(redis) -> int:
    """未读数量（收件箱有容量上限，全量扫描代价可控）。"""
    ids = await redis.zrevrange(_ZSET_TIME, 0, MAX_NOTIFICATIONS - 1)
    records = await _load_records(redis, list(ids or []))
    return sum(1 for n in records if not n.is_read)


async def mark_read(redis, notification_id: str, is_read: bool = True) -> Notification | None:
    """切换单条通知的已读状态；不存在返回 None。"""
    raw = await redis.get(_record_key(notification_id))
    if not raw:
        return None
    current = parse_notification(raw)
    if current is None:
        return None
    updated = replace(current, is_read=is_read)
    await redis.set(_record_key(notification_id), _dump(updated))
    return updated


async def mark_all_read(redis) -> int:
    """把全部未读标记为已读，返回本次变更条数。"""
    ids = await redis.zrevrange(_ZSET_TIME, 0, MAX_NOTIFICATIONS - 1)
    records = await _load_records(redis, list(ids or []))
    unread = [n for n in records if not n.is_read]
    if not unread:
        return 0
    pipe = redis.pipeline()
    for n in unread:
        pipe.set(_record_key(n.id), _dump(replace(n, is_read=True)))
    await pipe.execute()
    return len(unread)


async def delete_notification(redis, notification_id: str) -> bool:
    """删除一条通知，返回它此前是否存在。"""
    existed = await redis.exists(_record_key(notification_id))
    pipe = redis.pipeline()
    pipe.delete(_record_key(notification_id))
    pipe.zrem(_ZSET_TIME, notification_id)
    await pipe.execute()
    return bool(existed)


async def clear_all(redis) -> int:
    """清空收件箱，返回删除条数。"""
    ids = list(await redis.zrevrange(_ZSET_TIME, 0, MAX_NOTIFICATIONS - 1) or [])
    if not ids:
        return 0
    pipe = redis.pipeline()
    for nid in ids:
        pipe.delete(_record_key(nid))
        pipe.zrem(_ZSET_TIME, nid)
    await pipe.execute()
    return len(ids)


async def _load_records(redis, ids: list[str]) -> list[Notification]:
    if not ids:
        return []
    raws = await redis.mget([_record_key(nid) for nid in ids])
    parsed = [parse_notification(raw) for raw in raws if raw]
    return [n for n in parsed if n is not None]
