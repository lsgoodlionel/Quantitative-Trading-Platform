"""
富途 HK 网关接入 OMS（Epic E / E6）

独立接线 helper，不改动 manager.py 的 __init__/init_hybrid_order_manager。
主循环在混合 OMS 初始化后调用 register_futu_gateway()：
  - 当 Redis 存在 futu 配置（broker_config:futu，enabled=true）→ 为 HK 市场注册 FutuGateway
  - 否则保持 init 阶段注册的 PaperGateway（HK）不变

仿 AlpacaGateway 接入模式（app/oms/manager.py::init_hybrid_order_manager）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Redis 配置键（与 broker_config 端点约定一致）
_FUTU_CONFIG_KEY = "broker_config:futu"


async def _read_futu_config(redis_client) -> dict[str, str]:
    """从 Redis 读取富途配置哈希（bytes/str 兼容）。失败返回空字典。"""
    if redis_client is None:
        return {}
    try:
        raw = await redis_client.hgetall(_FUTU_CONFIG_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read Futu config from Redis: %s", exc)
        return {}
    if not raw:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


def _is_enabled(cfg: dict[str, str]) -> bool:
    return str(cfg.get("enabled", "false")).lower() == "true"


async def register_futu_gateway(manager, redis_client=None) -> bool:
    """
    当 Redis 有 futu 配置时，为 HK 市场注册 FutuGateway（替换 PaperGateway）。

    Args:
        manager: 已初始化并 start() 的 OrderManager
        redis_client: 异步 Redis 客户端（可为 None）

    Returns:
        True  = 成功接入 FutuGateway（HK 实盘/模拟走富途）
        False = 未配置或连接失败（HK 保持 PaperGateway，向后兼容）
    """
    cfg = await _read_futu_config(redis_client)
    if not cfg or not _is_enabled(cfg):
        logger.info("HK market: PaperGateway (Futu not configured)")
        return False

    try:
        from app.gateway.futu_gateway import FutuGateway

        port = cfg.get("port")
        gw_hk = FutuGateway(
            host=cfg.get("host") or None,
            port=int(port) if port else None,
            trade_env=cfg.get("trade_env") or None,
            unlock_pwd=cfg.get("unlock_pwd"),
        )
        await gw_hk.connect()
        manager.register_gateway("HK", gw_hk)
        env = (cfg.get("trade_env") or "SIMULATE").upper()
        mode_label = "SIMULATE (Futu)" if env == "SIMULATE" else "REAL (Futu ⚠ 真实资金)"
        logger.info("HK market: FutuGateway connected (%s)", mode_label)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "FutuGateway connection failed, keeping PaperGateway for HK: %s", exc
        )
        return False
