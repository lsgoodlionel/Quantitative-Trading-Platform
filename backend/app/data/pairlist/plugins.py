"""
标的池生成插件（M7）

`core.py` 的规则链解决的是「从一个已有 universe 里按指标过滤」。
这里补的是链条**最前面**缺的一环：universe 本身从哪来 ——
按市值排名取前 N，或按指数成分股取。

设计上刻意做成与既有规则链可组合：插件产出/收窄 `list[PairMetrics]`，
`apply_chain` 再对结果做指标过滤。所以是「接进 pairlist」而不是另起一套筛选逻辑。

语义（两个插件一致）：
- 传入空列表 → **生成**模式，直接产出该来源的全部标的
- 传入非空   → **收窄**模式，只保留同时属于该来源的标的，并按来源顺序重排
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date

from app.data.models import Market
from app.data.pairlist.core import PairMetrics
from app.data.universe.index_components import index_components
from app.data.universe.market_cap import top_by_market_cap
from app.data.universe.models import UniverseItem


def _from_universe_item(item: UniverseItem) -> PairMetrics:
    return PairMetrics(
        symbol=item.symbol,
        market=item.market,
        name=item.name or item.symbol,
        market_cap=item.market_cap,
    )


def _reorder(items: list[PairMetrics], ordered_symbols: list[str]) -> list[PairMetrics]:
    """按 `ordered_symbols` 的顺序保留交集；不在其中的标的被丢弃。返回新列表。"""
    by_symbol = {it.symbol: it for it in items}
    return [by_symbol[s] for s in ordered_symbols if s in by_symbol]


@dataclass(frozen=True)
class MarketCapPairList:
    """按总市值排名取前 N 的标的池插件。"""

    market: Market
    top_n: int
    exclude: frozenset[str] = field(default_factory=frozenset)

    name = "market_cap"

    async def apply(self, items: list[PairMetrics]) -> list[PairMetrics]:
        ranking = await top_by_market_cap(
            self.market, self.top_n, exclude=set(self.exclude)
        )
        if not items:
            return [_from_universe_item(it) for it in ranking]
        return _reorder(items, [it.symbol for it in ranking])


@dataclass(frozen=True)
class IndexComponentPairList:
    """按指数成分股取标的池的插件。

    `on` 早于数据源快照日期时会抛 `HistoricalComponentsUnavailableError` ——
    刻意不在这里兜底，静默用最新成分回测历史区间就是幸存者偏差。
    """

    index_code: str
    market: Market = Market.A
    on: Date | None = None

    name = "index_component"

    async def apply(self, items: list[PairMetrics]) -> list[PairMetrics]:
        symbols = await index_components(self.index_code, on=self.on)
        if not items:
            return [
                PairMetrics(symbol=s, market=self.market.value, name=s) for s in symbols
            ]
        return _reorder(items, symbols)


async def run_plugins(
    items: list[PairMetrics], plugins: list[MarketCapPairList | IndexComponentPairList]
) -> list[PairMetrics]:
    """按顺序执行插件链，返回新列表（不修改入参）。"""
    result = list(items)
    for plugin in plugins:
        result = await plugin.apply(result)
    return result
