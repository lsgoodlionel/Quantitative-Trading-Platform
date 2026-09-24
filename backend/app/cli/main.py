"""
CLI 入口：参数解析 + 分发 + 统一错误处理（Wave O-a / O3）

子命令实现放在 `app.cli.commands.*`，且**在分发时才导入** —— `--help` 与
`new-strategy` 不该为了 pandas / SQLAlchemy 的导入耗时买单。
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from app.cli.errors import EXIT_OK, EXIT_RUNTIME_ERROR, EXIT_USAGE_ERROR, CliError

__all__ = ["build_parser", "main"]

_PROG = "python -m app.cli"

#: 子命令名 → `app.cli.commands` 下的模块名。值只在分发时才被导入
_HANDLERS = {
    "list-strategies": "strategies",
    "new-strategy": "strategies",
    "backtest": "backtest",
    "download": "download",
}


def build_parser() -> argparse.ArgumentParser:
    """构建总解析器。每个子命令的参数定义各自成函数，避免这里长成一坨。"""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="QuantBot 命令行工具：策略清单 / 回测 / 数据下载 / 策略骨架",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="出错时打印完整 traceback（默认只给一句人话）",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    _add_list_strategies(sub)
    _add_backtest(sub)
    _add_download(sub)
    _add_new_strategy(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析并执行；返回退出码而不是直接 `sys.exit`，方便测试与嵌入调用。"""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 的 --help / 用法错误
        return int(exc.code or EXIT_OK)

    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    """执行子命令，把任何异常翻译成「一句人话 + 退出码」。"""
    from importlib import import_module

    module = import_module(f"app.cli.commands.{_HANDLERS[args.command]}")
    handler = getattr(module, args.func_name)
    try:
        return handler(args)
    except CliError as exc:
        return _fail(str(exc), exc.exit_code, args.verbose)
    except KeyboardInterrupt:
        return _fail("已中断", EXIT_RUNTIME_ERROR, verbose=False)
    except Exception as exc:  # noqa: BLE001 — CLI 边界，绝不把栈甩给用户
        return _fail(f"{_describe(exc)}（加 --verbose 看完整栈）", EXIT_RUNTIME_ERROR, args.verbose)


def _fail(message: str, code: int, verbose: bool) -> int:
    print(f"错误: {message}", file=sys.stderr)
    if verbose:
        traceback.print_exc()
    return code


def _describe(exc: BaseException) -> str:
    """把底层异常压成一行。空消息的异常（如 ConnectionRefusedError）也要有话说。"""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ── 子命令定义 ────────────────────────────────────────────────

def _add_list_strategies(sub) -> None:
    p = sub.add_parser("list-strategies", help="列出全部可用策略（preset + 用户策略）")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    p.set_defaults(func_name="run_list_strategies")


def _add_backtest(sub) -> None:
    p = sub.add_parser("backtest", help="跑一次单标的回测")
    p.add_argument("--strategy", required=True, help="策略名，见 list-strategies")
    p.add_argument("--symbol", required=True, help="标的代码，如 AAPL")
    p.add_argument("--start", required=True, help="起始日 YYYY-MM-DD（含）")
    p.add_argument("--end", required=True, help="结束日 YYYY-MM-DD（含）")
    p.add_argument("--market", default="US", help="US / HK / A（默认 US）")
    p.add_argument("--frequency", default="1d", help="K 线周期（默认 1d）")
    p.add_argument("--cash", type=float, default=100_000.0, help="初始资金（默认 100000）")
    p.add_argument("--params", default=None, help="策略参数 JSON，如 '{\"fast_period\": 5}'")
    p.add_argument(
        "--use-cache", action="store_true",
        help="允许读写 TimescaleDB 缓存（默认关闭，这样没起 DB 也能跑）",
    )
    p.add_argument("--json", action="store_true", help="输出 JSON 而非摘要")
    p.set_defaults(func_name="run_backtest")


def _add_download(sub) -> None:
    p = sub.add_parser("download", help="下载历史 bar 到本地归档")
    p.add_argument("--symbols", required=True, help="逗号分隔的标的代码，如 AAPL,MSFT")
    p.add_argument("--market", default="US", help="US / HK / A（默认 US）")
    p.add_argument("--frequency", default="1d", help="K 线周期（默认 1d）")
    p.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（默认一年前）")
    p.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（默认今天）")
    p.set_defaults(func_name="run_download")


def _add_new_strategy(sub) -> None:
    p = sub.add_parser("new-strategy", help="在用户策略目录生成一个策略骨架")
    p.add_argument("--name", required=True, help="策略名，snake_case，如 my_strategy")
    p.add_argument("--description", default="", help="策略描述")
    p.add_argument(
        "--dir", dest="directory", default=None,
        help="输出目录（默认 settings.user_strategies_dir）",
    )
    p.add_argument("--force", action="store_true", help="覆盖已存在的同名文件")
    p.set_defaults(func_name="run_new_strategy")
