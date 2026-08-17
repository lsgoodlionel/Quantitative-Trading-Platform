# V4 Wave F-a 契约：自适应再训练 + 漂移检测（M6）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **M6** · Agent-Fa
> 依赖：**M5 统一模型模板已合入**（`app/quant/models/template.py` 的 `AlphaModelTemplate`）
> 状态：✅ 已实现（Agent-Fa）
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
    di_max: float
    threshold: float                 # 单样本判离群的 DI 阈值
    outlier_ratio_threshold: float   # 判 is_drifting 的离群占比阈值
    is_drifting: bool
    # 抽样与退化的如实记录（原稿漏了，但 §四.2 又要求断言 sampled_n）
    n_train_rows: int
    sampled_n: int
    is_sampled: bool
    n_dropped_rows: int
    degenerate: bool
```

> **原稿这个字段集与它自己的验收项矛盾。** §四.2 第 4 条要求断言
> 「`sampled_n` 如实出现在报告里」，而 `sampled_n` 根本不在原稿那 8 个字段里 ——
> 照抄定义就通不过自己的验收。
>
> 另外单个 `threshold` 撑不起两个语义：「单样本判离群的 DI 阈值」和
> 「判 `is_drifting` 的离群占比阈值」是两个东西。只给一个，
> 读者无法复算 `is_drifting` 是怎么来的，与 §2.2.4「让人能自己判断」直接冲突。

### 2.2 五条必须做对的

1. **特征必须先标准化。** 不同量纲的特征（价格 100 vs 收益率 0.01）直接算欧氏距离，
   结果完全由量纲最大的那一列决定。用训练集的 mean/std，**不是新数据的** ——
   用新数据的统计量标准化，等于把漂移本身抹掉了。
2. **训练集的统计量必须随模型一起持久化。** 重训一次就得存一份。
   下次判漂移时用的必须是**那个模型训练时**的分布，不是最近一次的。
3. **两两距离是 O(n²)。** ~~要么抽样，要么分块。~~ **两个都得做，不是二选一。**
   抽样解决的是**时间**（n=5 万时不抽样根本跑不完），
   分块解决的是**内存**（2000×2000 的广播中间量是 `2000×2000×n_features×8B`，
   特征多一点就是几百 MB）。
   **不要静默截断** —— 用了 1000 行样本却报得像全量算过。

   ⚠️ 距离用广播差值算，**不要用 `‖a‖²+‖b‖²−2ab` 展开**：后者省内存，
   但在距离趋零时丢有效位，可能开根出 NaN。
4. **`is_drifting` 是启发式，不是判决。** 阈值怎么定都有主观性。
   报告里必须带上 `threshold` 与 `outlier_ratio` 原始值，让人能自己判断。
   与项目既有立场一致：给依据，不替用户下结论。
5. **漂移 ≠ 必须重训。** 检测到漂移只发通知（走 G5 事件总线），
   **不自动触发重训** —— 一个市场剧变的当天自动重训，学到的正是那天的噪声。

6. **（原稿漏了，最容易静默踩中的一条）漂移检测的取特征路径必须与训练分开。**
   若复用「带标签的训练表」取特征，会丢掉每个标的最后 `forward_period` 根 bar
   —— 标签为 NaN 被 `dropna` 清掉，而那恰恰是**最需要看的最新数据**。
   实测（forward_period=5）：特征表最新到 10-28、训练表只到 10-23，**差整整 5 天**。
   症状是「漂移永远慢 5 天」，测试里几乎不可能发现。
   必须拆出无标签的 `build_feature_frame()`，并有用例守住「保留最新 bar」。

7. **（原稿漏了）模型 + baseline 的持久化形态。** 若存成元组或普通 bundle 塞进
   `LabStore`，`GET /lab/artifacts/{id}/detail` 会因对象没有 `detail()` 而 400 ——
   一个不报错、只是「详情页空了」的部署级回归。
   让包装类实现 `AlphaModelTemplate`，`detail()` 返回内层 detail + baseline 摘要。

8. **（原稿漏了）漂移需要独立的 `NotifyEventType`。** 枚举里只有 Wave O-a 预留的
   `RETRAIN_DONE`；复用它会让用户无法只订阅漂移，而漂移恰恰是唯一需要
   立刻看一眼的那个。新增 `MODEL_DRIFT`（默认仅站内）。

---

## 三、定时再训练

### 3.1 三条必须做对的

1. **绝不自动上线。** 重训产出新模型 → 写进 `LabStore` → **人工确认后才替换**。
   与自动因子循环（I2）同一立场：没人看过的自动产物不进交易链路。
2. **新旧模型必须在同一段样本外数据上对比**，把两者的 IC / 夏普并排存进产物。
   只报新模型的指标等于没有对比。

   > ⚠️ **「夏普」在这个数据形状下没有无歧义口径，原稿留了白。**
   > 标签是**重叠的** `forward_period` 日前瞻收益，模型输出是无量纲打分而非仓位 ——
   > 要算夏普必须先定义信号→收益的映射。
   > 实现取 `sign(pred) × label` 逐日横截面均值、按 `sqrt(252/forward_period)` 年化，
   > 并在 docstring 与报告里明写：**它系统性偏乐观，只能用于新旧对比，
   > 不是可交易的收益预期。**

3. **（原稿漏了）训练数据从哪来。** 原稿同时提到 `LabStore` 和「取数」，
   但没说是读 DATASET 产物还是从行情重建。取后者 ——
   定时任务的语义就是「用最新数据重训」，静态数据集产物做不到。
   代价是特征集被钉死在 `ml_strategy.FEATURE_NAMES` 那 8 个技术指标上。
3. **重训失败不能留下半个模型。** 先训练 + 评估完成，再整体写入；
   中途失败保持旧模型不动，并发通知。

### 3.2 调度

Celery beat 周期任务（沿用 `app/tasks/` 的 `asyncio.run()` 桥接约定），
默认**关闭**，由配置显式开启。

⚠️ **关闭时连 beat 条目都不要注册。** 注册一个进去就 `return` 的任务，
会在 beat 日志里每周留下一条「看起来在工作」的记录 —— 比不注册更糟。
任务体内再留第二道检查作防御。

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
- 漂移触发的自动重训（见 §2.2 第 5 条）
- 在线/增量学习
- 概念漂移的因果归因（「为什么漂了」是另一个量级的问题）
