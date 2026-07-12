"""
数据源注册表与多源配置（Multi-Source Data Channel）

管理每个市场的「多个可配置数据源」：
- 目录（catalog）：每个市场所有可用源的元数据（id/名称/类型/是否需配置/是否实时）
- 配置（config）：每个市场的启用顺序 + 禁用集 + 手动强制源（pin），存 Redis
- 解析（resolve）：按配置返回有序的数据源实例链，供 DataService 逐个尝试

设计原则：
- 始终有兜底：任何真实源都失败时，DataService 用合成演示源，平台永不断供
- 零新增依赖：新增源（AkShare US/HK、Stooq）复用已装的 akshare/httpx
- 动态 + 手动：默认按顺序自动回退；pin 后强制只用该源（失败仍回退 demo）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.data.feeds.akshare_feed import AkShareDataFeed
from app.data.feeds.akshare_global_feed import AkShareHKFeed, AkShareUSFeed
from app.data.feeds.alpaca import AlpacaDataFeed
from app.data.feeds.base import DataFeed
from app.data.feeds.demo_feed import DemoDataFeed
from app.data.feeds.futu import FutuDataFeed
from app.data.feeds.stooq_feed import StooqDataFeed
from app.data.feeds.yfinance_feed import YFinanceDataFeed
from app.data.models import Market

logger = get_logger(__name__)

_REDIS_KEY = "data_config:sources"


@dataclass(frozen=True)
class SourceMeta:
    """数据源元数据（供前端展示与探活）。"""
    id: str
    name: str
    requires: str | None      # None=开箱即用；"alpaca_key"/"futu_opend"=需配置
    realtime: bool
    note: str


# ── 数据源目录（每市场，按推荐默认顺序）──────────────────────────
SOURCE_CATALOG: dict[str, list[SourceMeta]] = {
    "US": [
        SourceMeta("alpaca", "Alpaca", "alpaca_key", True, "官方实时+历史，需 API Key"),
        SourceMeta("yfinance", "yfinance", None, False, "免费历史，无需 Key"),
        SourceMeta("akshare_us", "AkShare 美股", None, False, "免费日线，无需 Key"),
        SourceMeta("stooq", "Stooq", None, False, "免费日/周线 CSV"),
    ],
    "HK": [
        SourceMeta("futu", "富途 OpenAPI", "futu_opend", True, "实时行情，需本地 OpenD"),
        SourceMeta("yfinance", "yfinance", None, False, "免费历史，无需 Key"),
        SourceMeta("akshare_hk", "AkShare 港股", None, False, "免费日线，无需 Key"),
        SourceMeta("stooq", "Stooq", None, False, "免费日/周线 CSV"),
    ],
    "A": [
        SourceMeta("akshare", "AkShare", None, False, "免费日/周线，无需 Key"),
    ],
}


@dataclass
class MarketSourceConfig:
    """单市场的多源配置。"""
    order: list[str]                      # 启用源的顺序（首个为主源）
    disabled: list[str] = field(default_factory=list)
    pinned: str | None = None             # 手动强制源（仅用该源，失败回退 demo）

    def to_dict(self) -> dict:
        return {"order": self.order, "disabled": self.disabled, "pinned": self.pinned}


def _default_config() -> dict[str, MarketSourceConfig]:
    return {
        m: MarketSourceConfig(order=[s.id for s in metas])
        for m, metas in SOURCE_CATALOG.items()
    }


class DataSourceRegistry:
    """数据源注册表单例：持有 feed 实例 + 内存配置。"""

    _instance: "DataSourceRegistry | None" = None

    def __init__(self) -> None:
        # 所有 feed 实例（懒复用；construction 无副作用）
        self._feeds: dict[str, DataFeed] = {
            "alpaca": AlpacaDataFeed(),
            "yfinance_us": YFinanceDataFeed(Market.US),
            "yfinance_hk": YFinanceDataFeed(Market.HK),
            "akshare": AkShareDataFeed(),
            "akshare_us": AkShareUSFeed(),
            "akshare_hk": AkShareHKFeed(),
            "stooq_us": StooqDataFeed(Market.US),
            "stooq_hk": StooqDataFeed(Market.HK),
            "futu": FutuDataFeed(Market.HK),
        }
        self._demo: dict[str, DemoDataFeed] = {
            "US": DemoDataFeed(Market.US),
            "HK": DemoDataFeed(Market.HK),
            "A": DemoDataFeed(Market.A),
        }
        self._config: dict[str, MarketSourceConfig] = _default_config()

    @classmethod
    def instance(cls) -> "DataSourceRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── source_id → feed 实例（按市场消歧）──────────────────────
    def _feed_for(self, source_id: str, market: str) -> DataFeed | None:
        if source_id == "yfinance":
            return self._feeds["yfinance_hk"] if market == "HK" else self._feeds["yfinance_us"]
        if source_id == "stooq":
            return self._feeds["stooq_hk"] if market == "HK" else self._feeds["stooq_us"]
        return self._feeds.get(source_id)

    # ── 配置读写 ──────────────────────────────────────────────
    async def load_config(self, redis_client=None) -> None:
        """从 Redis 载入配置（缺失/损坏时回退默认）。启动与 PUT 后调用。"""
        if redis_client is None:
            return
        try:
            raw = await redis_client.get(_REDIS_KEY)
        except Exception as e:  # noqa: BLE001
            logger.warning("load data-source config failed", error=str(e))
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        cfg = _default_config()
        valid_ids = {m: {s.id for s in metas} for m, metas in SOURCE_CATALOG.items()}
        for m in SOURCE_CATALOG:
            mc = data.get(m) or {}
            order = [x for x in mc.get("order", []) if x in valid_ids[m]]
            # 补上目录里有但配置漏掉的源（追加到末尾，保证不丢源）
            for sid in valid_ids[m]:
                if sid not in order:
                    order.append(sid)
            disabled = [x for x in mc.get("disabled", []) if x in valid_ids[m]]
            pinned = mc.get("pinned")
            if pinned not in valid_ids[m]:
                pinned = None
            cfg[m] = MarketSourceConfig(order=order, disabled=disabled, pinned=pinned)
        self._config = cfg

    async def save_config(self, redis_client=None) -> None:
        if redis_client is None:
            return
        payload = json.dumps({m: c.to_dict() for m, c in self._config.items()})
        try:
            await redis_client.set(_REDIS_KEY, payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("save data-source config failed", error=str(e))

    def get_config(self) -> dict[str, MarketSourceConfig]:
        return self._config

    def set_market_config(self, market: str, cfg: MarketSourceConfig) -> None:
        self._config[market] = cfg

    # ── 解析有序数据源链 ──────────────────────────────────────
    def get_feed_chain(self, market: Market) -> list[DataFeed]:
        """
        返回该市场按配置排序的启用数据源实例链。

        - pinned 存在 → 只返回该源（失败由 DataService 回退 demo）
        - 否则 → order 中未禁用的源，依次排列
        """
        m = market.value
        cfg = self._config.get(m)
        if cfg is None:
            metas = SOURCE_CATALOG.get(m, [])
            return [f for sid in (s.id for s in metas) if (f := self._feed_for(sid, m))]

        if cfg.pinned:
            f = self._feed_for(cfg.pinned, m)
            return [f] if f else []

        chain: list[DataFeed] = []
        for sid in cfg.order:
            if sid in cfg.disabled:
                continue
            f = self._feed_for(sid, m)
            if f is not None:
                chain.append(f)
        return chain

    def get_demo_feed(self, market: Market) -> DemoDataFeed:
        return self._demo[market.value]

    # ── 供状态探活：返回 (source_id, meta, feed) 列表 ───────────
    def iter_sources(self, market: str):
        cfg = self._config.get(market)
        pinned = cfg.pinned if cfg else None
        disabled = set(cfg.disabled) if cfg else set()
        order = cfg.order if cfg else [s.id for s in SOURCE_CATALOG.get(market, [])]
        meta_by_id = {s.id: s for s in SOURCE_CATALOG.get(market, [])}
        for sid in order:
            meta = meta_by_id.get(sid)
            if meta is None:
                continue
            yield sid, meta, self._feed_for(sid, market), {
                "enabled": sid not in disabled,
                "pinned": sid == pinned,
            }
