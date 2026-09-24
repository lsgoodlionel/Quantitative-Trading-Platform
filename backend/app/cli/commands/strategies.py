"""
`list-strategies` 与 `new-strategy`（Wave O-a / O3）

两个子命令都**不碰 DB/Redis** —— 用户在没起 docker 的机器上想看看有哪些策略，
或者想生成一个骨架，不该被基础设施挡住。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.cli.errors import EXIT_OK, CliError, CliUsageError

__all__ = ["run_list_strategies", "run_new_strategy"]


def run_list_strategies(args: argparse.Namespace) -> int:
    """列出 preset + 用户策略；加载失败的文件在 stderr 单独列出。"""
    from app.strategy.presets import STRATEGY_REGISTRY
    from app.strategy.resolver import (
        StrategyNameConflictError,
        available_strategies,
        user_strategy_errors,
    )

    try:
        registry = available_strategies()
        errors = user_strategy_errors()
    except StrategyNameConflictError as exc:
        raise CliError(str(exc)) from exc

    rows = [
        {
            "name": name,
            "description": getattr(cls, "description", "") or "",
            "source": "preset" if name in STRATEGY_REGISTRY else "user",
        }
        for name, cls in sorted(registry.items())
    ]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        _print_table(rows)

    for err in errors:
        print(f"警告: 策略文件加载失败 {err.path} — {err.reason}", file=sys.stderr)
    return EXIT_OK


def _print_table(rows: list[dict]) -> None:
    width = max((len(r["name"]) for r in rows), default=4)
    print(f"{'NAME'.ljust(width)}  SOURCE  DESCRIPTION")
    for row in rows:
        print(f"{row['name'].ljust(width)}  {row['source']:<6}  {row['description']}")
    print(f"\n共 {len(rows)} 个策略")


def run_new_strategy(args: argparse.Namespace) -> int:
    """生成一个能被 `discover_strategies` 直接加载的策略骨架文件。"""
    from app.strategy.presets import STRATEGY_REGISTRY
    from app.strategy.scaffold import INVALID_NAME_HINT, is_valid_strategy_name, render_skeleton

    name = args.name
    if not is_valid_strategy_name(name):
        raise CliUsageError(f"策略名 '{name}' 非法：{INVALID_NAME_HINT}")
    if name in STRATEGY_REGISTRY:
        raise CliUsageError(
            f"策略名 '{name}' 与内置 preset 重名。用户策略不会覆盖 preset，请换个名字。"
        )

    target_dir = Path(args.directory) if args.directory else _default_dir()
    target = target_dir / f"{name}.py"
    if target.exists() and not args.force:
        raise CliError(f"{target} 已存在。确认要覆盖请加 --force。")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(render_skeleton(name, args.description), encoding="utf-8")
    except OSError as exc:
        raise CliError(f"写入 {target} 失败: {exc}") from exc

    print(f"已生成策略骨架: {target}")
    print("提示: 该目录下的文件会被以服务进程权限执行，只放你自己写的或已审阅的策略。")
    return EXIT_OK


def _default_dir() -> Path:
    from app.core.config import settings

    return Path(settings.user_strategies_dir)
