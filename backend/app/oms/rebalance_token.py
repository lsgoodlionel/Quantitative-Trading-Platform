"""
再平衡确认令牌（V3 Wave A-b · G2）

预览与执行之间价格与持仓都会变。没有这道校验，一次陈旧的预览就能在市场
大幅波动后被原样执行 —— 这是真金白银的端点，令牌是必需项而非装饰。

令牌是**无状态**的（HMAC 签名，不落 Redis），内含三样东西：
1. 签发时间戳 —— 超过 TTL 一律拒绝
2. 持仓快照哈希 —— 两步之间持仓变了一律拒绝
3. 调仓腿哈希 —— 客户端改了 legs 一律拒绝（否则等于让前端决定下什么单）
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Iterable, Mapping

from app.core.config import settings
from app.engine.portfolio.rebalance import RebalanceLeg

logger = logging.getLogger(__name__)

#: 令牌有效期（秒）。预览结果超过这个时长必须重新预览。
REBALANCE_TOKEN_TTL_SECONDS = 60

_TOKEN_VERSION = "rb1"
_FIELD_SEP = "."
_SIGNATURE_LENGTH = 32


class RebalanceTokenError(Exception):
    """令牌不合法：格式错误、签名不符、过期，或持仓/腿已变化。"""


def snapshot_hash(market: str, positions: Mapping[str, int]) -> str:
    """持仓快照哈希：市场 + 每个非零持仓的 (symbol, qty)，与顺序无关。"""
    parts = [market.upper()]
    parts += [f"{sym}:{qty}" for sym, qty in sorted(positions.items()) if qty]
    return _sha256("|".join(parts))


def legs_hash(legs: Iterable[RebalanceLeg]) -> str:
    """调仓腿哈希：只覆盖真正决定下单的字段（标的 + 带符号股数）。"""
    parts = sorted(f"{leg.symbol}:{leg.delta_qty}" for leg in legs)
    return _sha256("|".join(parts))


def issue_token(market: str, positions: Mapping[str, int], legs: Iterable[RebalanceLeg]) -> str:
    """签发确认令牌。返回 `rb1.<签发秒>.<持仓哈希>.<腿哈希>.<签名>`。"""
    payload = _FIELD_SEP.join(
        (
            _TOKEN_VERSION,
            str(int(time.time())),
            snapshot_hash(market, positions),
            legs_hash(legs),
        )
    )
    return f"{payload}{_FIELD_SEP}{_sign(payload)}"


def verify_token(
    token: str,
    *,
    market: str,
    positions: Mapping[str, int],
    legs: Iterable[RebalanceLeg],
    ttl_seconds: int = REBALANCE_TOKEN_TTL_SECONDS,
) -> None:
    """
    校验令牌。任何一项不符都抛 `RebalanceTokenError`，调用方应回 409 要求重新预览。

    校验顺序刻意是「签名 → 过期 → 持仓 → 腿」：先证明令牌确实由本服务签发，
    再谈内容，避免用伪造令牌探测账户持仓。
    """
    version, issued_at, snap, legs_digest, signature = _split(token)
    if version != _TOKEN_VERSION:
        raise RebalanceTokenError(f"令牌版本不支持: {version}")

    payload = _FIELD_SEP.join((version, str(issued_at), snap, legs_digest))
    if not hmac.compare_digest(signature, _sign(payload)):
        raise RebalanceTokenError("令牌签名不合法")

    age = int(time.time()) - issued_at
    if age < 0 or age > ttl_seconds:
        raise RebalanceTokenError(
            f"令牌已过期（{age}s > {ttl_seconds}s），请重新预览后再执行"
        )

    if snap != snapshot_hash(market, positions):
        raise RebalanceTokenError("持仓在预览与执行之间发生了变化，请重新预览")

    if legs_digest != legs_hash(legs):
        raise RebalanceTokenError("调仓明细与预览结果不一致，请重新预览")


def _split(token: str) -> tuple[str, int, str, str, str]:
    parts = token.split(_FIELD_SEP) if token else []
    if len(parts) != 5:
        raise RebalanceTokenError("令牌格式非法")
    version, issued_raw, snap, legs_digest, signature = parts
    try:
        issued_at = int(issued_raw)
    except ValueError as exc:
        raise RebalanceTokenError("令牌时间戳非法") from exc
    return version, issued_at, snap, legs_digest, signature


def _sign(payload: str) -> str:
    key = settings.secret_key.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:_SIGNATURE_LENGTH]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:_SIGNATURE_LENGTH]
