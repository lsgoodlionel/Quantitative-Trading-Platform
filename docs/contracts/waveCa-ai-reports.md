# V3 Wave C-a 契约：AI 个股研报（I4）+ 回测 AI 诊断（I5）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **I4 / I5** · Agent-Ca
> 依赖：**I0 LLM 网关已合入**（`app/core/llm/`）
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 零、一处依赖的重新判断

蓝图写 I4 依赖 **I3 新闻情绪因子**，而 I3 至今没做。

但摸底后发现**不必卡在它上面**：`app/data/providers/news_provider.py` 已能抓取
公司新闻，缺的只是「情绪打分」这一步。而研报本来就该让 LLM **直接读原始新闻**，
而不是先压缩成一个情绪分再喂给它 —— 中间那一步是有损的，且会把
「为什么看空」这类可解释性丢掉。

**所以 I4 不引入 I3，直接消费 `news_provider` 的原始条目。**
若后续 I3 落地，它是另一条独立的因子路径，与研报无耦合。

---

## 一、I4 AI 个股研报

```
POST /api/v1/ai/reports/stock   { symbol, market, lookback_days? }
  → { symbol, sections: {...}, sources: [...], generated_at, model }
```

**输入**（全部来自既有端点/服务，不新增数据源）：
行情与技术指标 · 近期新闻条目 · 财报日历 · 期权隐含波动率（有则带上）

**输出分节**：概览 / 技术面 / 消息面 / 风险提示 / 关注要点

### 1.1 四条必须遵守的

1. **`sources` 必须列出实际喂给模型的新闻标题与时间**。
   一篇说不清依据的研报不如不生成 —— 用户要能自己核对模型有没有瞎编。
2. **免责声明是结构化字段而非提示词里的一句话**：
   返回体里带 `disclaimer`，前端固定展示。**不要输出「建议买入/卖出」这类操作结论**，
   与 A-d 的验证评级同一立场 —— 这是启发式汇总，不是投资建议。
3. **数据缺失时明说而不是让模型编**。拿不到新闻就在该节写「本期无可用新闻」，
   并在提示词里明确禁止基于空数据推断。
4. **不做缓存**（本期）。研报依赖实时行情与新闻，缓存的过期语义比省下的
   一次调用更麻烦。若将来要缓存，键必须含数据快照时间。

---

## 二、I5 回测 AI 诊断

```
POST /api/v1/ai/reports/backtest   { run_id?, grade, steps? }
  → { summary, coverage, is_complete, grade_level, grade_score,
      findings: [...], next_steps: [...],
      contradictions: [...], has_contradiction, disclaimer, model }
```

**输入**：A-d 已建的 `full-validation` 五步结果 + 规则化评级 `grade`。

> **契约原稿写的是 `{ backtest_id | full_validation_run_id }`，那是错的。**
> `full_validation.py` 的 `run_id` 是每次请求现生成的 `uuid4().hex[:16]`，
> 平台**从不落库**；`backtest_id` 同理。按 id 反查必然查空。
> 改为**直接收结果体本身**（前端手里就有那份数据），`run_id` 仅作溯源展示。
> 若日后要按 id 取，得先建 `full_validation_runs` 表 —— 那是独立的一项工作。

### 2.1 关键设计：AI 解读**规则评级**，而不是取代它

A-d 的 `validation_grade.py` 已经产出结构化的 `findings`（每条写明触发依据）。
I5 的价值是把那些机器判据**翻译成人能读的诊断与下一步**，
而不是自己重新判一遍。

⚠️ **提示词里必须把 `grade.findings` 原样给模型，并要求它的解读不得与之矛盾。**
若模型说「参数很稳健」而规则判据写着「敏感性过高」，那是明确的 bug ——
验收里有一条用例覆盖这种矛盾。

⚠️ **`grade.based_on` 是「基于 N/5 步」**。诊断里要如实带上这个覆盖度，
不能拿三步的结果给一个听起来很全面的结论。

### 2.2 「矛盾」如何判定（原稿没定义）

用模型去审模型成本翻倍且同样不可靠。实际实现是 **词表 + 否定护栏** 的启发式
（`app/ai_reports/contradiction.py`）：每条规则配一组反向断言词表
（`param_sensitivity` ↔「参数稳健 / 对参数不敏感」…），命中后再检查前后否定词，
避开「样本外表现稳健**性不足**」「**并非**无过拟合」这类误报。

⚠️ **它是兜底告警，不是准入判定。** 会漏报（模型换个说法就绕过），但极少误报。
命中时前端出红条「请以规则判据为准」，**报告照常展示** ——
把报告直接丢掉反而让用户看不到问题出在哪。

---

## 三、共用约束

- 两个端点都走 `app/core/llm/service.resolve_active()`；
  **未配置 provider → 501 + 指向 `/settings/models` 的引导**

  > 原稿这里写「与 Copilot 一致」，**措辞是错的**：Copilot 恰恰不回 501，
  > 它回 200 + `needs_setup=true`（`copilot.py::_setup_guidance`）。
  > 两者刻意不同 —— 聊天响应天然有 message 字段能承载引导语；
  > 研报是产出资源的一次性 POST，回 200 就得往响应体里塞一份假报告。
  > **用户体验一致（都渲染引导块），协议不一致（501 vs 200）。**

- LLM 调用要有超时；**JSON 解析失败最多纠正一次**（共 2 次调用，测试锁死调用数）。
  报告场景不调工具，没有 Copilot 那种 tool round —— 原稿的「轮次上限」指的是这个。
  失败时返回结构化错误而非半截报告
- **测试全部 mock LLM**，不连任何真实模型

### 3.1 数据覆盖度的真实边界（原稿没写，实现时才发现）

- **A 股没有公司新闻源**：`news_service.get_news` 对 A 股直接返回空。
  所以 A 股研报的 `sources` 恒为空、消息面恒走「本期无可用新闻」那条路径。
- **期权 IV 仅美股**（`endpoints/options.py` 明写）。港股/A 股不空跑请求，
  直接写一条 `data_note` 说明。

§1.1 第 1 点「sources 必须非空」只对 **US/HK** 成立。

---

## 四、前端

- 个股研报：行情页（`/market`）新增「AI 研报」动作，结果以分节卡片展示，
  底部固定显示 `sources` 与 `disclaimer`
- 回测诊断：验证页（`/backtest`）完整验证结果区下方新增「AI 诊断」按钮

⚠️ 不要编辑 `App.tsx` / `routes.tsx` / `Sidebar.tsx` / `types/index.ts`，
需改动时在报告里给集成片段。

---

## 五、验收

```
1. tests/regression 146 用例全绿
2. tests/test_ai_stock_report.py:
   - sources 包含实际喂给模型的新闻标题（不是空列表）
   - 无新闻时该节写明「无可用新闻」，且提示词含禁止臆测的指令
   - 未配置 provider → 501 且 detail 指向 /settings/models
3. tests/test_ai_backtest_diagnosis.py:
   - grade.findings 被原样放进提示词（断言提示词内容）
   - 模型输出与规则判据矛盾时 → 标记为异常而非照单全收
   - based_on 覆盖度出现在诊断结论里
4. 前端：分节卡片、sources 与 disclaimer 固定展示
5. ruff check app tests → All checks passed! · 前端 lint/tsc/test/build 全过
6. pytest -q 全绿
```

## 六、不做

- I3 新闻情绪因子（独立路径，见 §零）
- 研报缓存（见 §1.1 第 4 点）
- 多标的批量研报（先把单标的形态跑通）
- 研报导出 PDF
