"""富途网关配置 API（Epic E / E6 支撑）。

将富途连接参数存入 Redis（broker_config:futu 哈希），供 register_futu_gateway
在 OMS 初始化后读取。解锁密码仅写入、读取时脱敏，绝不回显明文。

- GET  /broker-config/futu   读取配置状态（脱敏）
- POST /broker-config/futu   保存配置（立即写入 Redis，重启 OMS 后生效）
"""

from __future__ import annotations

from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.redis import get_redis

router = APIRouter()

_KEY = "broker_config:futu"


class FutuSaveRequest(BaseModel):
    enabled: bool = Field(default=True, description="是否启用富途 HK 网关")
    host: str = Field(default="127.0.0.1", description="FutuOpenD 主机")
    port: int = Field(default=11111, ge=1, le=65535, description="FutuOpenD 端口")
    trade_env: Literal["SIMULATE", "REAL"] = Field(default="SIMULATE", description="SIMULATE / REAL")
    unlock_pwd: str = Field(default="", description="交易解锁密码（实盘必填）")


class FutuStatus(BaseModel):
    gateway: str = "futu"
    configured: bool
    enabled: bool = False
    host: str | None = None
    port: int | None = None
    trade_env: str | None = None
    has_unlock_pwd: bool = False


def _to_status(raw: dict[str, str]) -> FutuStatus:
    return FutuStatus(
        configured=True,
        enabled=str(raw.get("enabled", "false")).lower() == "true",
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 11111)),
        trade_env=(raw.get("trade_env", "SIMULATE")).upper(),
        has_unlock_pwd=bool(raw.get("unlock_pwd")),
    )


@router.get("/futu", response_model=FutuStatus)
async def get_futu_config(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> FutuStatus:
    """读取富途网关配置状态（不回显解锁密码明文）。"""
    raw = await redis.hgetall(_KEY)
    if not raw:
        return FutuStatus(configured=False)
    return _to_status(raw)


@router.post("/futu", response_model=FutuStatus)
async def save_futu_config(
    body: FutuSaveRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> FutuStatus:
    """保存富途网关配置到 Redis（下次 OMS 初始化时接入）。"""
    mapping = {
        "enabled": str(body.enabled).lower(),
        "host": body.host,
        "port": str(body.port),
        "trade_env": body.trade_env.upper(),
    }
    # 仅在提供了新密码时更新，避免空串覆盖已存密码
    if body.unlock_pwd:
        mapping["unlock_pwd"] = body.unlock_pwd
    await redis.hset(_KEY, mapping=mapping)

    raw = await redis.hgetall(_KEY)
    return _to_status(raw)
