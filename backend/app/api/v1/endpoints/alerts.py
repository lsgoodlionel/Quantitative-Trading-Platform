"""
价格预警 API

提供价格触发预警的增删改查与检查功能。

预警条件:
  - above     : 价格高于阈值触发
  - below     : 价格低于阈值触发
  - pct_change: |当前价格 - 基准价格| / 基准价格 >= 阈值（百分比）

存储:
  - 使用进程内字典（适合单机演示）；生产环境应替换为 Redis 或数据库表。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Alerts"])

# ── In-memory store ───────────────────────────────────────────────

AlertCondition = Literal["above", "below", "pct_change"]

_alerts: dict[str, dict] = {}


# ── Schemas ───────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    symbol:    str            = Field(min_length=1, max_length=20)
    market:    str            = Field(default="US", pattern="^(US|HK|A)$")
    condition: AlertCondition = Field(default="above")
    threshold: float          = Field(
        gt=0,
        description="价格（above/below）或百分比变动（pct_change，如 5.0=5%）",
    )
    base_price: float | None  = Field(
        default=None,
        gt=0,
        description="pct_change 条件时的基准价格；留空则以阈值本身作为基准",
    )
    note:      str            = Field(default="", max_length=200)


class AlertUpdate(BaseModel):
    is_active: bool


class PricePoint(BaseModel):
    symbol: str  = Field(min_length=1, max_length=20)
    market: str  = Field(pattern="^(US|HK|A)$")
    price:  float = Field(gt=0, description="当前价格，必须大于 0")


class AlertCheckRequest(BaseModel):
    prices: list[PricePoint]


# ── Helpers ───────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_condition(alert: dict, price: float) -> bool:
    condition = alert["condition"]
    threshold = alert["threshold"]
    if condition == "above":
        return price > threshold
    if condition == "below":
        return price < threshold
    if condition == "pct_change":
        base = alert.get("base_price") or threshold
        if base == 0:
            return False
        return abs(price - base) / abs(base) * 100 >= threshold
    return False


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("")
async def list_alerts() -> list[dict]:
    """返回所有预警（按创建时间倒序）。"""
    return sorted(_alerts.values(), key=lambda a: a["created_at"], reverse=True)


@router.post("", status_code=201)
async def create_alert(body: AlertCreate) -> dict:
    """创建新价格预警。"""
    alert_id = str(uuid.uuid4())
    alert = {
        "id":          alert_id,
        "symbol":      body.symbol.upper().strip(),
        "market":      body.market,
        "condition":   body.condition,
        "threshold":   body.threshold,
        "base_price":  body.base_price,
        "note":        body.note,
        "is_active":   True,
        "is_triggered": False,
        "created_at":  _now_iso(),
        "triggered_at": None,
    }
    _alerts[alert_id] = alert
    return alert


@router.delete("/{alert_id}")
async def delete_alert(alert_id: str) -> dict:
    """删除预警。"""
    if alert_id not in _alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    del _alerts[alert_id]
    return {"deleted": alert_id}


@router.patch("/{alert_id}")
async def update_alert(alert_id: str, body: AlertUpdate) -> dict:
    """启用或暂停预警。"""
    if alert_id not in _alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    _alerts[alert_id] = {**_alerts[alert_id], "is_active": body.is_active}
    return _alerts[alert_id]


@router.post("/check")
async def check_alerts(body: AlertCheckRequest) -> dict:
    """
    对当前价格列表检查所有激活中的预警。

    为每个触发的预警标记 is_triggered=True 并记录时间戳。
    返回本次检查中新触发的预警列表。
    """
    price_map: dict[tuple[str, str], float] = {
        (p.symbol.upper(), p.market): p.price
        for p in body.prices
    }
    newly_triggered: list[dict] = []

    for alert_id, alert in list(_alerts.items()):
        if not alert["is_active"] or alert["is_triggered"]:
            continue
        key = (alert["symbol"], alert["market"])
        price = price_map.get(key)
        if price is None:
            continue
        if _check_condition(alert, price):
            updated = {
                **alert,
                "is_triggered": True,
                "triggered_at": _now_iso(),
            }
            _alerts[alert_id] = updated
            newly_triggered.append(updated)

    return {"triggered": newly_triggered, "count": len(newly_triggered)}


@router.post("/{alert_id}/reset")
async def reset_alert(alert_id: str) -> dict:
    """将已触发的预警重置为未触发状态（可重新激活监控）。"""
    if alert_id not in _alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    _alerts[alert_id] = {
        **_alerts[alert_id],
        "is_triggered": False,
        "triggered_at": None,
        "is_active":    True,
    }
    return _alerts[alert_id]
