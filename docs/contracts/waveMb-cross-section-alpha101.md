# Wave M-b 契约：截面算子进公式引擎（M2）+ Alpha101 因子集（M3）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **M2 / M3** · Agent-Mb
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、M2 的核心难点：公式引擎目前是**单标的**的

这是本契约最重要的一条，先说清楚再谈实现。

```python
# app/quant/formula_factor.py:220
def evaluate_formula(df: pd.DataFrame, tokens: list[str]) -> pd.Series
```

它吃的是**一个标的**的 OHLCV 帧。而截面算子（cs_rank / cs_zscore …）的定义是
「**同一时刻、跨标的**做排名/标准化」—— 单标的帧里根本没有截面可言。

现有 `OPS` 表里的 `RANK` 被标成「截面」分类，但实现是
`x.rolling(60).apply(...)`，其 docstring 自己写着「截面 rank 的**时序近似**」。
**分类标签是错的，实现是诚实的。** 本契约要把这个名不副实的地方一并纠正。

### 1.1 设计：新增 panel 模式，不动既有单标的路径

```python
# app/quant/formula_factor.py

def evaluate_formula(df, tokens) -> pd.Series:
    """单标的求值。**签名与行为完全不变** —— 现有调用方一律不受影响。
    公式含 CS_* 算子时抛 FormulaError，明确告知需要走 panel 模式。"""

def evaluate_formula_panel(
    panel: pd.DataFrame, tokens: list[str]
) -> pd.Series:
    """面板求值。panel 为 (datetime, instrument) 双层索引（见 app/quant/panel.py）。

    时序算子逐标的分组计算，CS_* 算子按 datetime 分组跨标的计算。
    返回同样是双层索引的 Series。
    """
```

⚠️ **不要把 `evaluate_formula` 改成能同时吃两种输入**。它有大量调用方
（因子挖掘、因子适应度、`FormulaFactorAlphaModel`、API），一个函数按入参形态
分叉行为是最容易出错的设计。两个函数、两条路径，各自清晰。

### 1.2 新增算子

| token | 语义 |
|---|---|
| `CS_RANK` | 截面分位排名，归一化到 [0,1] |
| `CS_ZSCORE` | 截面标准化 |
| `CS_DEMEAN` | 截面去均值 |
| `CS_SCALE` | 截面缩放至 Σ\|x\| = 1 |
| `CS_MEAN` / `CS_STD` | 截面均值/标准差（广播回各标的） |

参考 vnpy `alpha/dataset/cs_function.py`（**MIT，可直接移植**，约 64 行）。

**每个截面时点的有效标的数少于 2 时返回 NaN** —— 一个标的的「截面排名」
恒为 1，那不是信息，是噪音。

### 1.3 纠正 `RANK` 的分类标签

`OpSpec("RANK", ..., "滚动分位排名", "截面")` → 分类改为 `"时序"`，
并在描述里写明它是滚动近似、真截面请用 `CS_RANK`。
**不要改它的实现或删掉它** —— 已有公式、已保存的策略、已记录的实验都在用它。

### 1.4 接入点

- `FormulaFactorAlphaModel`：公式含 CS_* 时走 panel 路径。
  它已经能拿到 `ctx.histories`（全标的），构建 panel 是现成的。
- **遗传挖掘**：`app/quant/mining/genetic.py` 的搜索空间要能包含 CS_* 算子，
  这样才能搜出截面 alpha —— 这正是 M2 的价值所在。
  但挖掘目前也是单标的路径，接入成本可能较高：
  **若一轮实现不完，先只做 `FormulaFactorAlphaModel` 侧，把挖掘侧作为已知缺口如实报告**，
  不要为了「都做了」而交一个半通的挖掘路径。

---

## 二、M3 Alpha101 因子集

移植 WorldQuant 101 因子，参考 `refs/vnpy-LHJY/vnpy/alpha/dataset/datasets/alpha_101.py`
（**MIT，可移植**）。

### 2.1 落点

```
app/quant/factor_lib/alpha101.py
```

注册进既有因子库（`generate_factor_library()`），与 Alpha158 风格因子并列可选，
这样 IC 排行、因子库回测入口（V3 A-a）全部自动可用 —— **不要另起一套注册表**。

### 2.2 三个务实的点

1. **Alpha101 大量依赖截面算子**，因此依赖 M2。若某个 alpha 用到本期没实现的算子，
   **跳过并记录**，不要用近似算子凑数 —— 一个「名字叫 alpha_042 但算法不是」的因子
   比没有这个因子危险得多。最终报告要列出实现了哪些、跳过了哪些及原因。
2. **`FactorSpec.compute` 的签名是单标的帧**（`Callable[[pd.DataFrame], pd.Series]`）。
   截面型 alpha 装不进这个签名。需要扩展 `FactorSpec` 支持 panel 型 compute，
   或另建 `PanelFactorSpec`。**这个设计决定请在报告里说明选了哪种及理由。**
3. **不要承诺 101 个全做**。逐个验证正确性的成本很高。
   做多少报多少，每个都要有对拍或至少形状/取值范围的断言。

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_cross_section_ops.py:
   - 6 个 CS_* 算子各自的数值正确性（手算小样本对拍）
   - 单标的时点返回 NaN（不是 1.0 也不是 0.0）
   - NaN 标的被排除在截面之外，不影响其他标的的排名
   - evaluate_formula 遇到 CS_* 抛 FormulaError 且错误信息指向 panel 模式
   - evaluate_formula 对不含 CS_* 的公式**逐值等于**改动前的结果
     （这是「既有路径未受影响」的证据，请用固定样本硬编码期望值）
3. tests/test_alpha101.py:
   - 已实现的每个 alpha 至少一个形状 + 取值范围断言
   - 注册进因子库后能被 generate_factor_library() 拿到
   - 能走通 V3 A-a 的因子库回测入口（端到端一个用例）
4. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 四、不做

- Pipeline 式声明因子图（D6，Wave N+O）
- 截面算子在实盘的增量计算优化
- Alpha101 的参数调优
