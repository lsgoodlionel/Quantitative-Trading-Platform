# V3 Wave A-d 契约：验证套件一键化（H2）+ 结果持久化（H5）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **H2 / H5** · Agent-Ad
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、H2 验证套件一键化

### 1.1 现状与目标

Backtest 页目前把回测 / 优化 / Hyperopt / WalkForward / 偏差检测 / 蒙特卡洛 / 稳健性
分散成多个独立表单，**每个都要重填一遍 symbol / strategy / 日期**。
这是全站最高频的路径，摩擦最大。

目标：
1. **共享配置头** —— symbol / strategy / 日期 / market / frequency 一处填
2. **合并 Tab** —— Optimize+Hyperopt 合一、MonteCarlo+Robustness 合一，收敛到 4 个 Tab
3. **「完整验证」按钮** —— 串行跑 回测 → 优化 → WalkForward → 偏差 → 稳健性，出综合评级

### 1.2 后端编排端点

```
POST /api/v1/backtests/full-validation
     { symbol, strategy, params, start, end, market, frequency, steps? }
  → { run_id, steps: {backtest: {...}, optimize: {...}, walkforward: {...},
                      bias: {...}, robustness: {...}},
      grade: {score, level, findings: [...]} }
```

**关键要求**：

1. **每一步失败不得中断整体**。某一步失败时该步返回 `{error: "..."}`，其余照常。
   综合评级要标注「基于 N/5 步」，不要拿不完整的数据给一个看起来很确定的评级。
2. **必须支持只跑部分步骤**（`steps` 参数）。全跑很慢，用户常常只想补一步。
3. **`grade` 的评级规则要写在代码里且可测**，不要塞进 prompt 或魔法数字。
   建议明确的扣分项：样本外夏普 < 样本内的 50% · 偏差检测有命中 · 蒙特卡洛 5% 分位为负 ·
   参数敏感性过高。每条扣多少分写成常量。
4. **长任务要能异步**。若同步跑会超时，走 Celery 并返回 `run_id` 供轮询
   （项目已有 Celery，见 `app/tasks/`）。

### 1.3 综合评级不要过度承诺

评级是**启发式汇总**，不是判决。`findings` 里每条要写清依据（哪一步的哪个指标越过了哪条线），
让用户能自己判断。**不要输出「建议实盘」这类结论性建议。**

---

## 二、H5 回测/实验结果持久化

### 2.1 现状

回测结果**只存在于响应里，刷新即失**。实验记录器（`app/quant/experiments/recorder.py`）
只存因子实验指标，且有 `MAX_RECORDS = 500` 的滚动淘汰。

### 2.2 设计

```
POST   /api/v1/backtests/history          保存一次回测结果（含配置 + 指标 + 净值曲线）
GET    /api/v1/backtests/history          列表（分页、按策略/标的/日期筛选）
GET    /api/v1/backtests/history/{id}     详情
DELETE /api/v1/backtests/history/{id}
POST   /api/v1/backtests/history/{id}/rerun   用原配置重跑
GET    /api/v1/backtests/history/compare?ids=a,b,c   对比视图数据
```

**两个必须想清楚的点**：

1. **存储介质**。实验记录器用 Redis + 500 条上限。回测历史若也用 Redis，
   用户攒几天就开始丢数据，而「消除刷新即失」正是本特性的目标 —— 丢数据等于没做。
   **建议落 TimescaleDB**（项目已有，见 `app/data/storage/timescale.py`）。
   若因故仍用 Redis，**必须**把容量上限与淘汰策略明确告知用户（前端显示「保留最近 N 条」），
   不要让它悄悄丢。

2. **净值曲线的体积**。一条 3 年日线净值曲线约 750 点，存 JSON 无妨；
   但分钟级会到几万点。**要么降采样后存，要么只存指标 + 配置并支持 rerun 重算**。
   在契约实现时选定一种并写进 docstring，不要两头不靠。

### 2.3 前端

- 回测结果区新增「保存」按钮
- 新增历史列表页（或在 Backtest 页加 Tab）：列表 → 详情 → 重跑 / 对比
- 对比视图：多条净值曲线叠加 + 指标表格并列

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_full_validation.py:
   - 全跑五步的正常路径
   - 某一步抛异常时其余步骤照常返回，该步带 error，grade 标注「基于 N/5 步」
   - steps 参数只跑指定步骤
   - 评级规则的边界用例（每条扣分项各一个用例）
3. tests/test_backtest_history.py:
   - 保存 → 列表 → 详情 → 重跑（重跑结果与原结果一致，证明配置完整存下来了）
   - 对比接口返回多条曲线且对齐
   - 分页与筛选
4. 前端：共享配置头改一处生效全 Tab；完整验证按钮出综合报告；历史列表与对比视图
5. ruff check app tests → All checks passed! · 前端 lint/type-check/test/build 全过
6. pytest -q 全绿
```

## 四、不做

- AI 解读验证结果（I5，Wave C；本期只出规则化评级）
- 跨用户的结果共享与权限（J3，Wave C）
- 历史结果的自动清理策略（先做手动删除）

⚠️ 不要编辑 `App.tsx` / `Sidebar.tsx` / `types/index.ts` / `router.py`，
需改动时在报告里给集成片段（历史列表若是新页面，一定会用到前两个）。
