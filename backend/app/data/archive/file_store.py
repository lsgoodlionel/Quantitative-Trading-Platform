"""
文件型归档基类（M1）

统一实现目录布局、路径穿越兜底、合并去重、覆盖区间与删除；
子类只需实现「一个文件 ↔ 一个列式字典」的读写（`_load` / `_dump`）。

目录布局：`{archive_root}/{market}/{frequency}/{symbol}{ext}`
"""

from __future__ import annotations

import logging
import os
from abc import abstractmethod
from datetime import date as Date
from pathlib import Path

from app.data.archive.base import ArchiveError, ArchiveHandler, ArchiveKey, validate_symbol
from app.data.archive.columns import bars_to_columns, columns_to_bars
from app.data.archive.locking import file_lock
from app.data.models import Bar, Frequency, Market

logger = logging.getLogger(__name__)


class FileArchiveBase(ArchiveHandler):
    """按 `market/frequency/symbol` 三级布局落盘的归档实现骨架。"""

    #: 文件后缀（含点），由子类给出
    extension: str = ""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    # ── 子类实现 ──────────────────────────────────────────────
    @abstractmethod
    def _load(self, path: Path) -> dict[str, list]:
        """读取单个归档文件为列式字典。"""

    @abstractmethod
    def _dump(self, path: Path, data: dict[str, list]) -> None:
        """把列式字典写入单个归档文件（覆盖写）。"""

    # ── 路径 ──────────────────────────────────────────────────
    def path_for(self, key: ArchiveKey) -> Path:
        """归档文件路径。第二道防线：解析后必须仍在 root 之内。"""
        market, frequency = key.relative_dir
        candidate = self._root / market / frequency / f"{validate_symbol(key.symbol)}{self.extension}"
        resolved = Path(candidate).resolve()
        if not _is_within(resolved, self._root):
            raise ArchiveError(f"归档路径逃逸出根目录: {resolved}")
        return resolved

    # ── ArchiveHandler ────────────────────────────────────────
    def read(self, key: ArchiveKey, start: Date, end: Date) -> list[Bar]:
        if end < start:
            raise ArchiveError(f"end {end} 早于 start {start}")
        bars = self._read_all(key)
        return [b for b in bars if start <= b.time.date() <= end]

    def write(self, key: ArchiveKey, bars: list[Bar]) -> int:
        """
        写入并返回实际新增条数。

        整个「读 → 合并 → 覆盖」持有文件锁，落盘用临时文件 + 原子 rename：
        并发写不会丢更新，写到一半崩溃也不会留下半截文件。
        """
        _assert_bars_match_key(key, bars)
        if not bars:
            return 0

        path = self.path_for(key)
        with file_lock(path):
            return self._merge_and_dump(key, path, bars)

    def _merge_and_dump(self, key: ArchiveKey, path: Path, bars: list[Bar]) -> int:
        existing = {b.time: b for b in self._read_all(key)}
        added = [b for b in bars if b.time not in existing]
        if not added:
            return 0

        merged = sorted([*existing.values(), *_dedupe_by_time(added)], key=lambda b: b.time)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        try:
            self._dump(temp_path, bars_to_columns(merged))
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return len(merged) - len(existing)

    def list_keys(self) -> list[ArchiveKey]:
        if not self._root.is_dir():
            return []
        keys = [
            key
            for path in sorted(self._root.glob(f"*/*/*{self.extension}"))
            if (key := _key_from_path(path, self.extension)) is not None
        ]
        return keys

    def coverage(self, key: ArchiveKey) -> tuple[Date, Date] | None:
        bars = self._read_all(key)
        if not bars:
            return None
        return bars[0].time.date(), bars[-1].time.date()

    def delete(self, key: ArchiveKey) -> bool:
        path = self.path_for(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    # ── 内部 ──────────────────────────────────────────────────
    def _read_all(self, key: ArchiveKey) -> list[Bar]:
        path = self.path_for(key)
        if not path.is_file():
            return []
        try:
            data = self._load(path)
        except ArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveError(f"读取归档 {path} 失败: {exc}") from exc
        return columns_to_bars(key, data)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _dedupe_by_time(bars: list[Bar]) -> list[Bar]:
    """同一批次内同一时间戳只保留最后一条（后写覆盖先写）。"""
    unique: dict = {}
    for bar in bars:
        unique[bar.time] = bar
    return list(unique.values())


def _assert_bars_match_key(key: ArchiveKey, bars: list[Bar]) -> None:
    """拒绝把不属于该 key 的 bar 写进来 —— 静默混写会让归档数据永久错乱。"""
    for bar in bars:
        if bar.symbol != key.symbol or bar.market != key.market or bar.frequency != key.frequency:
            raise ArchiveError(
                f"bar {bar.symbol}/{bar.market}/{bar.frequency} 与归档 key "
                f"{key.symbol}/{key.market}/{key.frequency} 不匹配"
            )


def _key_from_path(path: Path, extension: str) -> ArchiveKey | None:
    """由文件路径反推 ArchiveKey；无法识别的文件跳过（返回 None）。"""
    try:
        symbol = path.name[: -len(extension)] if extension else path.name
        return ArchiveKey(
            symbol=symbol,
            market=Market(path.parent.parent.name),
            frequency=Frequency(path.parent.name),
        )
    except (ValueError, ArchiveError) as exc:
        logger.debug("跳过无法识别的归档文件 %s: %s", path, exc)
        return None
