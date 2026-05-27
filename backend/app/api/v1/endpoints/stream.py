"""
实时流 WebSocket 端点

支持三类推送:
  /stream/bars      — 实时 K 线（来自数据源订阅）
  /stream/orders    — 订单状态变更（来自 Redis Stream orders:events）
  /stream/portfolio — 持仓实时盈亏（每 5 秒主动推送最新持仓）

设计原则:
  - 每个 WebSocket 连接独立一个 asyncio.Task
  - 订单流: 消费 Redis Stream，增量推送新事件
  - 持仓流: 定时查询 OMS，推送增量快照
  - Ping/Pong: 每 30 秒发送 ping，防止连接被中间件断开
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.data.models import Frequency, Market
from app.data.service import DataService

router = APIRouter()
logger = get_logger(__name__)

# 持仓推送间隔（秒）
_PORTFOLIO_INTERVAL = 5.0
# 订单流消费超时（毫秒）
_REDIS_BLOCK_MS = 2000


# ── 连接管理器 ────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        self._connections.setdefault(channel, []).append(websocket)
        logger.info("WS connected", channel=channel)

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        conns = self._connections.get(channel, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.info("WS disconnected", channel=channel)

    async def broadcast(self, channel: str, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections.get(channel, []):
            try:
                await ws.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, channel)


manager = ConnectionManager()


# ── /stream/bars — 实时 K 线 ─────────────────────────────────

@router.websocket("/bars")
async def stream_bars(
    websocket: WebSocket,
    symbols: Annotated[str, Query(description="逗号分隔的股票代码, 如 AAPL,MSFT")] = "",
    market: Annotated[Market, Query()] = Market.US,
    frequency: Annotated[Frequency, Query()] = Frequency.MIN_1,
    session: AsyncSession = Depends(get_db),
) -> None:
    """
    实时 K 线 WebSocket 推送。

    连接后自动订阅指定标的的实时 K 线，每根新 K 线推送一次。
    消息格式: {"type": "bar", "data": {time, open, high, low, close, volume, symbol}}
    """
    channel = f"bars:{market.value}"
    await manager.connect(websocket, channel)

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "No symbols specified",
        }))
        manager.disconnect(websocket, channel)
        return

    svc = DataService(session)

    async def _stream() -> None:
        try:
            async for bar in svc.subscribe_bars(symbol_list, market, frequency):
                msg = {
                    "type": "bar",
                    "data": {
                        "symbol": bar.symbol,
                        "time":   bar.time.isoformat(),
                        "open":   bar.open,
                        "high":   bar.high,
                        "low":    bar.low,
                        "close":  bar.close,
                        "volume": bar.volume,
                        "vwap":   bar.vwap,
                    },
                }
                await websocket.send_text(json.dumps(msg))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Bars stream error", error=str(e))

    stream_task = asyncio.create_task(_stream())

    async def _ping() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    ping_task = asyncio.create_task(_ping())

    try:
        await asyncio.gather(stream_task, ping_task)
    except WebSocketDisconnect:
        pass
    finally:
        stream_task.cancel()
        ping_task.cancel()
        manager.disconnect(websocket, channel)


# ── /stream/orders — 实时订单推送 ────────────────────────────

@router.websocket("/orders")
async def stream_orders(websocket: WebSocket) -> None:
    """
    实时订单状态推送。

    从 Redis Stream "orders:events" 读取增量事件，推送给前端。
    消息格式: {"type": "order_update", "data": {...LiveOrder.to_dict()}}
    """
    await manager.connect(websocket, "orders")

    async def _consume_redis() -> None:
        """从 Redis Stream 增量消费订单事件。"""
        try:
            import redis.asyncio as aioredis
            from app.core.config import settings
            r = aioredis.from_url(settings.redis_url)

            last_id = "$"  # 从最新开始消费
            while True:
                try:
                    messages = await r.xread(
                        {"orders:events": last_id},
                        block=_REDIS_BLOCK_MS,
                        count=10,
                    )
                    for _stream_name, entries in (messages or []):
                        for entry_id, fields in entries:
                            last_id = entry_id
                            raw = fields.get(b"data") or fields.get("data", b"{}")
                            data = json.loads(raw)
                            await websocket.send_text(json.dumps({
                                "type": "order_update",
                                "data": data,
                            }))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug("Redis order stream read error: %s", e)
                    await asyncio.sleep(2)
            await r.aclose()
        except Exception as e:
            logger.warning("Cannot connect to Redis for order stream: %s", e)
            # 降级：发送提示后保持连接（依靠 OMS 轮询推送）
            await websocket.send_text(json.dumps({
                "type": "info",
                "message": "Redis stream unavailable, order updates may be delayed",
            }))

    async def _fallback_poll() -> None:
        """Redis 不可用时，定期主动拉取活跃订单推送。"""
        while True:
            await asyncio.sleep(5)
            try:
                from app.oms.manager import get_order_manager
                oms = get_order_manager()
                active = oms.list_orders(status=None, limit=50)
                for order in active:
                    await websocket.send_text(json.dumps({
                        "type": "order_snapshot",
                        "data": order.to_dict(),
                    }))
            except Exception:
                pass

    redis_task = asyncio.create_task(_consume_redis())
    poll_task = asyncio.create_task(_fallback_poll())

    async def _ping() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    ping_task = asyncio.create_task(_ping())

    try:
        await websocket.receive_text()  # 等待客户端断开
    except WebSocketDisconnect:
        pass
    finally:
        redis_task.cancel()
        poll_task.cancel()
        ping_task.cancel()
        manager.disconnect(websocket, "orders")


# ── /stream/portfolio — 实时持仓推送 ─────────────────────────

@router.websocket("/portfolio")
async def stream_portfolio(
    websocket: WebSocket,
    market: Annotated[Market, Query()] = Market.US,
) -> None:
    """
    实时持仓盈亏推送。

    每 5 秒查询一次 OMS 持仓和账户，推送最新快照。
    消息格式:
      {"type": "portfolio_snapshot", "data": {"account": {...}, "positions": [...]}}
    """
    await manager.connect(websocket, f"portfolio:{market.value}")

    async def _push_loop() -> None:
        while True:
            try:
                from app.oms.manager import get_order_manager
                oms = get_order_manager()
                account = await oms.get_account(market.value)
                positions = await oms.get_positions(market.value)
                await websocket.send_text(json.dumps({
                    "type": "portfolio_snapshot",
                    "data": {
                        "market":    market.value,
                        "account":   account,
                        "positions": positions,
                    },
                }, default=str))
            except Exception as e:
                logger.debug("Portfolio push error: %s", e)

            await asyncio.sleep(_PORTFOLIO_INTERVAL)

    async def _ping() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    push_task = asyncio.create_task(_push_loop())
    ping_task = asyncio.create_task(_ping())

    try:
        await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        ping_task.cancel()
        manager.disconnect(websocket, f"portfolio:{market.value}")


# ── /stream/risk — 实时风控告警推送 ──────────────────────────

@router.websocket("/risk")
async def stream_risk(websocket: WebSocket) -> None:
    """
    实时风控状态推送。

    每 10 秒推送一次风控引擎的当日汇总。
    消息格式: {"type": "risk_summary", "data": {...}}
    """
    await manager.connect(websocket, "risk")

    async def _push_loop() -> None:
        while True:
            try:
                from app.risk.engine import get_risk_engine
                engine = get_risk_engine()
                summary = engine.daily_summary()
                await websocket.send_text(json.dumps({
                    "type": "risk_summary",
                    "data": summary,
                }))
            except Exception as e:
                logger.debug("Risk push error: %s", e)
            await asyncio.sleep(10)

    async def _ping() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    push_task = asyncio.create_task(_push_loop())
    ping_task = asyncio.create_task(_ping())

    try:
        await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        ping_task.cancel()
        manager.disconnect(websocket, "risk")
