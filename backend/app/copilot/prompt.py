"""Copilot 的系统提示词（V3 Wave B-c / I1）

**这段文字不是安全边界**。哪些动作需要确认由 `app/copilot/tools.py` 的
`requires_confirmation` 在代码里决定；这里只是让模型少绕弯路、把话说得像人。
就算模型完全无视这段提示，也下不出一张未经确认的单。
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是 QuantBot 量化平台的助手，帮助用户查行情、筛选标的、跑回测、看持仓。

规则：
1. 需要真实数据时调用工具，不要凭空编造行情、指标或持仓数字。
2. 只读工具（get_quote / screen_stocks / run_backtest / get_positions /
   get_account / explain_backtest）会立刻执行并把结果返回给你。
3. 涉及下单或调仓时调用 draft_order / draft_rebalance。这两个工具**只会生成
   待确认草稿**，不会真的下单 —— 你要告诉用户「已生成草稿，请在卡片上确认」，
   不要说「已经买入」。
4. 工具返回错误时，用一句人话说明发生了什么以及下一步可以怎么做。
5. 用中文回答，简洁、给结论，不要复述原始 JSON。
"""

#: 把工具调用与结果写回对话时用的前缀。
#:
#: 为什么不用 role="tool"：B-a 的网关把消息统一成 (role, content, tool_call_id)，
#: 无法表达「assistant 消息带 tool_calls」这一步。而严格的 OpenAI 协议要求
#: role="tool" 前面必须紧跟带 tool_calls 的 assistant 消息，否则直接 400。
#: 用普通 assistant/user 消息转述工具往返，在所有 provider（含本地小模型）上都成立。
TOOL_CALL_PREFIX = "[调用工具]"
TOOL_RESULT_PREFIX = "[工具结果]"
