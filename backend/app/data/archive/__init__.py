"""
本地数据归档（M1）

对外入口是 `create_archive()`：按环境自动挑选后端 —— 有 parquet 引擎就用
`ParquetArchive`，没有就退到零依赖的 `NpzArchive`。两者语义完全一致。

归档默认**关闭**（`settings.archive_enabled = False`）。归档是缓存，
缓存出错的表现是「回测结果莫名其妙变了」，属于最难排查的一类问题，
所以要求用户显式开启。
"""

from __future__ import annotations

import logging

from app.data.archive.base import (
    ArchiveError,
    ArchiveHandler,
    ArchiveKey,
    InvalidSymbolError,
    validate_symbol,
)
from app.data.archive.gaps import Gap, boundary_gaps, find_gaps
from app.data.archive.npz import NpzArchive
from app.data.archive.parquet import ParquetArchive, ParquetEngineUnavailableError, available_engine

logger = logging.getLogger(__name__)

__all__ = [
    "ArchiveError",
    "ArchiveHandler",
    "ArchiveKey",
    "Gap",
    "InvalidSymbolError",
    "NpzArchive",
    "ParquetArchive",
    "ParquetEngineUnavailableError",
    "available_engine",
    "boundary_gaps",
    "create_archive",
    "find_gaps",
    "get_archive",
    "reset_archive_cache",
    "validate_symbol",
]

_cached: ArchiveHandler | None = None


def create_archive(root: str | None = None) -> ArchiveHandler:
    """构造归档后端。`root` 留空则取 `settings.archive_root`。"""
    from app.core.config import settings

    target = root if root is not None else settings.archive_root
    if available_engine() is not None:
        return ParquetArchive(target)
    logger.info("未检测到 parquet 引擎，归档改用 npz 后端（root=%s）", target)
    return NpzArchive(target)


def get_archive() -> ArchiveHandler | None:
    """
    进程级单例；构造失败返回 None（调用方据此退回在线取数，而不是让回测挂掉）。
    """
    global _cached
    if _cached is None:
        try:
            _cached = create_archive()
        except Exception as exc:  # noqa: BLE001
            logger.warning("归档后端构造失败，本次取数退回在线源: %s", exc)
            return None
    return _cached


def reset_archive_cache() -> None:
    """清空单例（测试与配置热更用）。"""
    global _cached
    _cached = None
