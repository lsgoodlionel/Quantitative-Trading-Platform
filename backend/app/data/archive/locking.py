"""
归档写入的进程间互斥（M1）

归档的写入是「读全量 → 内存合并 → 整文件覆盖」。同一个 key 可能同时被多个
Celery worker 的下载任务、以及回测取数时的缺口回填写入 —— 没有互斥的话，
后写的一方会用自己那份旧快照覆盖掉先写方刚合并进去的 bar（丢更新），
而且**不报任何错**，表现为「归档莫名其妙少了几天数据」。

用 POSIX 的 `fcntl.flock` 建议锁（stdlib，不新增依赖）。非 POSIX 平台上
`fcntl` 不可用，此时降级为无锁并只警告一次 —— 单进程使用仍然正确。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_SUFFIX = ".lock"

try:  # pragma: no cover - 平台相关
    import fcntl

    _HAS_FLOCK = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    _HAS_FLOCK = False

_warned_no_lock = False


@contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """对 `target` 加独占写锁（锁文件是 `target` 同目录的 `<name>.lock`）。"""
    if not _HAS_FLOCK:
        _warn_once()
        yield
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + LOCK_SUFFIX)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _warn_once() -> None:
    global _warned_no_lock
    if _warned_no_lock:
        return
    _warned_no_lock = True
    logger.warning("当前平台不支持 fcntl.flock，归档写入无进程间互斥；多进程并发写可能丢数据")
