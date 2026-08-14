"""导入环回归测试。

背景：`app/gateway/base.py` 曾在运行期 `from app.oms.order import LiveOrder`，
而 `app.oms.__init__` 会加载 `app.oms.manager`，后者又 import `app.gateway.base` —— 成环。
全量测试当时并没红，只是因为 pytest 按字母序碰巧先加载了 `app.oms`；
单独跑 `pytest tests/test_oms_manager.py` 就是 ImportError。

这类问题的危险在于「看起来是绿的」：加一个测试文件、重命名一个模块，
都可能改变导入顺序而突然引爆整个测试套件。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# 每个模块都必须能作为**第一个**被导入的模块单独加载
_ENTRY_MODULES = [
    "app.gateway.base",
    "app.oms.manager",
    "app.oms.order",
    "app.engine.controls",
    "app.engine.framework",
    "app.engine.framework.risk",
    "app.engine.framework.execution",
    "app.engine.backtest.broker",
    "app.engine.backtest.portfolio_engine",
    # Wave L-d 实盘接线：engine → live_runner → live_context 必须是单向的
    "app.strategy.engine",
    "app.strategy.live_runner",
    "app.strategy.live_context",
    "app.strategy.paper_sim",
    # V3 Wave A-a 因子策略适配器：strategy → engine.framework 必须是单向的，
    # 且实验记录器 → factor_store 的延迟导入不得反过来把环补上
    "app.strategy.factor_strategy",
    "app.strategy.factor_store",
    "app.quant.experiments.recorder",
    "app.api.v1.endpoints.factor_strategy",
    # Wave M-a 归档/宇宙：data.service → data.archive 与
    # data.pairlist.plugins → data.universe → data.screener 都必须是单向的
    "app.data.archive",
    "app.data.universe",
    "app.data.pairlist",
    "app.data.pairlist.plugins",
    "app.tasks.archive",
    "app.api.v1.endpoints.data_archive",
    # Wave M-b 截面算子：cross_section 被公式引擎与因子库同时引用
    "app.quant.cross_section",
    "app.quant.factor_lib.alpha101",
    # Wave M-c 产物库与模型模板：适配层横跨 app.quant 的三个既有实现，
    # 产物库又被端点与实验记录器引用，最容易在这里补出一条环
    "app.quant.lab.store",
    "app.quant.models.template",
    "app.quant.models.legacy_adapters",
    "app.api.v1.endpoints.lab",
    # Wave O-a 用户策略加载器与 CLI：resolver → presets 必须是单向的，
    # 且 tasks.archive 新增的 notify 依赖不得把环补回来
    "app.strategy.resolver",
    "app.strategy.scaffold",
    "app.cli.main",
    "app.cli.commands.strategies",
    "app.cli.commands.backtest",
    "app.cli.commands.download",
    # V3 Wave B-a LLM 网关：registry → 两个协议适配器 → transport → base 必须单向，
    # service 与 config_store 不得反向引用 registry 之上的任何东西
    "app.core.llm",
    "app.core.llm.registry",
    "app.core.llm.service",
    "app.core.llm.config_store",
    "app.api.v1.endpoints.llm",
    # V3 Wave B-c Copilot：app.copilot 反向依赖了 app.api.v1.endpoints.*
    # （工具处理器刻意复用端点函数），这条边必须保持单向 —— 端点模块不得回头 import app.copilot
    "app.copilot",
    "app.copilot.tools",
    "app.copilot.engine",
    "app.copilot.execute",
    "app.api.v1.endpoints.copilot",
]


@pytest.mark.parametrize("module", _ENTRY_MODULES)
def test_module_imports_standalone(module: str) -> None:
    """在全新解释器里单独导入该模块，不依赖任何导入顺序。"""
    # 必须起子进程：同进程内 sys.modules 已被其他测试填满，测不出真实的首次导入
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{module} 无法独立导入（很可能是循环依赖）：\n{result.stderr}"
    )
