"""
完整验证综合评级（V3 · H2）

把「回测 / 参数寻优 / Walk-Forward / 偏差检测 / 蒙特卡洛稳健性」五步的结果
汇总成一个**规则化**评分。规则全部写在本模块的常量里，可单测、可审计，
不依赖任何模型推理或魔法数字。

设计原则
--------
1. **不过度承诺**：评级只是启发式汇总，不是判决。每条 finding 都写明
   「哪一步的哪个指标、越过了哪条线、扣了多少分」，让用户能自己复核。
   本模块**不产出任何交易/实盘建议**。
2. **缺步必须显式**：某一步失败或未跑时，依赖该步的规则进入
   `not_evaluated`，而不是默默按「通过」处理。`based_on` 会标注
   「基于 N/5 步」，`is_complete` 标明是否覆盖全部五步。

输入约定
--------
`steps` 是 步骤名 → 步骤结果字典。失败的步骤形如 ``{"error": "..."}``，
本模块据此判定该步不可用。各步骤成功时的字段见 `full_validation.py` 的归一化输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 步骤名（与 full_validation 共用） ─────────────────────────────
STEP_BACKTEST = "backtest"
STEP_OPTIMIZE = "optimize"
STEP_WALKFORWARD = "walkforward"
STEP_BIAS = "bias"
STEP_ROBUSTNESS = "robustness"

ALL_STEPS: tuple[str, ...] = (
    STEP_BACKTEST,
    STEP_OPTIMIZE,
    STEP_WALKFORWARD,
    STEP_BIAS,
    STEP_ROBUSTNESS,
)
TOTAL_STEPS = len(ALL_STEPS)

# ── 评分基准与扣分常量 ────────────────────────────────────────────
BASE_SCORE = 100.0

# 规则 1：样本内夏普 ≤ 0（策略在训练区间本身就不赚钱）
PENALTY_NEGATIVE_SHARPE = 10.0
NEGATIVE_SHARPE_THRESHOLD = 0.0

# 规则 2：参数敏感性过高 —— 最优分数远高于中位数，说明只有极少数参数点有效
PENALTY_PARAM_SENSITIVITY = 15.0
PARAM_SENSITIVITY_THRESHOLD = 0.5   # (best - median) / max(|best|, eps) 超过即命中

# 规则 3：样本外夏普 < 样本内的 50%
PENALTY_OOS_SHARPE_DECAY = 25.0
OOS_SHARPE_RETENTION_THRESHOLD = 0.5

# 规则 4：偏差检测有命中（前视 或 递归）
PENALTY_BIAS_DETECTED = 30.0

# 规则 5：蒙特卡洛总收益 5% 分位为负
PENALTY_MC_P5_NEGATIVE = 20.0
MC_P5_RETURN_THRESHOLD = 0.0

# 分数 → 等级（降序匹配第一个满足的档）
GRADE_THRESHOLDS: tuple[tuple[float, str, str], ...] = (
    (85.0, "A", "未触发任何检查项"),
    (70.0, "B", "触发轻度检查项"),
    (55.0, "C", "触发明显检查项"),
    (0.0, "D", "触发严重检查项"),
)

DISCLAIMER = (
    "本评级为规则化启发式汇总，仅反映本次数据与参数下的检查结果，"
    "不构成任何交易或实盘建议。请结合 findings 中的原始指标自行判断。"
)

_EPS = 1e-9


@dataclass(frozen=True)
class Finding:
    """一条命中的扣分项，写清依据便于用户复核。"""

    step: str
    rule: str
    metric: str
    value: float
    threshold: float
    penalty: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "rule": self.rule,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "penalty": self.penalty,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class NotEvaluated:
    """某条规则因所依赖的步骤缺失/失败而未能评估。"""

    step: str
    rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "rule": self.rule, "reason": self.reason}


@dataclass(frozen=True)
class GradeResult:
    """综合评级结果（不可变）。"""

    score: float
    level: str
    level_label: str
    completed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    not_evaluated: list[NotEvaluated] = field(default_factory=list)
    based_on: str = ""
    is_complete: bool = False
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "level_label": self.level_label,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "skipped_steps": self.skipped_steps,
            "findings": [f.to_dict() for f in self.findings],
            "not_evaluated": [n.to_dict() for n in self.not_evaluated],
            "based_on": self.based_on,
            "is_complete": self.is_complete,
            "disclaimer": self.disclaimer,
        }


# ── 单条规则 ──────────────────────────────────────────────────────

def _check_negative_sharpe(payload: dict) -> Finding | None:
    """规则 1：样本内夏普 ≤ 0。"""
    sharpe = float(payload.get("metrics", {}).get("sharpe_ratio", 0.0))
    if sharpe > NEGATIVE_SHARPE_THRESHOLD:
        return None
    return Finding(
        step=STEP_BACKTEST,
        rule="negative_sharpe",
        metric="sharpe_ratio",
        value=round(sharpe, 4),
        threshold=NEGATIVE_SHARPE_THRESHOLD,
        penalty=PENALTY_NEGATIVE_SHARPE,
        detail=f"样本内夏普 {sharpe:.4f} ≤ {NEGATIVE_SHARPE_THRESHOLD}，策略在回测区间本身未取得风险调整后正收益。",
    )


def _check_param_sensitivity(payload: dict) -> Finding | None:
    """规则 2：参数敏感性过高（最优分显著高于中位分）。"""
    dispersion = payload.get("score_dispersion")
    if dispersion is None:
        return None
    dispersion = float(dispersion)
    if dispersion <= PARAM_SENSITIVITY_THRESHOLD:
        return None
    return Finding(
        step=STEP_OPTIMIZE,
        rule="param_sensitivity",
        metric="score_dispersion",
        value=round(dispersion, 4),
        threshold=PARAM_SENSITIVITY_THRESHOLD,
        penalty=PENALTY_PARAM_SENSITIVITY,
        detail=(
            f"最优参数得分较中位数高出 {dispersion:.1%}（阈值 {PARAM_SENSITIVITY_THRESHOLD:.0%}），"
            "说明有效参数区间狭窄，换一组邻近参数结果可能大幅劣化。"
        ),
    )


def _check_oos_decay(payload: dict) -> Finding | None:
    """规则 3：样本外夏普 < 样本内的 50%。"""
    is_sharpe = float(payload.get("avg_is_sharpe", 0.0))
    oos_sharpe = float(payload.get("avg_oos_sharpe", 0.0))
    # 样本内夏普非正时无「衰减」可言，交由规则 1 处理，避免同一问题重复扣分
    if is_sharpe <= _EPS:
        return None
    retention = oos_sharpe / is_sharpe
    if retention >= OOS_SHARPE_RETENTION_THRESHOLD:
        return None
    return Finding(
        step=STEP_WALKFORWARD,
        rule="oos_sharpe_decay",
        metric="oos_is_sharpe_retention",
        value=round(retention, 4),
        threshold=OOS_SHARPE_RETENTION_THRESHOLD,
        penalty=PENALTY_OOS_SHARPE_DECAY,
        detail=(
            f"样本外平均夏普 {oos_sharpe:.4f} 仅为样本内 {is_sharpe:.4f} 的 {retention:.1%}"
            f"（阈值 {OOS_SHARPE_RETENTION_THRESHOLD:.0%}），存在曲线拟合迹象。"
        ),
    )


def _check_bias(payload: dict) -> Finding | None:
    """规则 4：前视 / 递归偏差有命中。"""
    lookahead = bool(payload.get("has_lookahead_bias", False))
    recursive = bool(payload.get("has_recursive_bias", False))
    if not lookahead and not recursive:
        return None
    kinds = [k for k, hit in (("前视偏差", lookahead), ("递归偏差", recursive)) if hit]
    return Finding(
        step=STEP_BIAS,
        rule="bias_detected",
        metric="has_bias",
        value=1.0,
        threshold=0.0,
        penalty=PENALTY_BIAS_DETECTED,
        detail=f"偏差检测命中：{' + '.join(kinds)}。截断重跑后的成交序列与全量回测不一致，回测结果可能不可复现。",
    )


def _check_mc_p5(payload: dict) -> Finding | None:
    """规则 5：蒙特卡洛总收益 5% 分位为负。"""
    p5 = payload.get("p5_total_return_pct")
    if p5 is None:
        return None
    p5 = float(p5)
    if p5 >= MC_P5_RETURN_THRESHOLD:
        return None
    return Finding(
        step=STEP_ROBUSTNESS,
        rule="mc_p5_negative",
        metric="p5_total_return_pct",
        value=round(p5, 4),
        threshold=MC_P5_RETURN_THRESHOLD,
        penalty=PENALTY_MC_P5_NEGATIVE,
        detail=(
            f"逐笔重采样后总收益的 5% 分位为 {p5:.2f}%，"
            "即在 5% 的成交顺序重排情形下该策略录得亏损。"
        ),
    )


# 规则表：步骤 → (规则名, 检查函数)
_RULES: tuple[tuple[str, str, Any], ...] = (
    (STEP_BACKTEST, "negative_sharpe", _check_negative_sharpe),
    (STEP_OPTIMIZE, "param_sensitivity", _check_param_sensitivity),
    (STEP_WALKFORWARD, "oos_sharpe_decay", _check_oos_decay),
    (STEP_BIAS, "bias_detected", _check_bias),
    (STEP_ROBUSTNESS, "mc_p5_negative", _check_mc_p5),
)


# ── 对外入口 ──────────────────────────────────────────────────────

def classify_score(score: float) -> tuple[str, str]:
    """分数 → (等级, 中文说明)。GRADE_THRESHOLDS 的下界为闭区间。"""
    for floor, level, label in GRADE_THRESHOLDS:
        if score >= floor:
            return level, label
    return GRADE_THRESHOLDS[-1][1], GRADE_THRESHOLDS[-1][2]


def _is_ok(payload: Any) -> bool:
    """步骤结果可用 = 是字典且不含 error 字段。"""
    return isinstance(payload, dict) and "error" not in payload


def grade_validation(steps: dict[str, Any], requested: list[str] | None = None) -> GradeResult:
    """
    汇总五步验证结果，产出规则化评级。

    Args:
        steps: 步骤名 → 步骤结果。失败步骤形如 ``{"error": "..."}``。
        requested: 本次请求跑的步骤（默认取 steps 的键）。未请求的步骤计入
            `skipped_steps`，请求了但报错的计入 `failed_steps`。

    Returns:
        GradeResult —— 分数、等级、命中的 findings、未评估的规则、覆盖度说明。
    """
    asked = list(requested) if requested is not None else list(steps.keys())
    completed = [s for s in ALL_STEPS if s in asked and _is_ok(steps.get(s))]
    failed = [s for s in ALL_STEPS if s in asked and s in steps and not _is_ok(steps.get(s))]
    skipped = [s for s in ALL_STEPS if s not in completed and s not in failed]

    findings: list[Finding] = []
    not_evaluated: list[NotEvaluated] = []

    for step, rule_name, check in _RULES:
        if step not in completed:
            reason = "该步骤执行失败" if step in failed else "该步骤本次未运行"
            not_evaluated.append(NotEvaluated(step=step, rule=rule_name, reason=reason))
            continue
        finding = check(steps[step])
        if finding is not None:
            findings.append(finding)

    score = max(0.0, BASE_SCORE - sum(f.penalty for f in findings))
    level, level_label = classify_score(score)

    return GradeResult(
        score=round(score, 2),
        level=level,
        level_label=level_label,
        completed_steps=completed,
        failed_steps=failed,
        skipped_steps=skipped,
        findings=findings,
        not_evaluated=not_evaluated,
        based_on=f"基于 {len(completed)}/{TOTAL_STEPS} 步",
        is_complete=len(completed) == TOTAL_STEPS,
    )
