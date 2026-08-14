# torch 启用评估（J4）· 2026-08-14

> J4 原文：「torch 启用评估（激活 B8 序列模型；ARM64 镜像成本测试）」。
> 这一项的产出应该是**一份结论**而不是代码 —— 所以它一直被推迟，
> 而不是被实现。这份就是结论。

---

## 一、结论

**不启用。保持 torch 为可选依赖、端点 501 的现状。**

代价明确、收益未经证实，而现状已经是「装了就能用、不装也不碍事」的最优形态 ——
真正想用的人 `pip install torch` 即可，不需要平台替所有人做这个决定。

---

## 二、代价：镜像体积翻倍还多

PyPI 上 torch 2.13.0 的 cp311 wheel（**压缩包**体积，解包后更大）：

| 平台 | wheel |
|---|---|
| linux x86_64 | **527 MB** |
| linux aarch64（ARM64） | **427 MB** |
| macOS arm64 | 111 MB |

对照：本项目当前整个虚拟环境（含 pandas / numpy / scipy / sklearn /
alpaca / futu / akshare 等全部依赖）**505 MB**。

也就是说，**加一个 torch ≈ 把现有全部依赖再装一遍还多**。
生产镜像基于 `python:3.11-slim`，这个增量会直接反映到拉取时间与磁盘占用上。

> ARM64 比 x86_64 小 100 MB，但 427 MB 依然是同一个量级 ——
> 「换 ARM64 能显著降低 torch 成本」这个假设不成立。
> J4 原文里的「ARM64 镜像成本测试」到此可以结案：换架构解决不了体积问题。

---

## 三、收益：未经证实，且平台已有同类能力

### 3.1 序列模型现在处于什么状态

`app/quant/models/sequence.py` + `networks.py` 已实现 LSTM / GRU / ALSTM
（参考 qlib 的 pytorch 实现），torch 走**函数内延迟导入**，
`torch_available()` 为 False 时：

- `GET /quant/sequence-models` 正常返回，`torch_ready: false` + 安装提示
- `POST` 训练端点返回 **501** + 安装提示
- 其余功能完全不受影响（已验证：相关测试 5 passed / 1 skipped，
  跳过的那条正是 torch 缺失时自动跳过的训练用例）

**这个降级做得是对的**，不需要动。

### 3.2 平台已有的非深度学习路径

| 已有 | 依赖 | 状态 |
|---|---|---|
| `HistGradientBoostingRegressor` (`models/boosting.py`) | sklearn（已装） | 可用 |
| `LogisticRegression` / `RandomForest` / `GradientBoosting` (`ml_strategy.py`) | sklearn（已装） | 可用 |
| DoubleEnsemble (`double_ensemble.py`) | sklearn | 可用 |
| 遗传公式挖掘 + Alpha101 | numpy/pandas | 可用 |
| LSTM / GRU / ALSTM | **torch（未装）** | 501 |

**没有任何证据表明序列模型在本项目的数据规模上会优于 GBDT。**
量化领域公开的对比（含 qlib 自己的 benchmark）普遍显示：
日频、中等样本量、强噪声的场景下，GBDT 类模型与 LSTM 类模型互有胜负，
且 GBDT 在训练成本与可解释性上占优。

**要证明收益，需要的是一次同数据同口径的对拍实验，不是把 torch 装上。**

### 3.3 已知的实现缺陷（启用前必须先修）

`sequence.py` 的 `_build_sequences` **把特征与标签耦合在一起构造**，
导致最新一根 bar（特征齐全但标签未知）被一并丢弃 ——
这与 `ml_strategy.py` / `double_ensemble.py` 已修正的口径不一致。
那两处已改为 `X_latest = X.dropna()`，序列模型这处**尚未修**，
因为 torch 装不上、改了也验不了。

**这是「启用 torch」之前必须先解决的事**：一个连最新信号都产不出的模型
装上也没用。

---

## 四、若将来要重新评估，需要什么

按重要性排序 —— 前两条不做，装 torch 就是白装：

1. **先修 §3.3 的训练/预测集耦合**，并补一条断言「最新 bar 能产出信号」的测试。
2. **做一次 GBDT vs LSTM 的同数据对拍**（同一 universe、同一特征集、
   同一前瞻期、同一评估指标），拿到 IC / 夏普的实际差距。
   差距不显著就到此为止 —— 那是最可能的结果。
3. 若差距显著，再谈镜像：可以做**独立的训练镜像**（含 torch）+
   主镜像不含，训练走离线任务、主服务只加载权重。
   这样 527 MB 只压在训练侧，不影响 API 服务的部署体积。
4. ARM64 不必再测（见 §二）。

---

## 五、这份评估的局限

- **没有实际安装 torch 跑过训练。** 体积数据来自 PyPI 元数据（准确），
  但「训练一次要多久、内存峰值多少」没有实测 —— 因为在得出「先修缺陷、
  先做对拍」这两条之前，那些数字用不上。
- **§3.2 关于 GBDT 与 LSTM 优劣的判断依据是领域共识，不是本项目的实测。**
  这正是 §四第 2 条要做的事。**不要把它当成已验证的结论。**
