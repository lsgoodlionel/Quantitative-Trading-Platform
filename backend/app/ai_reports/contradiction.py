"""AI 诊断与规则判据的矛盾检测（V3 Wave C-a / I5）

契约 §2.1 的红线：**模型解读不得与 `grade.findings` 矛盾**。
规则说「参数敏感性过高」而模型说「参数很稳健」，这是明确的 bug，
不能照单全收地端给用户。

检测方式是**词表 + 否定护栏**的启发式，不是语义理解：

- 每条规则配一组「与该规则相反的断言」词表；
- 命中后再看前后文有没有否定词（「并非无过拟合」「样本外表现稳健**性不足**」），
  有就不算矛盾 —— 否则这类否定句会大面积误报。

⚠️ 这是启发式：它会漏报（模型换个说法就绕过），但很少误报。定位是**兜底告警**，
不是准入判定 —— 命中时前端展示「AI 解读与规则判据存在冲突」的红条，
让用户以规则判据为准，而不是把报告丢掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 规则名 → 「与该规则相反」的断言词表
CONTRADICTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "negative_sharpe": (
        "夏普良好", "夏普表现良好", "夏普为正", "风险调整后收益良好", "盈利能力良好",
    ),
    "param_sensitivity": (
        "参数稳健", "参数很稳健", "参数不敏感", "对参数不敏感", "参数敏感性低",
        "参数空间平坦", "参数区间宽",
    ),
    "oos_sharpe_decay": (
        "无过拟合", "不存在过拟合", "未发现过拟合", "样本外表现稳健",
        "样本外无衰减", "泛化能力良好",
    ),
    "bias_detected": (
        "无偏差", "无前视偏差", "未发现偏差", "不存在偏差", "不存在前视偏差",
        "无未来函数",
    ),
    "mc_p5_negative": (
        "稳健性良好", "稳健性优秀", "下行风险可控", "极端情形下仍能盈利",
    ),
}

#: 有任何 finding 命中时，这些「整体没问题」的说法本身就是矛盾
OVERALL_PATTERNS: tuple[str, ...] = (
    "未触发任何检查项", "未发现任何问题", "没有发现任何问题",
    "全部检查项均通过", "各项检查均正常",
)

#: 整体性矛盾在结果里的规则名
OVERALL_RULE = "__overall__"

#: 命中词后紧跟这些字样时视为否定，不计为矛盾
_TRAILING_NEGATORS: tuple[str, ...] = (
    "性不足", "不足", "不佳", "欠佳", "存疑", "堪忧", "有限", "不够", "的迹象存在",
)

#: 命中词前出现这些字样时视为否定，不计为矛盾
_LEADING_NEGATORS: tuple[str, ...] = (
    "并非", "并不是", "不是", "未必", "难言", "谈不上", "算不上", "不能说", "不可视为",
)

#: 前向否定的回看窗口（中文否定词最长 3 字，取 4 留一格余量）
_LOOKBEHIND = 4
#: 后向否定的前瞻窗口
_LOOKAHEAD = 5


@dataclass(frozen=True)
class Contradiction:
    """一处「AI 解读」与「规则判据」冲突。"""

    rule: str
    claim: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "claim": self.claim, "detail": self.detail}


def detect_contradictions(
    text: str, findings: list[dict[str, Any]]
) -> tuple[Contradiction, ...]:
    """在 AI 诊断全文里找出与已触发规则相冲突的断言。

    Args:
        text: 诊断的全部文字（摘要 + 各条 finding + next_steps 拼接后）。
        findings: `grade.findings` —— 规则化评级实际触发的扣分项。
    """
    if not findings or not text:
        return ()

    hits: list[Contradiction] = []
    for finding in findings:
        rule = str(finding.get("rule", ""))
        for phrase in CONTRADICTION_PATTERNS.get(rule, ()):
            if _asserts(text, phrase):
                hits.append(
                    Contradiction(
                        rule=rule,
                        claim=phrase,
                        detail=(
                            f"规则判据已触发「{rule}」：{finding.get('detail', '')}"
                            f" 但 AI 诊断中出现「{phrase}」的表述，两者矛盾，请以规则判据为准。"
                        ),
                    )
                )
                break  # 同一条规则报一次就够，不刷屏

    for phrase in OVERALL_PATTERNS:
        if _asserts(text, phrase):
            hits.append(
                Contradiction(
                    rule=OVERALL_RULE,
                    claim=phrase,
                    detail=(
                        f"规则判据共触发 {len(findings)} 条扣分项，"
                        f"但 AI 诊断中出现「{phrase}」的表述，两者矛盾，请以规则判据为准。"
                    ),
                )
            )
            break

    return tuple(hits)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _asserts(text: str, phrase: str) -> bool:
    """文中是否**肯定地**出现该断言（排除被否定掉的那些）。"""
    start = text.find(phrase)
    while start != -1:
        if not _is_negated(text, start, len(phrase)):
            return True
        start = text.find(phrase, start + 1)
    return False


def _is_negated(text: str, start: int, length: int) -> bool:
    before = text[max(0, start - _LOOKBEHIND) : start]
    after = text[start + length : start + length + _LOOKAHEAD]
    if any(before.endswith(neg) for neg in _LEADING_NEGATORS):
        return True
    return any(after.startswith(neg) for neg in _TRAILING_NEGATORS)
