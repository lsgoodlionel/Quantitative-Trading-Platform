"""
`download` 子命令（Wave O-a / O3）

薄壳：直接复用 M-a 的归档任务 `app.tasks.archive.download_archive`。
用 `.apply()` 在**本进程内同步执行**，因此不需要 Celery broker —— CLI 的用户
想要的是「现在就把数据下下来」，而不是把任务丢进一个可能没起的队列里。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from app.cli.errors import EXIT_OK, EXIT_RUNTIME_ERROR, CliUsageError

__all__ = ["run_download"]

#: 与归档任务保持一致的单次上限
_MAX_SYMBOLS = 200

#: 失败明细最多回显这么多条，其余只给计数
_MAX_LISTED_FAILURES = 20


def run_download(args: argparse.Namespace) -> int:
    """下载并归档历史 bar。有任一标的失败即退出码 1（脚本据此决定是否重跑）。"""
    from app.tasks.archive import download_archive

    symbols = _parse_symbols(args.symbols)
    _validate_market(args.market)
    start, end = _parse_window(args.start, args.end)

    result = download_archive.apply(kwargs={
        "symbols": symbols,
        "market": args.market.upper(),
        "frequency": args.frequency,
        "start": start,
        "end": end,
    }).result

    if isinstance(result, BaseException):
        raise result
    return _report(result)


def _report(result: dict) -> int:
    print(
        f"市场 {result['market']} · 周期 {result['frequency']} · "
        f"请求 {result['requested']} 个标的"
    )
    print(f"已归档 {result['archived']} 个标的，写入 {result['written']} 根 bar")

    failed = list(result.get("failed") or [])
    if not failed:
        return EXIT_OK

    print(f"失败 {result['errors']} 个标的:", file=sys.stderr)
    for item in failed[:_MAX_LISTED_FAILURES]:
        print(f"  - {item}", file=sys.stderr)
    return EXIT_RUNTIME_ERROR


# ── 入参解析 ─────────────────────────────────────────────────

def _parse_symbols(raw: str) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise CliUsageError("--symbols 至少要给一个标的代码，如 AAPL,MSFT")
    if len(symbols) > _MAX_SYMBOLS:
        raise CliUsageError(f"--symbols 一次最多 {_MAX_SYMBOLS} 个，当前 {len(symbols)} 个")
    return symbols


def _validate_market(market: str) -> None:
    from app.data.models import Market

    try:
        Market(market.upper())
    except ValueError as exc:
        raise CliUsageError(
            f"未知市场 '{market}'，可选: {', '.join(m.value for m in Market)}"
        ) from exc


def _parse_window(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """只做格式与先后校验；留空交给归档任务用它自己的默认区间。"""
    parsed = {}
    for label, value in (("--start", start), ("--end", end)):
        if value is None:
            parsed[label] = None
            continue
        try:
            parsed[label] = date.fromisoformat(value)
        except ValueError as exc:
            raise CliUsageError(f"{label} 格式应为 YYYY-MM-DD: {exc}") from exc

    begin, finish = parsed["--start"], parsed["--end"]
    if begin and finish and finish < begin:
        raise CliUsageError(f"--end {finish} 早于 --start {begin}")
    return (begin.isoformat() if begin else None, finish.isoformat() if finish else None)
