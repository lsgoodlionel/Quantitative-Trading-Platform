"""
QuantBot 命令行工具（Wave O-a / O3）

    python -m app.cli list-strategies
    python -m app.cli backtest     --strategy double_ma --symbol AAPL --start ... --end ...
    python -m app.cli download     --symbols AAPL,MSFT --market US --start ... --end ...
    python -m app.cli new-strategy --name my_strategy

设计约束（写在这里免得后来者忘了）：

1. **只用 stdlib `argparse`** —— 不引入 typer/click。
2. **CLI 是薄壳**：每个子命令都调既有服务层函数（`BacktestEngine`、
   `app.tasks.archive.download_archive`、`app.strategy.resolver`），
   业务逻辑一行都不在这里复制。
3. **优雅失败**：没起 docker 的机器跑 `list-strategies` 不该看到一屏 traceback。
   连不上就给一句人话；要看栈加 `--verbose`。
4. **退出码**：成功 0 / 用户输入错误 2 / 运行失败 1。脚本会依赖这个。

本包刻意不在 `__init__` 里做任何重导入 —— `--help` 不该等 pandas 加载完。
"""

from __future__ import annotations

__all__: list[str] = []
