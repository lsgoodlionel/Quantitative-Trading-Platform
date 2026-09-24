# V3 Wave A-c 契约：Screener 多选贯通（G3）+ 统一事件总线通知（G5）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **G3 / G5** · Agent-Ac
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、G3 Screener 多选贯通

### 1.1 现状与目标

筛选出一批标的后，用户只能**逐个手抄**到别处。V3 要的是「发现 → 构建」这条流。

目标：筛选结果支持多选 → 三个动作
1. **送组合优化**（带 symbols 跳转 `PortfolioOptimizer`）
2. **加自选池**
3. **批量回测**

### 1.2 后端

`PortfolioOptimizer` 相关端点需要接受 `symbols` 参数（现状需确认是否已支持；
若已支持则前端直接用，**不要为了「统一」而重构一个能用的端点**）。

批量回测：新增

```
POST /api/v1/backtests/batch
     { symbols: [...], strategy, params, start, end, market, frequency }
  → { results: [{symbol, metrics, error?}] }
```

⚠️ **批量回测要有上限**（建议 `MAX_BATCH_SYMBOLS = 50`）并在超限时返回明确错误，
不要让一次请求跑几百个回测把服务打满。超时/失败的单个标的应在 `results` 里带 `error` 字段
返回，**不要让一个标的失败导致整批 500**。

### 1.3 前端

`Screener.tsx`：结果表加多选（含全选/反选/已选计数），选中后出现动作条。
「送组合优化」通过 URL 参数传递 symbols —— 这符合项目既有的「URL 即状态」惯例
（见 `~/.claude/rules/ecc/web/patterns.md`，且本项目已有 `useWorkflowStorage` 等先例）。

---

## 二、G5 统一事件总线通知

### 2.1 现状

`app/notify/dispatcher.py:139` 已有 `dispatch_event()`，
`NotifyEventType` 现有 7 类：`trade_fill` · `order_reject` · `pnl_update` · `position` ·
`daily_summary` · `risk_alert` · `protection`。

问题是**长任务完成后没有任何通知**——回测、Hyperopt、因子挖掘都得守着屏幕等。

### 2.2 新增事件类型

```python
class NotifyEventType(str, Enum):
    ...                                  # 既有 7 类保持不变
    BACKTEST_DONE = "backtest_done"
    HYPEROPT_DONE = "hyperopt_done"
    MINING_DONE = "mining_done"
    DATA_SOURCE_DEGRADED = "data_source_degraded"
    RECONCILE_DIFF = "reconcile_diff"    # G6 对账差异（G6 本身不在本期）
```

### 2.3 接线点

在各长任务完成处调 `dispatch_event`。**注意不要在同步路径里阻塞**：
通知失败绝不能让任务本身失败。统一模式：

```python
try:
    dispatch_event(evt)
except Exception:
    logger.exception("通知发送失败，不影响任务结果")   # 记录但不上抛
```

> 这是**唯一**允许吞异常的地方，且必须记 exception 而非静默 —— 通知是旁路，
> 不该让一次 Telegram 超时把回测结果弄丢。

### 2.4 Alerts 改走 notify 渠道

价格预警目前是独立通路，改为统一走 `dispatch_event`（`risk_alert` 类型），
这样站内 / Telegram / Webhook 三个渠道的配置只有一份。

⚠️ **这是用户可见的行为变更**：原本只在站内出现的预警，配了 Telegram 后会推送到手机。
必须保证 `NotifyConfig` 里该事件类型默认**关闭 Telegram/Webhook**，只开站内，
让用户主动选择开启。

### 2.5 通知中心页

新增页面：通知历史列表 + 已读/未读标记。存储复用 Redis（与实验记录器同样注意容量上限）。

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_batch_backtest.py:
   - 超过 MAX_BATCH_SYMBOLS 返回明确错误
   - 单个标的失败时其余照常返回，该项带 error 字段
3. tests/test_notify_events.py:
   - 5 个新事件类型可派发
   - dispatch_event 抛异常时**任务本身仍成功**（用 mock 让通知失败，断言回测结果正常返回）
   - 新事件类型默认不开 Telegram/Webhook
4. 前端：Screener 多选 → 三个动作各自跳转/调用正确；通知中心列表渲染与已读切换
5. ruff check app tests → All checks passed! · 前端 lint/type-check/test/build 全过
6. pytest -q 全绿
```

## 四、不做

- G6 实盘对账本身（只预留 `RECONCILE_DIFF` 事件类型）
- 通知的重试队列与投递保证（旁路系统，尽力而为即可）
- 批量回测的并行化（先串行跑通，性能优化另议）

⚠️ 不要编辑 `App.tsx` / `Sidebar.tsx` / `types/index.ts` / `router.py`，需改动时给集成片段
（通知中心是新页面，一定会需要 `App.tsx` 路由与 `Sidebar.tsx` 入口 —— 请在报告里给出）。
