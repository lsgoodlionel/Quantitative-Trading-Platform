# Wave L-b 契约：组合风控模型 + 执行模型

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **L2 / L3** · Agent-Lb
> 依赖：K-d（`engine/framework/` 已建）
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）必须全绿。
> 默认组合仍是 `NullRiskModel` + `ImmediateExecutionModel`，不装新模型时行为不变。

---

## 一、现状（K-d 交付后）

`app/engine/framework/` 已有三段式骨架，但风控与执行各只有一个最小实现：

| 文件 | 现有内容 |
|---|---|
| `risk.py`（42 行） | `RiskManagementModel` ABC + `NullRiskModel`（直通） |
| `execution.py`（84 行） | `ExecutionModel` ABC + `ImmediateExecutionModel`（目标 → 市价 diff 单） |

`FrameworkStrategy` 的调用链已经打通：
`alpha.update() → pcm.create_targets() → risk.manage_risk() → execution.execute() → ctx.submit()`。
**本契约只填模型，不动调用链。**

---

## 二、L2 组合风控模型（5 个）

移植自 `refs/Lean-LHJY/Algorithm.Framework/Risk/*.py`（Apache-2.0，Python 源码，
**可直接翻译并保留版权头**）。Lean 的签名是 `manage_risk(algorithm, targets)`，
我们已有的等价签名是 `manage_risk(ctx: PortfolioContext, targets) -> list[PortfolioTarget]`。

| 模型 | 参数 | 语义 |
|---|---|---|
| `MaximumDrawdownPerSecurity` | `max_drawdown=0.05` | 单标的浮亏超阈值 → 该标的目标清零 |
| `MaximumDrawdownPortfolio` | `max_drawdown=0.05`, `is_trailing=False` | 组合回撤超阈值 → **全部**目标清零 |
| `MaximumUnrealizedProfitPerSecurity` | `max_profit=0.05` | 单标的浮盈超阈值 → 落袋，目标清零 |
| `MaximumSectorExposure` | `max_exposure=0.20` | 单行业敞口超阈值 → 按比例缩减该行业内各标的目标 |
| `TrailingStopRiskManagement` | `max_drawdown=0.05` | 从**持仓期内最高浮盈**回撤超阈值 → 目标清零 |

### 2.1 三个必须做对的点

1. **风控模型输出的是「修正后的 PortfolioTarget 列表」，不是订单**。
   清仓表达为 `PortfolioTarget(symbol, quantity=0)`，由执行模型算出 diff 后下单。
   直接调 `ctx.sell()` 是错的 —— 会绕过执行模型，也拿不到 diff 语义。

2. **`MaximumSectorExposure` 需要行业数据，而本项目没有统一的行业字典**。
   现状：`app/data/symbol_dict.py` 是静态字典（蓝图 C4 已记为缺口）。
   **本期做法**：模型接受一个 `sector_of: Callable[[str], str | None]` 注入，
   拿不到行业（返回 None）的标的**跳过而非当成同一行业** —— 后者会把所有未知标的
   算成一个巨大敞口而全部砍掉。默认注入读 `symbol_dict`，取不到就是 None。

3. **浮盈/浮亏的基准是 `Trade.open_price`，不是 `Position.avg_cost`**。
   K-d 已建 `Trade` 概念并处理了「加仓改变止损基准」的问题（同向加仓时重置收益率极值）。
   风控模型必须复用 `engine/backtest/trade.py` 的 `Trade`，不要另算一套浮盈。

### 2.2 与 K4 策略级止损的分工

**两者都会平仓，必须说清楚差别，否则会重复触发**：

| | K4（`broker_exits.py`） | L2（`framework/risk/`） |
|---|---|---|
| 作用域 | 单个 `Trade` | 整个组合的 `PortfolioTarget` 集合 |
| 时机 | 每根 bar，`on_bars` **之前** | 每次调仓，PCM 之后、执行之前 |
| 适用 | 传统策略（16 个 preset 那类） | `FrameworkStrategy` |
| 输出 | 直接挂平仓单 | 修正目标数量 |

同时启用时两者会各自触发一次平仓。**本期不做互斥**，但必须在 `risk/__init__.py`
的模块 docstring 里写明这一点，并在验收里加一个「两者同时开启不会导致超卖」的用例
（第二次平仓应因持仓已为 0 而成为 no-op，不得出现负持仓或异常）。

---

## 三、L3 执行模型（3 个新增）

移植自 `refs/Lean-LHJY/Algorithm.Framework/Execution/*.py`（Apache-2.0）。

| 模型 | 语义 |
|---|---|
| `VolumeWeightedAveragePriceExecution` | 按成交量占比拆单，单 bar 不超过 `max_order_percent_volume`（默认 1%）；未完成部分留到下一 bar |
| `StandardDeviationExecution` | 价格偏离均值达 N 个标准差且方向有利时才成交（`period=60, deviations=2`） |
| `SpreadExecution` | 买卖价差收窄到阈值内才成交（`accepted_spread_percent=0.005`）；只有 OHLCV 时用 `(high-low)/close` 近似，**必须在 docstring 写明这是近似而非真实盘口** |

### 3.1 与 K6 成交量约束滑点的关系（易混淆，必须区分）

- **K6 `VolumeShareSlippage.fill_limit()`** 是**撮合层**约束：券商侧「这根 bar 最多成交这么多」，
  策略无法规避。
- **L3 `VolumeWeightedAveragePriceExecution`** 是**执行层**策略：主动把大单拆小以降低冲击。

两者叠加是正常的：执行模型先拆单，撮合层再对拆出来的单子施加上限。
验收里要有一个两者同时开启的用例，确认不会互相打架、且账目守恒。

### 3.2 实盘复用

蓝图写「实盘复用现有 `oms/algos/` 作为底层」。现有 `app/oms/algos/` 已有
`twap.py` / `vwap.py` / `iceberg.py` / `executor.py`。

**本期只做接口对齐，不做实盘接线**（实盘接线是本 Wave 的独立步骤）：
`ExecutionModel` 保持只依赖 `PortfolioContext`，不 import 任何 `oms` 模块。
在 `execution/__init__.py` docstring 里记录「实盘时由 `ctx.submit` 路由到 OMS，
届时可将本层的拆单参数映射到 `oms/algos/` 的同名算法」。

---

## 四、文件规划

```
app/engine/framework/risk/          # 由单文件 risk.py 升级为包
    __init__.py                     # 导出 + 与 K4 分工的说明
    base.py                         # RiskManagementModel ABC + NullRiskModel（从原 risk.py 迁入）
    drawdown.py                     # PerSecurity / Portfolio / TrailingStop
    profit.py                       # MaximumUnrealizedProfitPerSecurity
    sector.py                       # MaximumSectorExposure
app/engine/framework/execution/     # 由单文件 execution.py 升级为包
    __init__.py
    base.py                         # ExecutionModel ABC + ImmediateExecutionModel（迁入）
    vwap.py / std_dev.py / spread.py
```

⚠️ `risk.py` → `risk/` 与 `execution.py` → `execution/` 是**模块变包**，
必须保证 `from app.engine.framework.risk import NullRiskModel` 等既有导入路径不变。

---

## 五、验收

```
1. tests/regression 146 用例全绿（默认仍是 NullRiskModel + ImmediateExecutionModel）
2. tests/test_risk_models.py（注意：已有同名文件测的是 engine/portfolio/risk_models.py，
   本契约的新文件请命名 tests/test_framework_risk.py 避免撞车）:
   - 5 个模型各自的触发/不触发边界
   - MaximumSectorExposure 在行业未知时**跳过**该标的，不得并入同一敞口
   - 浮盈浮亏基准取自 Trade.open_price，加仓后行为符合 K-d 既定语义
   - K4 与 L2 同时开启不产生超卖/负持仓
3. tests/test_framework_execution.py:
   - VWAP 拆单：单 bar 不超过占比上限，残量下一 bar 继续，累计成交 == 目标
   - StandardDeviation / Spread 的成交与不成交条件
   - 与 K6 成交量约束滑点叠加时账目守恒（成交 + 挂单 + 显式作废 == 委托）
4. 端到端：FormulaFactorAlphaModel + OptimizerPCM(HRP) + MaximumDrawdownPortfolio +
   VWAPExecution 在 5 标的上跑通组合回测
5. ruff check app tests → All checks passed!
6. pytest -q 全绿（当前 1033 passed，覆盖率门禁 61）
```

## 六、不做

- 实盘接线（本 Wave 的独立步骤，在 L-a/L-b/L-c 合入后进行）
- 真实盘口价差（`SpreadExecution` 用 OHLCV 近似，Tick/QuoteBar 是 C3，Wave M+）
- 行业分类体系本身（C4，本期只接受注入）
