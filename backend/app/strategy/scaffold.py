"""
用户策略骨架生成（Wave O-a / O3 的 `new-strategy` 子命令）

产出的文件必须能被 `app.strategy.resolver.discover_strategies` 直接加载：
- 显式设置 `name`（resolver 用它做策略键名，没设就会被跳过）
- 具体类（实现 `on_bar`），抽象类会被 resolver 忽略
"""

from __future__ import annotations

import keyword
import re

__all__ = ["INVALID_NAME_HINT", "class_name_for", "is_valid_strategy_name", "render_skeleton"]

#: 策略名同时是「Python 模块名」和「策略键名」，所以只允许 snake_case
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

INVALID_NAME_HINT = "策略名需为 snake_case（小写字母开头，仅含小写字母/数字/下划线），且不能是 Python 关键字"


def is_valid_strategy_name(name: str) -> bool:
    """校验策略名。空、大写、连字符、Python 关键字一律拒绝。"""
    return bool(_NAME_PATTERN.match(name)) and not keyword.iskeyword(name)


def class_name_for(name: str) -> str:
    """`my_edge` → `MyEdgeStrategy`；已以 strategy 结尾则不重复加后缀。"""
    parts = [p for p in name.split("_") if p]
    pascal = "".join(p.capitalize() for p in parts)
    return pascal if pascal.endswith("Strategy") else f"{pascal}Strategy"


def render_skeleton(name: str, description: str = "") -> str:
    """渲染一个可直接跑的双均线骨架，作者在 `on_bar` 里改自己的逻辑。"""
    return _TEMPLATE.format(
        name=name,
        class_name=class_name_for(name),
        description=description or f"{name} 自定义策略",
    )


_TEMPLATE = '''\
"""{name} — 用户自定义策略

⚠️ 本文件位于用户策略目录下，会被**以服务进程的权限执行**（没有沙箱）。
   详见 app/strategy/resolver.py 的模块 docstring。

改完直接生效的方式：CLI 每次都是新进程；服务端需要调用 reload 或重启。
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import crossover, crossunder, sma


class {class_name}(StrategyBase):
    """双均线骨架 —— 把 on_bar 换成你自己的逻辑即可。"""

    #: 策略键名。**必须显式设置**，否则加载器会跳过本文件并记一条 error
    name = "{name}"
    description = "{description}"

    def on_start(self, ctx: StrategyContext) -> None:
        """回测/实盘启动时调用一次，可在此初始化状态变量。"""

    def on_bar(self, ctx: StrategyContext) -> None:
        """每根 K 线调用一次，策略主逻辑写在这里。"""
        fast_period = self.param("fast_period", 10)
        slow_period = self.param("slow_period", 30)

        df = ctx.history
        if len(df) < slow_period + 1:
            return

        fast_ma = sma(df, fast_period)
        slow_ma = sma(df, slow_period)

        if crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)
        elif crossunder(fast_ma, slow_ma).iloc[-1] and ctx.qty > 0:
            ctx.sell_all()

    def on_stop(self, ctx: StrategyContext) -> None:
        """回测/实盘结束时调用一次，可在此收尾。"""
'''
