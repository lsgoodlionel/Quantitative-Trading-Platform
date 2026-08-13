"""本地数据归档单元测试（Wave M-a / M1）

覆盖：
- 写入 → 读回，bar 逐笔一致（含 time/ohlcv/vwap/turnover/trade_count 全字段）
- 路径穿越防护：symbol 含 "../" / "/" / "\\" / 空字节一律拒绝
- coverage 返回实际区间；空归档返回 None
- list_keys / delete / 区间裁剪 / 合并去重
- find_gaps 五种情形：完全命中 / 头部缺 / 尾部缺 / 中间缺 / 全缺
- boundary_gaps（DataService 用的两端缺口口径）
- archive_enabled=False 时 DataService 行为与归档上线前完全一致
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.archive import (
    ArchiveError,
    ArchiveKey,
    Gap,
    InvalidSymbolError,
    NpzArchive,
    boundary_gaps,
    create_archive,
    find_gaps,
    validate_symbol,
)
from app.data.models import Bar, Frequency, Market
from app.data.service import DataService

# ── 公用构造器 ─────────────────────────────────────────────────

def _bar(day: int, symbol: str = "AAPL", close: float = 100.0, **extra) -> Bar:
    return Bar(
        time=datetime(2024, 3, day, 20, 0, tzinfo=UTC),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close - 1,
        high=close + 2,
        low=close - 3,
        close=close,
        volume=1_000_000 + day,
        **extra,
    )


@pytest.fixture
def archive(tmp_path) -> NpzArchive:
    return NpzArchive(tmp_path / "archive")


@pytest.fixture
def key() -> ArchiveKey:
    return ArchiveKey(symbol="AAPL", market=Market.US, frequency=Frequency.DAY_1)


# ── 读写往返 ───────────────────────────────────────────────────
class TestRoundTrip:
    def test_write_then_read_returns_identical_bars(self, archive, key) -> None:
        # Arrange：带上全部可空字段，确保它们也无损往返
        bars = [
            _bar(1, close=100.0, vwap=99.5, turnover=1.5e8, trade_count=12345),
            _bar(2, close=101.0, vwap=None, turnover=None, trade_count=None),
        ]

        # Act
        written = archive.write(key, bars)
        read_back = archive.read(key, date(2024, 3, 1), date(2024, 3, 31))

        # Assert：逐笔逐字段一致
        assert written == 2
        assert read_back == bars

    def test_write_returns_only_newly_added_count(self, archive, key) -> None:
        archive.write(key, [_bar(1), _bar(2)])

        added = archive.write(key, [_bar(2), _bar(3), _bar(4)])

        assert added == 2
        assert len(archive.read(key, date(2024, 3, 1), date(2024, 3, 31))) == 4

    def test_write_empty_is_noop(self, archive, key) -> None:
        assert archive.write(key, []) == 0
        assert archive.coverage(key) is None

    def test_read_clips_to_requested_range(self, archive, key) -> None:
        archive.write(key, [_bar(d) for d in (1, 5, 9)])

        clipped = archive.read(key, date(2024, 3, 4), date(2024, 3, 6))

        assert [b.time.date() for b in clipped] == [date(2024, 3, 5)]

    def test_read_missing_archive_returns_empty(self, archive, key) -> None:
        assert archive.read(key, date(2024, 3, 1), date(2024, 3, 2)) == []

    def test_read_rejects_inverted_range(self, archive, key) -> None:
        with pytest.raises(ArchiveError):
            archive.read(key, date(2024, 3, 5), date(2024, 3, 1))

    def test_write_rejects_bars_from_another_symbol(self, archive, key) -> None:
        # 静默混写会让归档永久错乱，必须显式报错
        with pytest.raises(ArchiveError, match="不匹配"):
            archive.write(key, [_bar(1, symbol="MSFT")])


# ── 路径穿越防护 ───────────────────────────────────────────────
class TestPathTraversal:
    def test_rejects_parent_dir_token(self) -> None:
        with pytest.raises(InvalidSymbolError):
            ArchiveKey(symbol="../../etc/passwd", market=Market.US, frequency=Frequency.DAY_1)

    def test_rejects_forward_slash(self) -> None:
        with pytest.raises(InvalidSymbolError):
            ArchiveKey(symbol="US/AAPL", market=Market.US, frequency=Frequency.DAY_1)

    def test_rejects_backslash(self) -> None:
        with pytest.raises(InvalidSymbolError):
            ArchiveKey(symbol="US\\AAPL", market=Market.US, frequency=Frequency.DAY_1)

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(InvalidSymbolError):
            ArchiveKey(symbol="AAPL\x00.png", market=Market.US, frequency=Frequency.DAY_1)

    def test_rejects_empty_symbol(self) -> None:
        with pytest.raises(InvalidSymbolError):
            validate_symbol("")

    def test_rejects_overlong_symbol(self) -> None:
        with pytest.raises(InvalidSymbolError):
            validate_symbol("A" * 33)

    def test_rejects_trailing_newline(self) -> None:
        # 正则用 `$` 结尾时 "AAPL\n" 会漏网（`$` 匹配到结尾换行之前），必须用 `\Z`
        with pytest.raises(InvalidSymbolError):
            validate_symbol("AAPL\n")

    def test_rejects_punctuation_only_symbol(self) -> None:
        for symbol in (".", "-", "^", "==", "._-"):
            with pytest.raises(InvalidSymbolError):
                validate_symbol(symbol)

    def test_rejects_windows_reserved_names(self) -> None:
        for symbol in ("CON", "nul", "COM1", "LPT9", "aux.dat"):
            with pytest.raises(InvalidSymbolError):
                validate_symbol(symbol)

    def test_accepts_real_world_symbols(self) -> None:
        for symbol in ("AAPL", "BRK.B", "00700", "0700.HK", "^GSPC", "BF=F", "600519"):
            assert validate_symbol(symbol) == symbol

    def test_archived_file_stays_under_root(self, archive, key) -> None:
        archive.write(key, [_bar(1)])

        path = archive.path_for(key)

        assert archive.root in path.parents
        assert path.name == "AAPL.npz"


# ── coverage / list_keys / delete ──────────────────────────────
class TestCoverageAndKeys:
    def test_coverage_returns_actual_span(self, archive, key) -> None:
        archive.write(key, [_bar(3), _bar(11), _bar(7)])

        assert archive.coverage(key) == (date(2024, 3, 3), date(2024, 3, 11))

    def test_coverage_of_empty_archive_is_none(self, archive, key) -> None:
        assert archive.coverage(key) is None

    def test_list_keys_enumerates_written_entries(self, archive) -> None:
        us = ArchiveKey("AAPL", Market.US, Frequency.DAY_1)
        hk = ArchiveKey("00700", Market.HK, Frequency.HOUR_1)
        archive.write(us, [_bar(1)])
        archive.write(hk, [
            Bar(time=datetime(2024, 3, 1, 2, tzinfo=UTC), symbol="00700", market=Market.HK,
                frequency=Frequency.HOUR_1, open=1, high=2, low=0.5, close=1.5, volume=10),
        ])

        keys = archive.list_keys()

        assert set(keys) == {us, hk}

    def test_list_keys_on_missing_root_is_empty(self, tmp_path) -> None:
        assert NpzArchive(tmp_path / "nope").list_keys() == []

    def test_concurrent_writers_do_not_lose_updates(self, archive, key) -> None:
        # Arrange: 8 个线程各写一天，无互斥时后写者会用旧快照覆盖先写者
        from concurrent.futures import ThreadPoolExecutor

        days = list(range(1, 9))

        # Act
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda d: archive.write(key, [_bar(d)]), days))

        # Assert: 8 天一天不少
        stored = archive.read(key, date(2024, 3, 1), date(2024, 3, 31))
        assert sorted(b.time.date().day for b in stored) == days

    def test_failed_dump_leaves_no_temp_file(self, archive, key, monkeypatch) -> None:
        # Arrange: 写盘中途炸掉
        def _boom(self, path, data) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(type(archive), "_dump", _boom)

        # Act
        with pytest.raises(OSError, match="disk full"):
            archive.write(key, [_bar(1)])

        # Assert: 不留半截临时文件，原文件也没被破坏
        assert list(archive.root.rglob("*.tmp")) == []
        assert archive.coverage(key) is None

    def test_delete_removes_file_and_reports(self, archive, key) -> None:
        archive.write(key, [_bar(1)])

        assert archive.delete(key) is True
        assert archive.delete(key) is False
        assert archive.coverage(key) is None


# ── 后端工厂 ───────────────────────────────────────────────────
class TestFactory:
    def test_create_archive_returns_handler_rooted_at_target(self, tmp_path) -> None:
        handler = create_archive(str(tmp_path / "arc"))

        assert handler.root.name == "arc"
        assert handler.extension in (".parquet", ".npz")


# ── 缺口检测 ───────────────────────────────────────────────────
class TestFindGaps:
    def test_full_hit_returns_no_gap(self) -> None:
        have = [date(2024, 3, 1) + timedelta(days=i) for i in range(5)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 5)) == []

    def test_missing_head(self) -> None:
        have = [date(2024, 3, 3), date(2024, 3, 4), date(2024, 3, 5)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 5)) == [
            Gap(date(2024, 3, 1), date(2024, 3, 2))
        ]

    def test_missing_tail(self) -> None:
        have = [date(2024, 3, 1), date(2024, 3, 2)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 5)) == [
            Gap(date(2024, 3, 3), date(2024, 3, 5))
        ]

    def test_missing_middle(self) -> None:
        have = [date(2024, 3, 1), date(2024, 3, 2), date(2024, 3, 5)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 5)) == [
            Gap(date(2024, 3, 3), date(2024, 3, 4))
        ]

    def test_missing_everything(self) -> None:
        assert find_gaps([], date(2024, 3, 1), date(2024, 3, 5)) == [
            Gap(date(2024, 3, 1), date(2024, 3, 5))
        ]

    def test_multiple_disjoint_gaps(self) -> None:
        have = [date(2024, 3, 2), date(2024, 3, 5)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 6)) == [
            Gap(date(2024, 3, 1), date(2024, 3, 1)),
            Gap(date(2024, 3, 3), date(2024, 3, 4)),
            Gap(date(2024, 3, 6), date(2024, 3, 6)),
        ]

    def test_dates_outside_window_are_ignored(self) -> None:
        have = [date(2023, 1, 1), date(2024, 3, 1), date(2025, 1, 1)]

        assert find_gaps(have, date(2024, 3, 1), date(2024, 3, 2)) == [
            Gap(date(2024, 3, 2), date(2024, 3, 2))
        ]

    def test_inverted_window_raises(self) -> None:
        with pytest.raises(ValueError, match="早于"):
            find_gaps([], date(2024, 3, 5), date(2024, 3, 1))

    def test_calendar_excludes_weekends_from_gaps(self) -> None:
        # Arrange: 2024-03-08 周五 … 2024-03-11 周一；给了日历后周末不算缺
        from app.engine.calendar.us import USCalendar

        have = [date(2024, 3, 8), date(2024, 3, 11)]

        # Act
        gaps = find_gaps(have, date(2024, 3, 8), date(2024, 3, 11), calendar=USCalendar())

        # Assert
        assert gaps == []

    def test_without_calendar_weekend_counts_as_gap(self) -> None:
        have = [date(2024, 3, 8), date(2024, 3, 11)]

        gaps = find_gaps(have, date(2024, 3, 8), date(2024, 3, 11))

        assert gaps == [Gap(date(2024, 3, 9), date(2024, 3, 10))]


class TestBoundaryGaps:
    def test_no_coverage_means_whole_window(self) -> None:
        assert boundary_gaps(None, date(2024, 3, 1), date(2024, 3, 5)) == [
            Gap(date(2024, 3, 1), date(2024, 3, 5))
        ]

    def test_full_coverage_means_no_gap(self) -> None:
        coverage = (date(2024, 2, 1), date(2024, 4, 1))

        assert boundary_gaps(coverage, date(2024, 3, 1), date(2024, 3, 5)) == []

    def test_head_and_tail_gaps(self) -> None:
        coverage = (date(2024, 3, 3), date(2024, 3, 4))

        assert boundary_gaps(coverage, date(2024, 3, 1), date(2024, 3, 6)) == [
            Gap(date(2024, 3, 1), date(2024, 3, 2)),
            Gap(date(2024, 3, 5), date(2024, 3, 6)),
        ]

    def test_interior_holes_are_ignored(self) -> None:
        # 内部空洞不触发在线补齐 —— 否则周末/假日会让归档每次都退化成全量请求
        coverage = (date(2024, 3, 1), date(2024, 3, 5))

        assert boundary_gaps(coverage, date(2024, 3, 1), date(2024, 3, 5)) == []


# ── DataService 接入 ───────────────────────────────────────────
def _service_with_mock_feed(bars: list[Bar]) -> tuple[DataService, AsyncMock, AsyncMock]:
    svc = DataService(AsyncMock())
    repo = AsyncMock()
    repo.get_bars.return_value = []
    repo.save_bars.return_value = len(bars)
    svc._repo = repo

    feed = AsyncMock()
    feed.get_bars.return_value = bars
    return svc, repo, feed


class TestDataServiceArchiveDisabled:
    """archive_enabled=False 时行为必须与归档上线前完全一致。"""

    @pytest.mark.asyncio
    async def test_returns_feed_bars_and_writes_timescale(self, tmp_path) -> None:
        bars = [_bar(1), _bar(2)]
        svc, repo, feed = _service_with_mock_feed(bars)

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 2)
            )

        assert result == bars
        feed.get_bars.assert_awaited_once()
        repo.save_bars.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_never_touches_archive(self, monkeypatch) -> None:
        # 关闭时连归档模块都不该被调用 —— 否则「关掉」就不是真的关掉
        svc, _repo, feed = _service_with_mock_feed([_bar(1)])
        called: list[str] = []
        monkeypatch.setattr(
            "app.data.archive.get_archive",
            lambda: called.append("hit") or None,
        )

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 1)
            )

        assert called == []

    @pytest.mark.asyncio
    async def test_cache_hit_still_short_circuits_feed(self) -> None:
        cached = [_bar(d) for d in range(1, 28)]
        svc, repo, feed = _service_with_mock_feed([])
        repo.get_bars.return_value = cached

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 27)
            )

        assert result == cached
        feed.get_bars.assert_not_awaited()


class TestDataServiceArchiveEnabled:
    @pytest.fixture(autouse=True)
    def _enable_archive(self, tmp_path, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "archive_enabled", True)
        monkeypatch.setattr(settings, "archive_root", str(tmp_path / "arc"))
        from app.data import archive as archive_pkg

        archive_pkg.reset_archive_cache()
        yield
        archive_pkg.reset_archive_cache()

    @pytest.mark.asyncio
    async def test_first_call_fetches_online_and_writes_archive(self) -> None:
        bars = [_bar(1), _bar(2)]
        svc, _repo, feed = _service_with_mock_feed(bars)

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 2)
            )

        from app.data.archive import get_archive

        assert result == bars
        archived = get_archive().read(
            ArchiveKey("AAPL", Market.US, Frequency.DAY_1), date(2024, 3, 1), date(2024, 3, 2)
        )
        assert archived == bars

    @pytest.mark.asyncio
    async def test_second_call_hits_archive_without_online_request(self) -> None:
        bars = [_bar(1), _bar(2)]
        svc, _repo, feed = _service_with_mock_feed(bars)
        window = (date(2024, 3, 1), date(2024, 3, 2))

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            await svc.get_bars("AAPL", Market.US, Frequency.DAY_1, *window)
            feed.get_bars.reset_mock()
            second = await svc.get_bars("AAPL", Market.US, Frequency.DAY_1, *window)

        assert second == bars
        feed.get_bars.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_hit_only_fetches_missing_tail(self) -> None:
        svc, _repo, feed = _service_with_mock_feed([_bar(1), _bar(2)])
        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 2)
            )

            feed.get_bars.reset_mock()
            feed.get_bars.return_value = [_bar(3), _bar(4)]
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 4)
            )

        # 只补 [3, 4] 这一段，不重拉 [1, 2]
        assert feed.get_bars.await_count == 1
        assert feed.get_bars.await_args.args[2:] == (date(2024, 3, 3), date(2024, 3, 4))
        assert [b.time.date().day for b in result] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_synthetic_demo_bars_are_not_archived(self) -> None:
        # 所有真实源失败 → demo 兜底；合成数据一旦落盘就再也分辨不出来
        svc, _repo, feed = _service_with_mock_feed([])
        demo_bars = [_bar(1)]
        demo = AsyncMock()
        demo.get_bars.return_value = demo_bars

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]), \
             patch.object(svc._registry, "get_demo_feed", return_value=demo):
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 1)
            )

        from app.data.archive import get_archive

        assert result == demo_bars
        assert get_archive().coverage(ArchiveKey("AAPL", Market.US, Frequency.DAY_1)) is None

    @pytest.mark.asyncio
    async def test_bad_symbol_falls_back_online_instead_of_crashing(self) -> None:
        bars = [_bar(1, symbol="../evil")]
        svc, _repo, feed = _service_with_mock_feed(bars)

        with patch.object(svc._registry, "get_feed_chain", return_value=[feed]):
            result = await svc.get_bars(
                "../evil", Market.US, Frequency.DAY_1, date(2024, 3, 1), date(2024, 3, 1)
            )

        assert result == bars


# ── Celery 归档下载任务 ────────────────────────────────────────
class TestDownloadArchiveTask:
    """任务本体的参数处理与汇总逻辑（不起 worker，直接调函数体）。"""

    def test_empty_symbols_returns_zeroed_result(self) -> None:
        from app.tasks.archive import download_archive

        result = download_archive.run(symbols=[], market="US")

        assert result["requested"] == 0
        assert result["errors"] == 0

    def test_inverted_window_reports_error_without_fetching(self) -> None:
        from app.tasks.archive import download_archive

        result = download_archive.run(
            symbols=["AAPL"], market="US", start="2024-03-05", end="2024-03-01"
        )

        assert result["errors"] == 1
        assert result["archived"] == 0

    def test_default_window_is_last_year(self) -> None:
        from app.tasks.archive import _parse_window

        start, end = _parse_window(None, "2024-03-31")

        assert end == date(2024, 3, 31)
        assert (end - start).days == 365

    def test_failures_are_collected_not_swallowed(self, monkeypatch) -> None:
        from app.tasks import archive as task_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("源挂了")

        monkeypatch.setattr(task_mod, "_archive_one", _boom)

        result = task_mod.download_archive.run(symbols=["AAPL", "MSFT"], market="US")

        assert result["errors"] == 2
        assert result["archived"] == 0
        assert all("源挂了" in item for item in result["failed"])

    def test_symbol_count_is_capped(self, monkeypatch) -> None:
        from app.tasks import archive as task_mod

        seen: list[str] = []

        async def _record(symbol, *args, **kwargs) -> int:
            seen.append(symbol)
            return 1

        monkeypatch.setattr(task_mod, "_archive_one", _record)

        result = task_mod.download_archive.run(
            symbols=[f"S{i}" for i in range(task_mod.MAX_SYMBOLS_PER_TASK + 50)],
            market="US",
        )

        assert len(seen) == task_mod.MAX_SYMBOLS_PER_TASK
        assert result["written"] == task_mod.MAX_SYMBOLS_PER_TASK
