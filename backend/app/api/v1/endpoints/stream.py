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
        await websocket.send_text(json.dumps({"type": "error", "message": "No symbols specified"}))
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
                        "time": bar.time.isoformat(),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "vwap": bar.vwap,
                    },
                }
                await websocket.send_text(json.dumps(msg))
        except Exception as e:
            logger.error("Stream error", error=str(e))

    stream_task = asyncio.create_task(_stream())

    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        stream_task.cancel()
        manager.disconnect(websocket, channel)


@router.websocket("/orders")
async def stream_orders(websocket: WebSocket) -> None:
    """实时订单状态推送 — Phase 3 接入 OMS。"""
    await manager.connect(websocket, "orders")
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket, "orders")


@router.websocket("/portfolio")
async def stream_portfolio(websocket: WebSocket) -> None:
    """实时持仓/盈亏推送 — Phase 3 接入账户同步。"""
    await manager.connect(websocket, "portfolio")
    try:
        while True:
            await asyncio.sleep(10)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket, "portfolio")
