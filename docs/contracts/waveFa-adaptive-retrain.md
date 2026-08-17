# V4 Wave F-a 契约：自适应再训练 + 漂移检测（M6）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **M6** · Agent-Fa
> 依赖：**M5 统一模型模板已合入**（`app/quant/models/template.py` 的 `AlphaModelTemplate`）
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）逐字节不变且全绿。
>
> ⚠️ **许可证**：freqtrade 是 **GPL-3.0**。`freqai/data_kitchen.py` 的 DI 算法
> **只读思路、独立实现**，一行代码都不许抄。本文件里也不要粘贴它的代码片段。

---

## 一、要做的事

两件互相独立、但配合起来才有意义的事：

1. **漂移检测**：判断「当前特征分布是否已经偏离训练时的分布」
2. **定时再训练**：Celery 周期任务，滚动窗口重训并归档

```
app/quant/drift.py          漂移指标（纯函数，无 IO）
app/quant/retrain.py        再训练编排（取数 → 训练 → 评估 → 归档）
app/tasks/retrain.py        Celery 任务
app/api/v1/endpoints/retrain.py   手动触发 + 查历史
```

---

## 二、漂移检测

### 2.1 指标：DI（Dissimilarity Index）

思路（**自行实现，不抄代码**）：对训练集特征算两两距离得到一个基准尺度，
再看新样本到训练集的最近距离相对这个尺度有多大。超过阈值即判为「离群」。

```python
@dataclass(frozen=True)
class DriftReport:
    n_samples: int
    n_outliers: int
    outlier_ratio: float
    di_mean: float
    di_p95: float
    threshold: float
    is_drifting: bool
```

### 2.2 五条必须做对的

1. **特征必须先标准化。** 不同量纲的特征（价格 100 vs 收益率 0.01）直接算欧氏距离，
   结果完全由量纲最大的那一列决定。用训练集的 mean/std，**不是新数据的** ——
   用新数据的统计量标准化，等于把漂移本身抹掉了。
2. **训练集的统计量必须随模型一起持久化。** 重训一次就得存一份。
   下次判漂移时用的必须是**那个模型训练时**的分布，不是最近一次的。
3. **两两距离是 O(n²)。** 训练集上万行时直接算会炸内存。
   要么抽样（记录抽样数并在报告里说明），要么分块。
   **不要静默截断** —— 用了 1000 行样本却报得像全量算过。
4. **`is_drifting` 是启发式，不是判决。** 阈值怎么定都有主观性。
   报告里必须带上 `threshold` 与 `outlier_ratio` 原始值，让人能自己判断。
   与项目既有立场一致：给依据，不替用户下结论。
5. **漂移 ≠ 必须重训。** 检测到漂移只发通知（走 G5 事件总线），
   **不自动触发重训** —— 一个市场剧变的当天自动重训，学到的正是那天的噪声。

---

## 三、定时再训练

### 3.1 三条必须做对的

1. **绝不自动上线。** 重训产出新模型 → 写进 `LabStore` → **人工确认后才替换**。
   与自动因子循环（I2）同一立场：没人看过的自动产物不进交易链路。
2. **新旧模型必须在同一段样本外数据上对比**，把两者的 IC / 夏普并排存进产物。
   只报新模型的指标等于没有对比。
3. **重训失败不能留下半个模型。** 先训练 + 评估完成，再整体写入；
   中途失败保持旧模型不动，并发通知。

### 3.2 调度

Celery beat 周期任务（沿用 `app/tasks/` 的 `asyncio.run()` 桥接约定），
默认**关闭**，由配置显式开启。

⚠️ **worker 进程拿不到 FastAPI 进程内的任何单例** —— 这是 Wave C-b 踩过的坑
（`get_order_manager()` 是模块级全局，worker 里永远是空的）。
再训练只依赖数据库/文件系统，不要碰任何进程内状态。

---

## 四、验收

```
1. tests/regression 146 用例全绿，git diff tests/regression/ 为空
2. tests/test_drift.py:
   - 同分布数据 → is_drifting=False
   - 明显平移的数据 → is_drifting=True 且 outlier_ratio 显著上升
   - 标准化用的是**训练集**统计量（构造一个「新数据方差大 10 倍」的用例，
     断言漂移能被检出 —— 若误用新数据统计量，这个用例会漏报）
   - 训练集超过抽样上限时 sampled_n 如实出现在报告里
   - 特征全常数（std=0）不产生 NaN/除零
3. tests/test_retrain.py:
   - 重训不自动替换线上模型（mock 断言）
   - 新旧模型的样本外指标并排出现在产物里
   - 训练中途失败 → 旧模型不变 + 发通知
   - 检测到漂移**不**自动触发重训
4. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 五、不做

- 自动上线新模型（见 §3.1）
- 漂移触发的自动重训（见 §2.5）
- 在线/增量学习
- 概念漂移的因果归因（「为什么漂了」是另一个量级的问题）
