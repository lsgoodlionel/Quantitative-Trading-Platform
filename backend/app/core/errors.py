"""跨层共享的异常类型。

**本模块不得 import 任何 `app.*` 模块** —— 它被引擎层与策略层同时引用，
一旦引入依赖就会成环（`app.strategy.__init__` → `context` → `broker` 那条路径）。
`tests/test_import_cycles.py` 会守住这一点。
"""

from __future__ import annotations


class StrategyContractError(Exception):
    """策略违反了基类约定 —— 例如钩子返回了非法值。

    与「策略运行时出错」（除零、索引越界、某根 bar 数据异常）**性质不同**，
    因此引擎对两者的处理也不同：

    | | 引擎处理 |
    |---|---|
    | 运行时错误 | `logger.exception` 后跳过本时点，回测继续 —— 单根 bar 的意外不该毁掉整轮 |
    | **契约违规** | **重新抛出，让回测立刻失败** |

    理由：契约违规是**确定性**的，每根 bar 都会重复，不改代码永远不会好。
    吞掉它的后果是一份「跑完了、报告正常、但钩子从未生效」的假结果 ——
    比直接失败危险得多。这与 `NotImplementedError`（装配错误）走同一条特例路径。
    """
