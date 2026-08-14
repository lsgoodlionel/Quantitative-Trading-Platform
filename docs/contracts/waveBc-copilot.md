# V3 Wave B-c 契约：平台 Copilot（I1）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **I1** · Agent-Bc
> 依赖：**B-a LLM 网关已合入**（`app/core/llm/`，两个协议适配器 + 6 家预设）
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 零、动作边界（已确认，这是本契约的核心约束）

> **凡是会产生订单或改变实盘配置的动作，一律生成「待确认草稿」，绝不直接执行。
> 其余只读动作可直接执行。**

### 0.1 必须确认（写动作）

| 动作 | 端点 |
|---|---|
| 下单 / 撤单 | `orders` |
| 再平衡执行 | `rebalance/execute` |
| 启停实盘策略 | `live_strategy` |
| 改券商配置 | `broker_config` |
| 算法单（TWAP/VWAP/冰山） | `order_algos` |

### 0.2 可直接执行（只读）

行情 / 筛选 / 回测 / 因子 / 持仓查询 / 指标解读 —— 这些**不改变任何状态**，
跑错了最多浪费一次计算。

### 0.3 实现上怎么保证

**不要靠 prompt 让模型「记得」哪些要确认。** 模型会忘、会被诱导、会幻觉出
一个不存在的只读工具名。边界必须由**代码**保证：

```python
@dataclass(frozen=True)
class CopilotTool:
    spec: ToolSpec
    handler: Callable[..., Awaitable[dict]]
    #: True = 执行前必须由用户确认。**这个标志是白名单式的**：
    #: 新增工具默认 requires_confirmation=True，要显式声明只读才放行。
    requires_confirmation: bool = True
```

⚠️ **默认值必须是 `True`**。默认 False 意味着「有人加了个新工具但忘了标注」
会直接变成「Copilot 可以未经确认下单」。默认 True 的失败模式只是多点一次确认。

⚠️ **草稿不是把参数丢给前端就完事**。草稿要包含：解析后的完整参数、
人类可读的动作描述、以及**执行时用的幂等标识**。用户点确认后走**既有端点**
（`rebalance/execute` 已有 `confirm_token` 机制，下单走 `OrderManager.submit_order`），
不要为 Copilot 另开一条绕过风控的执行通路。

---

## 一、工具集（本期范围）

先做**只读**的一批 + **两个**写动作草稿，不要一次把 20 个端点都包成工具：

**只读（直接执行）**
- `get_quote(symbol, market)` — 最新行情
- `screen_stocks(criteria)` — 筛选
- `run_backtest(strategy, symbol, start, end)` — 单标的回测
- `get_positions()` / `get_account()` — 持仓与账户
- `explain_backtest(backtest_id)` — 解读已有回测结果

**写动作（生成草稿）**
- `draft_order(symbol, side, qty, order_type, limit_price?)`
- `draft_rebalance(target_weights)`

⚠️ **工具的参数 schema 要从既有 Pydantic 模型导出**，不要手写一份 JSON Schema ——
手写的那份会和端点的真实校验漂移，模型按它生成的参数会在调用时 422。

---

## 二、对话与执行流

```
用户提问
  → /api/v1/copilot/chat（带会话历史）
  → LLM 网关 chat(tools=...)
  → 有 tool_calls?
      ├ 只读工具  → 直接执行 → 结果回灌模型 → 生成自然语言回复
      └ 写动作工具 → **不执行**，产出 draft 卡片，回复里说明「需要你确认」
  → 结构化回复：{ text, cards: [...], drafts: [...] }
```

### 2.1 三个必须处理的现实

1. **本地小模型会吐出非法 JSON 的 tool arguments**。B-a 的网关已经把原文
   保留在 `{"__raw__": ...}` 里而非静默丢弃 —— Copilot 遇到这种要**回一句
   人话的错误**（「模型返回的参数无法解析，请换个说法或换个模型」），
   而不是抛 500 或假装调用成功。
2. **工具调用可能连环**（查行情 → 再回测）。要有**轮次上限**（建议 5），
   超了就停下并如实说明，不要无限循环烧 token。
3. **未配置 provider 时** 网关抛 501。Copilot 要把它翻译成引导语
   + 指向 `/settings/models` 的链接，而不是把 501 直接甩给用户。

---

## 三、前端

对话侧栏（可从任意页面唤起）：
- 消息流 + 结构化卡片（行情卡 / 回测结果卡 / 持仓卡）
- **草稿卡片**：展示解析后的参数 + 「确认执行」/「取消」两个按钮
- 未配置模型时：引导块 + 「去配置」按钮直达 `/settings/models`

⚠️ **草稿卡片必须显示完整参数**（标的、方向、数量、价格、预估金额）。
一个只写着「买入 AAPL」而不写数量的确认按钮，等于没有确认。

⚠️ 不要编辑 `App.tsx` / `Sidebar.tsx` / `routes.tsx` / `types/index.ts`，
在报告里给集成片段。

---

## 四、验收

```
1. tests/regression 146 用例全绿
2. tests/test_copilot_tools.py:
   - **新增工具默认 requires_confirmation=True**（用反射遍历工具注册表断言，
     这是防止「忘了标注」的结构性保证）
   - 只读工具被直接执行；写动作工具**从不**被执行，只产出草稿
   - 草稿执行走既有端点（mock 断言调用路径，证明没绕过风控）
3. tests/test_copilot_chat.py:
   - tool_calls 参数非法（`__raw__`）→ 回人话错误，不 500
   - 连环调用超过轮次上限 → 停下并说明
   - 未配置 provider → 引导语而非裸 501
4. 前端：草稿卡片显示完整参数；未配置时的引导块
5. ruff check app tests → All checks passed! · 前端 lint/tsc/test/build 全过
6. pytest -q 全绿
```

## 五、不做

- 流式响应（网关本期非流式）
- 会话持久化（本期在内存/前端保存即可）
- 多轮自动规划（agent loop）—— 本期是「问答 + 工具调用」，不是自主 agent
- 把全部端点包成工具（先做上面这批，验证形态再扩）
