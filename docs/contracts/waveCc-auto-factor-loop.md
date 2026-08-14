# V3 Wave C-c 契约：自动因子研发循环（I2）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **I2** · Agent-Cc
> 依赖：I0 LLM 网关 · 遗传挖掘 `app/quant/mining/genetic.py` · 适应度 `app/quant/factor_fitness.py` · 产物库 `app/quant/lab/store.py`
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、要做的事

把已有的四块**串起来**，形成一轮可重复的自动研发：

```
种子表达式 → evolve() 遗传搜索 → compute_factor_fitness() 适应度
          → 样本外验证 → LabStore 入库 → LLM 复盘 + 提出下一轮种子
```

```python
# app/quant/lab/auto_loop.py（新文件）

@dataclass(frozen=True)
class LoopConfig:
    universe: tuple[str, ...]
    generations: int = 5
    population: int = 40
    top_k: int = 5
    is_end: date                    # 样本内截止（见 §2.1）
    max_candidates_evaluated: int = 2000

@dataclass(frozen=True)
class LoopRound:
    round_id: str
    seeds: tuple[str, ...]
    survivors: tuple[FactorCandidate, ...]
    hypotheses_tested: int          # 见 §2.2，必须如实计数
    llm_review: str | None
```

端点：`POST /api/v1/lab/auto-loop`（启动一轮）· `GET /api/v1/lab/auto-loop/{round_id}`

---

## 二、三条必须做对的（这个功能最容易骗自己）

### 2.1 样本外验证不是可选项

遗传搜索**天然会过拟合它评估的那段数据**。若适应度和最终报告用的是同一段
样本，产出的 IC 只是搜索强度的度量，不是预测能力的度量。

**要求**：`LoopConfig.is_end` 之后的数据在整个搜索过程中**不可见**，
只在最终入库前跑一次，并把样本内/样本外两个 IC **并排存进产物**。

⚠️ 若样本外 IC 掉到样本内的一半以下，入库时标 `overfit_suspect=True`。
**不要直接丢弃** —— 让用户看到衰减幅度比替他做决定更有价值。

### 2.2 「检验了多少个假设」必须记录并展示

评估了 2000 个表达式后取最好的 5 个，它们的 IC 一定好看 —— 这是多重检验
下的必然，不是发现。

**要求**：`hypotheses_tested` 如实记录本轮实际评估过的候选数（含被淘汰的），
写进产物并在前端与因子指标同屏展示。

⚠️ **本期不实现 deflated Sharpe 之类的正式校正**（需要仔细推导，做错比不做更糟）。
就诚实地把分母摆出来，并在 UI 上一句话说明它的含义。
在报告里明确写出这是本期的取舍。

### 2.3 循环只入库，绝不自动上线

产出的因子写进 `LabStore`，**不自动注册为策略、不自动接实盘**。
晋级走已有的 `promote_to_strategy` 人工路径。

一个没人看过的自动挖掘结果直接进入交易链路，是这类系统最典型的事故来源。

---

## 三、LLM 的角色：复盘与提种子，不参与打分

- **复盘**：拿到本轮存活因子的表达式与指标，用自然语言说明它们在捕捉什么模式、
  彼此是否高度相关
- **提种子**：为下一轮提出若干候选表达式

⚠️ **LLM 提出的表达式必须过 `expression_tree` 的解析与算子白名单校验**，
非法的直接丢弃并计数。绝不 `eval` 模型输出的字符串。

⚠️ **LLM 不参与适应度打分**。打分必须是确定性的、可复现的 ——
让模型给因子打分，等于让一个不知道自己在猜的东西决定资金去向。

⚠️ 未配置 LLM provider 时，**循环仍然要能跑**（跳过复盘与提种子，用固定种子集）。
这个功能的核心是搜索，不是模型。

---

## 四、资源上限

- `max_candidates_evaluated` 是硬上限，达到即停止并在结果里标 `truncated=True`
- 走 Celery 异步执行，端点立即返回 `round_id`（沿用 `app/tasks/` 的 `asyncio.run()` 桥接约定）
- **不做**无人值守的定时循环 —— 手动触发一轮跑通再说

---

## 五、前端

产物库页（`/lab`）新增「自动循环」区：启动表单、轮次列表、单轮结果
（存活因子 + 样本内/外 IC 并排 + `hypotheses_tested` + LLM 复盘）。

⚠️ 不要编辑 `App.tsx` / `routes.tsx` / `Sidebar.tsx` / `types/index.ts`，
需改动时在报告里给集成片段。

---

## 六、验收

```
1. tests/regression 146 用例全绿
2. tests/test_auto_factor_loop.py:
   - is_end 之后的数据在搜索期不可见（断言评估器拿到的 panel 末日期）
   - 样本外 IC < 样本内一半 → overfit_suspect=True 且**仍然入库**
   - hypotheses_tested == 实际评估候选数（含淘汰的）
   - 达到 max_candidates_evaluated → truncated=True 且提前停止
   - 循环不注册策略（mock 断言 promote_to_strategy 零调用）
3. tests/test_auto_loop_llm.py:
   - LLM 返回非法表达式 → 丢弃并计数，不抛异常
   - LLM 输出的字符串不经 eval（断言解析走 expression_tree）
   - 未配置 provider → 循环正常完成，llm_review is None
4. 前端：样本内/外 IC 并排、hypotheses_tested 与其含义说明可见
5. ruff check app tests → All checks passed! · 前端 lint/tsc/test/build 全过
6. pytest -q 全绿
```

## 七、不做

- deflated Sharpe / Bonferroni 等正式多重检验校正（见 §2.2）
- 无人值守定时循环（见 §四）
- 自动晋级为策略（见 §2.3）
- 跨轮次的种子进化记忆（每轮独立，先跑通单轮）
