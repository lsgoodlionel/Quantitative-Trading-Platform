"""Copilot 工具的执行上下文（V3 Wave B-c / I1）

工具处理器不自己去 `Depends()` —— 它们不是 FastAPI 端点。端点把已经注入好的
数据库会话 / Redis 装进这个上下文传下来，处理器缺什么就明确报错，不静默降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ToolExecutionError(Exception):
    """工具执行失败 —— 会被翻译成一句人话回灌给模型，而不是 500。"""


@dataclass(frozen=True)
class CopilotContext:
    """一次请求内所有工具共享的依赖。"""

    session: Any = None   # AsyncSession | None
    redis: Any = None

    def require_session(self) -> Any:
        """取数据库会话；没有就明确报错（这是接线问题，不该悄悄返回空结果）。"""
        if self.session is None:
            raise ToolExecutionError("数据库会话不可用，无法执行该查询。")
        return self.session
