"""市值/成分股宇宙单元测试（Wave M-a / M7）

覆盖：
- 市值榜单命中缓存不重复请求数据源（mock 断言调用次数）
- 榜单降序、缺失市值剔除、top_n 截断、exclude 后名次重新编号、as_of 存在
- 成分股取历史日期而数据源只有最新时 → 报错，不静默返回最新
- 不支持的指数代码 / 别名归一
- MarketCapPairList / IndexComponentPairList 两个插件能与既有规则链链式组合
"""

from __future__ import annotations

import json
from datetime import date
from importlib import import_module

import pytest

from app.data.models import Market
from app.data.pairlist import (
    IndexComponentPairList,
    MarketCapPairList,
    PairlistRule,
    PairMetrics,
    apply_chain,
    run_plugins,
)
from app.data.screener import Candidate
from app.data.universe import (
    HistoricalComponentsUnavailableError,
    IndexNotSupportedError,
    UniverseItem,
    index_components,
    normalize_index_code,
    top_by_market_cap,
)
from app.data.universe.index_components import ComponentSnapshot, IndexSpec

# 包根再导出的 `index_components` **函数**会遮蔽同名子模块，
# 因此 monkeypatch 必须拿 import_module 取到真正的模块对象，不能用点号字符串路径。
_INDEX_MOD = import_module("app.data.universe.index_components")
_MCAP_MOD = import_module("app.data.universe.market_cap")


# ── 测试替身 ───────────────────────────────────────────────────
class _MemoryCache:
    """进程内缓存替身，记录读写次数。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.writes = 0

    def read(self, key: str):
        raw = self.store.get(key)
        return json.loads(raw) if raw else None

    def write(self, key: str, value, ttl: int) -> None:
        self.writes += 1
        self.store[key] = json.dumps(value)


@pytest.fixture
def cache(monkeypatch) -> _MemoryCache:
    """把 market_cap / index_components 两处的缓存都换成内存替身。"""
    memory = _MemoryCache()
    for module in (_MCAP_MOD, _INDEX_MOD):
        monkeypatch.setattr(module, "read_json", memory.read)
        monkeypatch.setattr(module, "write_json", memory.write)
    return memory


class _SnapshotSpy:
    """替代 screener.get_snapshot，统计被调用次数。"""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates
        self.calls = 0

    async def __call__(self, market: Market) -> list[Candidate]:
        self.calls += 1
        return list(self._candidates)


def _candidates() -> list[Candidate]:
    return [
        Candidate(symbol="AAPL", market="US", name="Apple", market_cap=3.0e12),
        Candidate(symbol="MSFT", market="US", name="Microsoft", market_cap=2.8e12),
        Candidate(symbol="NVDA", market="US", name="Nvidia", market_cap=2.2e12),
        Candidate(symbol="NOCAP", market="US", name="Unknown", market_cap=None),
    ]


@pytest.fixture
def snapshot(monkeypatch) -> _SnapshotSpy:
    spy = _SnapshotSpy(_candidates())
    monkeypatch.setattr("app.data.screener.get_snapshot", spy)
    return spy


# ── 市值榜单 ───────────────────────────────────────────────────
class TestTopByMarketCap:
    @pytest.mark.asyncio
    async def test_returns_descending_top_n(self, cache, snapshot) -> None:
        # Act
        top = await top_by_market_cap(Market.US, 2)

        # Assert
        assert [it.symbol for it in top] == ["AAPL", "MSFT"]
        assert [it.rank for it in top] == [1, 2]

    @pytest.mark.asyncio
    async def test_drops_symbols_without_market_cap(self, cache, snapshot) -> None:
        top = await top_by_market_cap(Market.US, 10)

        assert "NOCAP" not in [it.symbol for it in top]

    @pytest.mark.asyncio
    async def test_second_call_hits_cache_without_refetching(self, cache, snapshot) -> None:
        # Arrange / Act：连续两次请求同一市场
        await top_by_market_cap(Market.US, 3)
        await top_by_market_cap(Market.US, 2)

        # Assert：数据源只被打了一次
        assert snapshot.calls == 1
        assert cache.writes == 1

    @pytest.mark.asyncio
    async def test_cache_miss_refetches(self, cache, snapshot) -> None:
        await top_by_market_cap(Market.US, 3)
        cache.store.clear()

        await top_by_market_cap(Market.US, 3)

        assert snapshot.calls == 2

    @pytest.mark.asyncio
    async def test_exclude_renumbers_ranks(self, cache, snapshot) -> None:
        top = await top_by_market_cap(Market.US, 3, exclude={"MSFT"})

        assert [(it.symbol, it.rank) for it in top] == [("AAPL", 1), ("NVDA", 2)]

    @pytest.mark.asyncio
    async def test_items_carry_as_of_timestamp(self, cache, snapshot) -> None:
        top = await top_by_market_cap(Market.US, 1)

        assert top[0].as_of
        assert top[0].as_of == top[0].as_of.strip()

    @pytest.mark.asyncio
    async def test_rejects_non_positive_top_n(self, cache, snapshot) -> None:
        with pytest.raises(ValueError, match="top_n"):
            await top_by_market_cap(Market.US, 0)

    def test_market_cap_yi_conversion(self) -> None:
        item = UniverseItem(symbol="AAPL", market="US", market_cap=3.0e12)

        assert item.market_cap_yi == 30000.0
        assert item.to_dict()["market_cap_yi"] == 30000.0


# ── 指数成分股 ─────────────────────────────────────────────────
def _patch_snapshot(monkeypatch, symbols: list[str], as_of: date | None) -> None:
    """替换底层数据源，返回固定的成分快照。"""

    async def _fake_load(spec: IndexSpec) -> ComponentSnapshot:
        return ComponentSnapshot(spec.code, spec.name, tuple(symbols), as_of)

    monkeypatch.setattr(_INDEX_MOD, "_load_snapshot", _fake_load)


class TestIndexComponents:
    @pytest.mark.asyncio
    async def test_latest_components(self, monkeypatch) -> None:
        _patch_snapshot(monkeypatch, ["600519", "000001"], date(2024, 6, 30))

        assert await index_components("000300") == ["600519", "000001"]

    @pytest.mark.asyncio
    async def test_historical_date_before_snapshot_raises(self, monkeypatch) -> None:
        # Arrange：数据源只有 2024-06-30 的最新成分
        _patch_snapshot(monkeypatch, ["600519"], date(2024, 6, 30))

        # Act / Assert：要 2020 年的成分 → 必须报错，不能静默返回最新（幸存者偏差）
        with pytest.raises(HistoricalComponentsUnavailableError, match="幸存者偏差"):
            await index_components("000300", on=date(2020, 1, 1))

    @pytest.mark.asyncio
    async def test_date_after_snapshot_is_allowed(self, monkeypatch) -> None:
        # 快照日之后的日期不含未来信息，允许使用
        _patch_snapshot(monkeypatch, ["600519"], date(2024, 6, 30))

        assert await index_components("000300", on=date(2024, 9, 1)) == ["600519"]

    @pytest.mark.asyncio
    async def test_missing_as_of_with_explicit_date_raises(self, monkeypatch) -> None:
        _patch_snapshot(monkeypatch, ["600519"], None)

        with pytest.raises(HistoricalComponentsUnavailableError):
            await index_components("000300", on=date(2024, 1, 1))

    @pytest.mark.asyncio
    async def test_missing_as_of_without_date_is_fine(self, monkeypatch) -> None:
        _patch_snapshot(monkeypatch, ["600519"], None)

        assert await index_components("000300") == ["600519"]

    @pytest.mark.asyncio
    async def test_unsupported_index_raises(self) -> None:
        with pytest.raises(IndexNotSupportedError):
            await index_components("SPX")

    def test_alias_normalization(self) -> None:
        assert normalize_index_code("hs300") == "000300"
        assert normalize_index_code(" csi500 ") == "000905"
        assert normalize_index_code("000016") == "000016"

    def test_empty_index_code_raises(self) -> None:
        with pytest.raises(IndexNotSupportedError):
            normalize_index_code("   ")


# ── pairlist 插件组合 ──────────────────────────────────────────
def _metrics() -> list[PairMetrics]:
    return [
        PairMetrics("AAPL", "US", "Apple", price=190.0, volume=5_000_000, market_cap=3.0e12),
        PairMetrics("MSFT", "US", "Microsoft", price=410.0, volume=2_000_000, market_cap=2.8e12),
        PairMetrics("NVDA", "US", "Nvidia", price=120.0, volume=9_000_000, market_cap=2.2e12),
    ]


class TestPairlistPlugins:
    @pytest.mark.asyncio
    async def test_market_cap_plugin_generates_universe(self, cache, snapshot) -> None:
        plugin = MarketCapPairList(market=Market.US, top_n=2)

        produced = await plugin.apply([])

        assert [m.symbol for m in produced] == ["AAPL", "MSFT"]
        assert produced[0].market_cap == 3.0e12

    @pytest.mark.asyncio
    async def test_market_cap_plugin_narrows_existing_items(self, cache, snapshot) -> None:
        plugin = MarketCapPairList(market=Market.US, top_n=2)

        narrowed = await plugin.apply(_metrics())

        assert [m.symbol for m in narrowed] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_index_plugin_narrows_existing_items(self, monkeypatch) -> None:
        _patch_snapshot(monkeypatch, ["NVDA", "AAPL"], date(2024, 6, 30))
        plugin = IndexComponentPairList(index_code="000300", market=Market.US)

        narrowed = await plugin.apply(_metrics())

        assert [m.symbol for m in narrowed] == ["NVDA", "AAPL"]

    @pytest.mark.asyncio
    async def test_index_plugin_propagates_survivorship_error(self, monkeypatch) -> None:
        _patch_snapshot(monkeypatch, ["NVDA"], date(2024, 6, 30))
        plugin = IndexComponentPairList(index_code="000300", on=date(2020, 1, 1))

        with pytest.raises(HistoricalComponentsUnavailableError):
            await plugin.apply(_metrics())

    @pytest.mark.asyncio
    async def test_two_plugins_chain_together(self, cache, snapshot, monkeypatch) -> None:
        # Arrange: 市值前 3 → 再与指数成分股取交集
        _patch_snapshot(monkeypatch, ["NVDA", "MSFT"], date(2024, 6, 30))
        plugins = [
            MarketCapPairList(market=Market.US, top_n=3),
            IndexComponentPairList(index_code="000300", market=Market.US),
        ]

        # Act
        result = await run_plugins([], plugins)

        # Assert
        assert [m.symbol for m in result] == ["NVDA", "MSFT"]

    @pytest.mark.asyncio
    async def test_plugins_compose_with_existing_rule_chain(self, cache, snapshot) -> None:
        # Arrange: 插件先产出市值前 3，再交给既有规则链按成交量过滤 + 排序
        produced = await run_plugins(_metrics(), [MarketCapPairList(Market.US, 3)])

        # Act
        filtered = apply_chain(
            produced,
            [PairlistRule(kind="volume", min_value=3_000_000, sort="desc", top=1)],
        )

        # Assert
        assert [m.symbol for m in filtered] == ["NVDA"]

    @pytest.mark.asyncio
    async def test_run_plugins_does_not_mutate_input(self, cache, snapshot) -> None:
        items = _metrics()

        await run_plugins(items, [MarketCapPairList(Market.US, 1)])

        assert len(items) == 3
