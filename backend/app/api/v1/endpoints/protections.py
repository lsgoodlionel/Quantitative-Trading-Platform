"""
动态防护 / 熔断 API

- GET  /protections/config        当前配置（Redis，或默认）
- PUT  /protections/config        持久化 + 版本自增 + 热重载 manager
- GET  /protections/locks         活跃锁列表（Risk 页消费）
- DELETE /protections/locks/{id}  手动解除锁

存储遵循 broker_config 约定（Redis 哈希 + :version）。
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.redis import get_redis
from app.oms.protections import store
from app.oms.protections.config import ProtectionsConfig
from app.oms.protections.manager import get_protection_manager

router = APIRouter()


@router.get("/config", response_model=ProtectionsConfig)
async def get_protections_config(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> ProtectionsConfig:
    """获取当前防护配置。"""
    return await store.load_config(redis)


@router.put("/config", response_model=ProtectionsConfig)
async def update_protections_config(
    body: ProtectionsConfig,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> ProtectionsConfig:
    """持久化防护配置并热重载 manager（立即生效）。"""
    await store.save_config(redis, body)
    get_protection_manager().update_config(body)
    return body


@router.get("/locks")
async def list_active_locks() -> dict:
    """列出当前活跃锁。"""
    manager = get_protection_manager()
    locks = manager.active_locks()
    return {
        "locks": [lk.to_dict() for lk in locks],
        "count": len(locks),
    }


@router.delete("/locks/{lock_id}")
async def clear_lock(lock_id: str) -> dict:
    """手动解除指定锁。"""
    manager = get_protection_manager()
    if not manager.clear_lock(lock_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lock not found: {lock_id}",
        )
    return {"cleared": lock_id}
