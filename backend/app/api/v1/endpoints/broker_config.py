"""
券商配置 API — 通过前端页面配置 API 密钥

密钥存储于 Redis（broker_config:{gateway} 哈希），不落库。
读取时脱敏返回，绝不暴露完整 Secret。
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.redis import get_redis

router = APIRouter()

# Redis 键前缀
_KEY_PREFIX = "broker_config"

# 已知网关清单
GATEWAYS = ["alpaca"]


# ── Schemas ──────────────────────────────────────────────────────────────────

class AlpacaSaveRequest(BaseModel):
    api_key: str = Field(min_length=1, description="Alpaca API Key (PK...)")
    api_secret: str = Field(min_length=1, description="Alpaca API Secret")
    base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="API base URL（paper/live）",
    )
    paper_mode: bool = Field(default=True, description="是否为模拟盘")


class BrokerStatus(BaseModel):
    gateway: str
    configured: bool
    key_hint: str | None = None   # 脱敏后的 key，如 "PK••••••••1234"
    base_url: str | None = None
    paper_mode: bool = True


class AllBrokerConfigResponse(BaseModel):
    alpaca: BrokerStatus


class TestConnectionResponse(BaseModel):
    ok: bool
    account_id: str | None = None
    buying_power: float | None = None
    error: str | None = None


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _mask(value: str) -> str:
    """显示首2位 + 中间掩码 + 末4位，如 PK••••••••••••5678。"""
    if len(value) <= 6:
        return "••••••"
    return value[:2] + "•" * (len(value) - 6) + value[-4:]


def _redis_key(gateway: str) -> str:
    return f"{_KEY_PREFIX}:{gateway}"


async def _get_alpaca_raw(redis: aioredis.Redis) -> dict[str, str] | None:
    """从 Redis 读取 Alpaca 原始配置（含完整 secret）。"""
    data = await redis.hgetall(_redis_key("alpaca"))
    return data if data else None


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=AllBrokerConfigResponse)
async def get_all_broker_config(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> AllBrokerConfigResponse:
    """获取所有券商通道的配置状态（脱敏，不含完整密钥）。"""
    raw = await _get_alpaca_raw(redis)

    if raw:
        alpaca = BrokerStatus(
            gateway="alpaca",
            configured=True,
            key_hint=_mask(raw.get("api_key", "")),
            base_url=raw.get("base_url", "https://paper-api.alpaca.markets"),
            paper_mode=raw.get("paper_mode", "true").lower() == "true",
        )
    else:
        alpaca = BrokerStatus(gateway="alpaca", configured=False)

    return AllBrokerConfigResponse(alpaca=alpaca)


@router.post("/alpaca", response_model=BrokerStatus, status_code=status.HTTP_200_OK)
async def save_alpaca_config(
    body: AlpacaSaveRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> BrokerStatus:
    """保存 Alpaca API 密钥（存入 Redis，立即生效）。"""
    key = _redis_key("alpaca")
    await redis.hset(key, mapping={
        "api_key": body.api_key,
        "api_secret": body.api_secret,
        "base_url": body.base_url,
        "paper_mode": str(body.paper_mode).lower(),
    })
    # 写入配置版本号，触发 AlpacaDataFeed 自动重建客户端
    await redis.incr(f"{key}:version")

    return BrokerStatus(
        gateway="alpaca",
        configured=True,
        key_hint=_mask(body.api_key),
        base_url=body.base_url,
        paper_mode=body.paper_mode,
    )


@router.delete("/alpaca", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alpaca_config(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> None:
    """清除 Alpaca 配置（回退到环境变量或演示数据）。"""
    await redis.delete(_redis_key("alpaca"))
    await redis.delete(f"{_redis_key('alpaca')}:version")


@router.post("/alpaca/test", response_model=TestConnectionResponse)
async def test_alpaca_connection(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TestConnectionResponse:
    """测试当前 Alpaca 配置是否可连通（需先保存）。"""
    raw = await _get_alpaca_raw(redis)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未配置 Alpaca API 密钥，请先保存。",
        )

    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return TestConnectionResponse(
            ok=False,
            error="alpaca-py 未安装，无法测试连接。",
        )

    paper = raw.get("paper_mode", "true").lower() == "true"
    try:
        import asyncio
        import json as _json

        client = TradingClient(
            api_key=raw["api_key"],
            secret_key=raw["api_secret"],
            paper=paper,
        )
        loop = asyncio.get_event_loop()
        account = await loop.run_in_executor(None, client.get_account)
        return TestConnectionResponse(
            ok=True,
            account_id=str(account.id),
            buying_power=float(account.buying_power),
        )
    except Exception as e:
        err_str = str(e)
        # Try to extract a human-readable message from Alpaca API error JSON
        try:
            parsed = _json.loads(err_str)
            if isinstance(parsed, dict) and "message" in parsed:
                err_str = parsed["message"]
        except Exception:
            pass
        return TestConnectionResponse(ok=False, error=err_str)
