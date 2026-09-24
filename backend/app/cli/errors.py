"""
CLI 退出码与错误类型（Wave O-a / O3）

退出码语义对脚本调用方是契约的一部分，别随手改：

    0  成功
    1  运行失败（取数失败、DB/Redis 连不上、任务报错……）
    2  用户输入错误（未知策略名、日期区间反了、JSON 参数写坏了……）

2 与 argparse 自身的用法错误退出码一致，所以「参数写错」在两条路径上表现相同。
"""

from __future__ import annotations

__all__ = [
    "EXIT_OK",
    "EXIT_RUNTIME_ERROR",
    "EXIT_USAGE_ERROR",
    "CliError",
    "CliUsageError",
]

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2


class CliError(Exception):
    """运行失败 → 退出码 1。消息会原样打到 stderr，所以要写成人话。"""

    exit_code = EXIT_RUNTIME_ERROR


class CliUsageError(CliError):
    """用户输入错误 → 退出码 2。"""

    exit_code = EXIT_USAGE_ERROR
