"""回测 AI 诊断编排（V3 Wave C-a / I5）

规则评级 → 提示词 → 模型 → 结构化诊断 + 矛盾告警。

两处**不依赖模型自觉**的兜底：

- **覆盖度**：`based_on` 由本模块强制补进 summary。契约 §2.1 要求诊断如实带上
  「基于 N/5 步」，而这句话太容易被模型省略 —— 靠提示词是概率，靠这里是确定。
- **矛盾**：模型说的与规则判据冲突时打 `has_contradiction`，而不是把冲突的文字
  原样端出去。规则判据可审计，模型的复述不可。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ai_reports.common import DISCLAIMER, clean_lines, clean_text, request_json
from app.ai_reports.contradiction import Contradiction, detect_contradictions
from app.ai_reports.diagnosis_prompt import build_diagnosis_messages
from app.core.llm import LLMProvider

#: 诊断条目数量上限
MAX_FINDINGS = 12
#: 下一步动作数量上限
MAX_NEXT_STEPS = 8
#: 合法的严重度取值；模型给别的一律降级为 medium
SEVERITIES: frozenset[str] = frozenset({"high", "medium", "low"})
DEFAULT_SEVERITY = "medium"

_EMPTY_SUMMARY = "模型未输出总述。"


@dataclass(frozen=True)
class DiagnosisFinding:
    """AI 对一条机器判据的解读。"""

    title: str
    detail: str
    severity: str
    rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "severity": self.severity,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class BacktestDiagnosis:
    """一次回测诊断的全部产出。"""

    run_id: str | None
    grade_level: str
    grade_score: float
    coverage: str
    is_complete: bool
    summary: str
    findings: list[dict[str, Any]]
    next_steps: list[str]
    contradictions: list[dict[str, str]]
    has_contradiction: bool
    generated_at: str
    model: str
    disclaimer: str = DISCLAIMER


async def generate_backtest_diagnosis(
    provider: LLMProvider,
    grade: dict[str, Any],
    steps: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> BacktestDiagnosis:
    """生成一次诊断。模型连不上抛 `LLMUnavailableError`，吐不出结构抛 `AIReportError`。"""
    steps = steps or {}
    payload, model_name = await request_json(provider, build_diagnosis_messages(grade, steps))

    rule_findings = list(grade.get("findings") or [])
    based_on = clean_text(grade.get("based_on"))
    summary = ensure_coverage(clean_text(payload.get("summary"), fallback=_EMPTY_SUMMARY), based_on)
    findings = _parse_findings(payload.get("findings"))
    next_steps = clean_lines(payload.get("next_steps"), limit=MAX_NEXT_STEPS)

    conflicts = detect_contradictions(
        _all_text(summary, findings, next_steps), rule_findings
    )

    return BacktestDiagnosis(
        run_id=run_id,
        grade_level=clean_text(grade.get("level")),
        grade_score=float(grade.get("score") or 0.0),
        coverage=based_on,
        is_complete=bool(grade.get("is_complete")),
        summary=summary,
        findings=[f.to_dict() for f in findings],
        next_steps=next_steps,
        contradictions=[c.to_dict() for c in conflicts],
        has_contradiction=bool(conflicts),
        generated_at=datetime.now(tz=UTC).isoformat(),
        model=model_name,
    )


def ensure_coverage(summary: str, based_on: str) -> str:
    """覆盖度没被模型带上就补在句首。已经带了就原样返回，不重复啰嗦。"""
    if not based_on or based_on in summary:
        return summary
    return f"（{based_on}）{summary}"


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _parse_findings(raw: Any) -> list[DiagnosisFinding]:
    if not isinstance(raw, list):
        return []
    parsed = [_one_finding(item) for item in raw[:MAX_FINDINGS]]
    return [f for f in parsed if f is not None]


def _one_finding(item: Any) -> DiagnosisFinding | None:
    if isinstance(item, str):
        text = clean_text(item)
        return DiagnosisFinding(title=text, detail=text, severity=DEFAULT_SEVERITY) if text else None
    if not isinstance(item, dict):
        return None

    detail = clean_text(item.get("detail"))
    title = clean_text(item.get("title"), fallback=detail)
    if not title and not detail:
        return None

    severity = clean_text(item.get("severity")).lower()
    rule = clean_text(item.get("rule"))
    return DiagnosisFinding(
        title=title,
        detail=detail or title,
        severity=severity if severity in SEVERITIES else DEFAULT_SEVERITY,
        rule=rule or None,
    )


def _all_text(summary: str, findings: list[DiagnosisFinding], next_steps: list[str]) -> str:
    """矛盾检测要覆盖诊断的每一处文字，不能只看 summary。"""
    parts = [summary, *next_steps]
    for finding in findings:
        parts.extend([finding.title, finding.detail])
    return "\n".join(parts)


__all__ = [
    "BacktestDiagnosis",
    "Contradiction",
    "DiagnosisFinding",
    "ensure_coverage",
    "generate_backtest_diagnosis",
]
