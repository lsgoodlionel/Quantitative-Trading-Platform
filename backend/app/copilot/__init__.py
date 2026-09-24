"""平台 Copilot（V3 Wave B-c / I1）—— 问答 + 工具调用

核心约束（契约 §零）：**凡是会产生订单或改变实盘配置的动作，一律生成
「待确认草稿」，绝不直接执行**。这条边界由 `tools.CopilotTool.requires_confirmation`
在代码里保证（默认 True），不是靠系统提示词请模型「记得」。

模块分工：

- `args`          工具参数模型（尽量直接复用既有端点的 Pydantic 模型）
- `schema_export` Pydantic → 自包含 JSON Schema
- `handlers`      只读工具的处理器（调用既有端点函数，不复制查询逻辑）
- `drafts`        写动作的待确认草稿
- `tools`         工具注册表 + 动作边界
- `engine`        对话编排（轮次上限 / 非法参数 / 结果回灌）
- `execute`       草稿确认后的执行（走既有端点）
"""

from app.copilot.context import CopilotContext, ToolExecutionError
from app.copilot.drafts import CopilotDraft, DraftField
from app.copilot.engine import (
    MAX_TOOL_ROUNDS,
    CopilotCard,
    CopilotOutcome,
    run_copilot_chat,
)
from app.copilot.execute import DraftExecutionError, execute_draft
from app.copilot.tools import COPILOT_TOOLS, CopilotTool, get_tool, tool_specs

__all__ = [
    "COPILOT_TOOLS",
    "MAX_TOOL_ROUNDS",
    "CopilotCard",
    "CopilotContext",
    "CopilotDraft",
    "CopilotOutcome",
    "CopilotTool",
    "DraftExecutionError",
    "DraftField",
    "ToolExecutionError",
    "execute_draft",
    "get_tool",
    "run_copilot_chat",
    "tool_specs",
]
