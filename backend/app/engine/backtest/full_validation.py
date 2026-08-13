"""
完整验证编排器（V3 · H2）

串行执行 回测 → 参数寻优 → Walk-Forward → 偏差检测 → 蒙特卡洛稳健性 五步，
再交给 `validation_grade` 汇总成规则化评级。

**容错语义（契约红线）**：任何一步抛异常都不会中断整体。该步在结果里退化为
``{"error": "..."}``，其余步骤照常执行；评级模块据此把依赖该步的规则标为
「未评估」，并在 `based_on` 里注明「基于 N/5 步」。

本模块只做编排，不碰 IO/HTTP：五步的实际执行以 `runners`（步骤名 → 无参可调用）
注入，便于单测用假 runner 精确构造成功/失败组合。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.engine.backtest.validation_grade import (
    ALL_STEPS,
    GradeResult,
    grade_validation,
)

logger = get_logger(__name__)

StepRunner = Callable[[], dict]

# 单步错误信息截断长度：避免把整段 traceback 塞进 API 响应
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class FullValidationOutcome:
    """一次完整验证的产物（不可变）。"""

    run_id: str
    requested_steps: list[str]
    steps: dict[str, dict] = field(default_factory=dict)
    grade: GradeResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "requested_steps": self.requested_steps,
            "steps": self.steps,
            "grade": self.grade.to_dict() if self.grade else None,
        }


def resolve_steps(steps: Sequence[str] | None) -> list[str]:
    """
    归一化 steps 参数：None/空 → 全部五步；否则按 ALL_STEPS 顺序保留合法项。

    Raises:
        ValueError: 传入的步骤名全部非法时。
    """
    if not steps:
        return list(ALL_STEPS)
    wanted = {s.strip().lower() for s in steps}
    unknown = wanted - set(ALL_STEPS)
    resolved = [s for s in ALL_STEPS if s in wanted]
    if not resolved:
        raise ValueError(f"无可执行步骤，未知步骤: {sorted(unknown)}；可用: {list(ALL_STEPS)}")
    if unknown:
        logger.warning("Ignoring unknown validation steps", unknown=sorted(unknown))
    return resolved


def _format_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:MAX_ERROR_CHARS]


def run_full_validation(
    runners: Mapping[str, StepRunner],
    steps: Sequence[str] | None = None,
    run_id: str | None = None,
) -> FullValidationOutcome:
    """
    串行执行各验证步骤并汇总评级。

    Args:
        runners: 步骤名 → 无参可调用，返回该步的归一化结果字典。
        steps: 需要执行的步骤子集；None 表示全部五步。
        run_id: 可选的外部 run_id，缺省自动生成。

    Returns:
        FullValidationOutcome —— 每步结果（失败步为 ``{"error": ...}``）+ 综合评级。
    """
    requested = resolve_steps(steps)
    results: dict[str, dict] = {}

    for name in requested:
        runner = runners.get(name)
        if runner is None:
            results[name] = {"error": f"步骤 '{name}' 未提供执行器"}
            continue
        try:
            results[name] = runner()
        except Exception as exc:  # 单步失败不得中断整体
            logger.warning("Full validation step failed", step=name, error=str(exc))
            results[name] = {"error": _format_error(exc)}

    return FullValidationOutcome(
        run_id=run_id or uuid.uuid4().hex[:16],
        requested_steps=requested,
        steps=results,
        grade=grade_validation(results, requested=requested),
    )
