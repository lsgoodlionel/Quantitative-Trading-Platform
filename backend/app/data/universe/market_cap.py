"""
市值排名宇宙（M7）

数据来源沿用 `app/data/screener.py` 的快照：A 股走 AkShare 东财实时行情
（一次拉全市场，自带总市值），美股/港股走 yfinance 批量下载 + 基本面。
**不新增依赖，也不再写一份采集逻辑。**

榜单结果整体缓存进 Redis（TTL 6 小时）并在每个 item 上带 `as_of`：
逐个标的拉市值在 500 只规模下会非常慢且触发限流。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.data.models import Market
from app.data.universe.cache import MARKET_CAP_TTL, read_json, write_json
from app.data.universe.models import UniverseItem

logger = logging.getLogger(__name__)

MAX_TOP_N = 500


def _cache_key(market: Market) -> str:
    return f"universe:market_cap:{market.value}"


async def market_cap_ranking(market: Market) -> list[UniverseItem]:
    """
    该市场按总市值降序的完整榜单（命中缓存则不再请求数据源）。

    缺失市值的标的直接剔除 —— 排名里塞进 None 只会让下游的 top_n 语义失真。
    """
    cached = read_json(_cache_key(market))
    if cached:
        return [UniverseItem(**item) for item in cached]

    from app.data import screener

    candidates = await screener.get_snapshot(market)
    as_of = datetime.now(UTC).isoformat()

    ranked = sorted(
        (c for c in candidates if c.market_cap is not None),
        key=lambda c: c.market_cap,
        reverse=True,
    )
    items = [
        UniverseItem(
            symbol=c.symbol,
            market=c.market,
            name=c.name,
            market_cap=c.market_cap,
            rank=i,
            as_of=as_of,
        )
        for i, c in enumerate(ranked, start=1)
    ]
    if items:
        write_json(_cache_key(market), [item.__dict__ for item in items], MARKET_CAP_TTL)
    return items


async def top_by_market_cap(
    market: Market, top_n: int, *, exclude: set[str] | None = None
) -> list[UniverseItem]:
    """
    按总市值取前 N。

    Args:
        market:  市场
        top_n:   取前 N（1..500）
        exclude: 需要排除的标的代码（大小写敏感，与数据源口径一致）

    Returns:
        按市值降序的 UniverseItem 列表，`rank` 为**排除后**重新编号的名次。
    """
    if top_n <= 0:
        raise ValueError(f"top_n 必须为正数，收到 {top_n}")
    limit = min(int(top_n), MAX_TOP_N)

    ranking = await market_cap_ranking(market)
    blocked = exclude or set()
    kept = [item for item in ranking if item.symbol not in blocked][:limit]

    # 排除会在名次里留下空洞，重新编号让调用方看到的名次是连续的
    return [
        UniverseItem(
            symbol=item.symbol,
            market=item.market,
            name=item.name,
            market_cap=item.market_cap,
            rank=i,
            as_of=item.as_of,
        )
        for i, item in enumerate(kept, start=1)
    ]
