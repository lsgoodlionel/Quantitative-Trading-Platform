"""个股研报提示词（V3 Wave C-a / I4）

提示词把契约 §1.1 的三条硬约束写成模型能执行的指令：

1. **只用快照里的事实** —— 研报的可核对性建立在「模型看到的 = sources 里列出的」，
   一旦允许它调用记忆里的公司知识，`sources` 就成了摆设。
2. **数据缺失时明说，禁止臆测** —— 无新闻时提示词里会多出一条独立的禁令
   （`NO_NEWS_INSTRUCTION`），而不是指望模型自己从空列表推断出「应该说没有」。
3. **不给操作建议** —— 与 A-d 的规则化评级同一立场：这是数据汇总，不是投资建议。
"""

from __future__ import annotations

import json

from app.ai_reports.snapshot import StockSnapshot
from app.core.llm import ChatMessage

#: 报告分节的键与中文标题。顺序即前端展示顺序。
SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("overview", "概览"),
    ("technical", "技术面"),
    ("news", "消息面"),
    ("risks", "风险提示"),
    ("watchpoints", "关注要点"),
)

SECTION_KEYS: tuple[str, ...] = tuple(key for key, _ in SECTION_TITLES)

#: 无新闻时追加的禁令。测试断言它出现在提示词里（契约 §五 验收 2）。
NO_NEWS_INSTRUCTION = (
    "【数据缺失】本期没有抓到任何新闻条目。"
    "「消息面」一节必须原样写明「本期无可用新闻」，"
    "严禁臆测、编造或凭记忆补充任何未在本快照中出现的消息、传闻或事件。"
)

SYSTEM_PROMPT = """你是一名严谨的量化研究助理，负责根据**给定的数据快照**撰写个股观察报告。

必须遵守的规则：
1. 只能使用「数据快照」中出现的事实。严禁引入快照之外的任何信息，
   严禁凭记忆补充公司背景、财务数字、分析师观点或新闻。
2. 任何一类数据缺失时，对应章节必须如实写明「本期无可用数据」，
   严禁基于空数据做推断或臆测。
3. 严禁输出任何操作建议 —— 不得出现「买入 / 卖出 / 加仓 / 减仓 / 目标价 / 止损位 / 建议持有」
   之类的结论。本报告是数据汇总与风险提示，不是投资建议。
4. 引用数字时必须与快照一致，不得四舍五入到失真，也不得自行换算出快照里没有的指标。

输出格式：只输出一个 JSON 对象，不要代码块围栏，不要任何解释性文字。
形如 {"overview": "...", "technical": "...", "news": "...", "risks": "...", "watchpoints": "..."}
五个键必须齐全，每个值是一段 100~300 字的中文，不要嵌套结构。"""


def build_stock_messages(snapshot: StockSnapshot) -> list[ChatMessage]:
    """把快照拼成一次对话。返回的消息列表即测试断言的对象。"""
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_user_prompt(snapshot)),
    ]


def build_user_prompt(snapshot: StockSnapshot) -> str:
    """数据快照 + 本次特有的缺失数据禁令。"""
    blocks = [
        f"## 标的\n{snapshot.symbol}（{snapshot.market}），"
        f"回溯区间 {snapshot.start_date} ~ {snapshot.end_date}，共 {snapshot.bar_count} 根日线。",
        f"## 行情与技术指标\n{_dump(snapshot.technicals)}\n"
        "（值为 null 表示样本长度不足以计算该指标，请勿据此推断方向。）",
        _news_block(snapshot),
        _earnings_block(snapshot),
        _iv_block(snapshot),
    ]
    if snapshot.data_notes:
        blocks.append("## 数据缺口\n" + "\n".join(f"- {note}" for note in snapshot.data_notes))
    if not snapshot.has_news:
        blocks.append(NO_NEWS_INSTRUCTION)
    blocks.append(
        "请据此输出 JSON："
        + "、".join(f"{key}（{title}）" for key, title in SECTION_TITLES)
    )
    return "\n\n".join(blocks)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _news_block(snapshot: StockSnapshot) -> str:
    if not snapshot.news_summaries:
        return "## 近期新闻\n（无）"
    lines = "\n".join(f"{i}. {line}" for i, line in enumerate(snapshot.news_summaries, 1))
    return f"## 近期新闻（共 {len(snapshot.news_summaries)} 条，仅限以下条目）\n{lines}"


def _earnings_block(snapshot: StockSnapshot) -> str:
    if not snapshot.earnings:
        return "## 财报日历\n（无可用数据）"
    return f"## 财报日历\n{_dump(list(snapshot.earnings))}"


def _iv_block(snapshot: StockSnapshot) -> str:
    if snapshot.implied_volatility is None:
        return "## 期权隐含波动率\n（无可用数据）"
    return f"## 期权隐含波动率（近月平值）\n{_dump(snapshot.implied_volatility)}"


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
