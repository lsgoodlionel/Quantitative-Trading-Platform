"""回测 AI 诊断提示词（V3 Wave C-a / I5）

契约 §2.1 的两条 ⚠️ 都落在这里：

1. **`grade.findings` 原样进提示词**。不做摘要、不做改写 —— 每条 finding 都写明
   「哪一步的哪个指标越过哪条线扣了多少分」，这正是模型该翻译的原文。
   一旦这里先压缩一遍，模型就只能解读「我的转述」而不是机器判据。
2. **`based_on` 覆盖度必须带上**。三步的结果不能给一个听起来覆盖五步的结论，
   所以覆盖度既写进提示词，也在响应侧由 `diagnosis.py` 兜底补进结论。

模型在这里的职责被刻意限制成「翻译 + 给下一步」，不是「重新评一遍」：
规则评级是可审计的，模型的再评判不是。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.llm import ChatMessage

#: 回灌给模型的单步原始结果字符数上限 —— 步骤 payload 可能很大（净值曲线等）
MAX_STEP_CHARS = 1200

SYSTEM_PROMPT = """你是一名量化回测审阅助理。你的任务是把**规则化验证评级**的机器判据
翻译成研究员能读懂的诊断与下一步动作。

必须遵守的规则：
1. findings 是规则引擎判定的**事实**，不是待商榷的意见。你的解读**不得与之矛盾**：
   已触发的检查项，绝不能被描述为「正常 / 稳健 / 无问题 / 未触发」。
2. 你不负责重新评级，也不得给出与 level/score 不同的自己的评级。
3. 结论必须如实带上覆盖度（based_on，形如「基于 N/5 步」）。
   未运行或失败的步骤所对应的问题，只能说「未检验」，不得说「没有问题」。
4. 严禁输出任何交易或实盘建议（买入/卖出/加仓/上实盘/可以实盘），
   下一步只能是**研究动作**（补跑某一步、缩小参数空间、更换样本区间等）。

输出格式：只输出一个 JSON 对象，不要代码块围栏，不要任何解释性文字。
形如 {"summary": "...", "findings": [{"title": "...", "detail": "...", "severity": "high|medium|low",
"rule": "对应的规则名，没有就填 null"}], "next_steps": ["...", "..."]}
summary 是 100~250 字的中文总述；findings 逐条对应上面的机器判据；next_steps 给 2~5 条具体动作。"""


def build_diagnosis_messages(grade: dict[str, Any], steps: dict[str, Any]) -> list[ChatMessage]:
    """把评级与步骤结果拼成一次对话。"""
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_user_prompt(grade, steps)),
    ]


def build_user_prompt(grade: dict[str, Any], steps: dict[str, Any]) -> str:
    findings = grade.get("findings") or []
    blocks = [
        "## 综合评级（规则化，不可推翻）\n"
        + _dump(
            {
                "level": grade.get("level"),
                "level_label": grade.get("level_label"),
                "score": grade.get("score"),
                "based_on": grade.get("based_on"),
                "is_complete": grade.get("is_complete"),
                "completed_steps": grade.get("completed_steps"),
                "failed_steps": grade.get("failed_steps"),
                "skipped_steps": grade.get("skipped_steps"),
            }
        ),
        # ⚠️ 原样给出，不做任何摘要 —— 契约 §2.1
        f"## 已触发的规则判据 findings（共 {len(findings)} 条，原样给出）\n{_dump(findings)}",
        "## 未评估的规则 not_evaluated（对应步骤缺失/失败，只能说「未检验」）\n"
        + _dump(grade.get("not_evaluated") or []),
        f"## 覆盖度\n{grade.get('based_on') or '未知'}"
        f"（is_complete={grade.get('is_complete')}）。结论中必须如实带上这一覆盖度。",
        _steps_block(steps),
        "请据此输出 JSON：summary（总述）、findings（逐条解读）、next_steps（下一步研究动作）。",
    ]
    return "\n\n".join(blocks)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _steps_block(steps: dict[str, Any]) -> str:
    if not steps:
        return "## 各步原始结果\n（本次未提供，请仅依据上面的评级与判据作答）"
    rendered = "\n".join(f"### {name}\n{_clip(_dump(payload))}" for name, payload in steps.items())
    return f"## 各步原始结果\n{rendered}"


def _clip(text: str) -> str:
    if len(text) <= MAX_STEP_CHARS:
        return text
    return f"{text[:MAX_STEP_CHARS]}…（过长已截断）"


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
