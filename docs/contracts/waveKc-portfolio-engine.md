# Wave K-c 契约：多标的组合回测引擎

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **K1** · Agent-Kc
> 依赖：K-a（订单类型 + 做空）先合入
> 状态：📋 待审阅
>
> **这是 Wave K 破坏面最大的一项。红线：`tests/regression` 144 用例逐笔全绿。**

---

## 一、现状与目标

现状 `app/engine/backtest/engine.py:83`：

```python
def run(self, strategy: StrategyBase, bars: list[Bar], strategy_id: str = "backtest") -> BacktestResult
```

只接单一 symbol 的 bar 序列；`StrategyContext.history` 是单标的 DataFrame；
`SimulatedBroker` 虽然 `positions` 是 dict 但 `process_bar` 一次只喂一根 bar。

目标：新增组合引擎，**且把现有单标的引擎实现为它的特例包装**，而不是并行维护两套。

---

## 二、新增文件

```
app/engine/backtest/portfolio_engine.py    # 组合主循环
app/engine/backtest/portfolio_broker.py    # 多标的撮合与账户
app/engine/backtest/daily_result.py        # 逐合约/组合日度盈亏
```

## 三、核心接口

### 3.1 组合引擎

```python
@dataclass
class PortfolioBacktestConfig(BacktestConfig):
    # 继承 initial_cash / market / commission_model / slippage_model / warmup_bars
    # / allow_short / calendar / adjust_prices
    max_open_positions: int | None = None    # 同时持仓上限（None = 不限）
    cash_per_position: float | None = None   # 每仓固定金额（None = 由策略决定）


class PortfolioBacktestEngine:
    def run(
        self,
        strategy: PortfolioStrategyBase,
        bars_by_symbol: dict[str, list[Bar]],
        strategy_id: str = "portfolio-backtest",
    ) -> PortfolioBacktestResult: ...
```

### 3.2 时间轴对齐（关键设计）

各标的 bar 序列长度/日期不一致（停牌、上市时间不同）。参考 freqtrade `time_pair_generator`：

1. 取所有标的时间戳的**并集**排序，作为主时间轴
2. 每个时间点，只把**当前时点有 bar 的标的**放进 `ctx.bars`
3. 无 bar 的标的：持仓保留，估值用**最近一次收盘价**（`last_known_price`），不参与撮合

> ⚠️ 停牌标的的估值必须用最后已知价而非 0，否则组合净值会出现假暴跌。

### 3.3 组合策略基类

```python
class PortfolioStrategyBase(StrategyBase):
    def on_bars(self, ctx: PortfolioContext) -> None: ...   # 每个时点一次，而非每标的一次
```

`PortfolioContext` 相对 `StrategyContext` 的差异：

```python
bars:     dict[str, Bar]              # 当前时点各标的 bar（可能缺标的）
histories: dict[str, pd.DataFrame]    # 各标的截至当前的历史
symbols:  list[str]                   # 全体标的
def position(self, symbol) -> Position | None
def buy(self, symbol, qty, ...) / sell / short / cover
def target_weight(self, weights: dict[str, float]) -> list[Order]   # 目标权重 → diff 订单
```

`target_weight()` 是 K5（Insight 框架）与 V3 G2（一键调仓）的共同底座，**必须在本契约实现**。

### 3.4 单标的引擎变为特例（零破坏迁移）

```python
class BacktestEngine:
    """保留原签名与原返回类型；内部委托给组合引擎。"""
    def run(self, strategy, bars, strategy_id="backtest") -> BacktestResult:
        ...  # 用 _SingleSymbolAdapter 把 StrategyBase 包成 PortfolioStrategyBase
```

**迁移风险控制**：
- 若适配层无法做到逐笔一致（浮点累加顺序、撮合遍历顺序等），**保留旧实现不动**，两套并存，
  在 `DEVPLAN_V4` 中记为已知债务。**宁可并存，也不能让基线变红后去改基线。**
- 先写适配层 → 跑 `tests/regression` → 一致才删旧实现。

---

## 四、日度盈亏拆解（参考 vnpy，MIT）

来源 `refs/vnpy/vnpy/alpha/strategy/backtesting.py:799-935`：

```python
@dataclass
class ContractDailyResult:      # 单标的单日
    date: date; close_price: float; pre_close: float
    trades: list[Fill]; trade_count: int
    start_pos: int; end_pos: int
    turnover: float; commission: float; slippage: float
    trading_pnl: float; holding_pnl: float; total_pnl: float

@dataclass
class PortfolioDailyResult:     # 组合单日 = 各 ContractDailyResult 汇总
    ...同上字段的加总 + net_pnl（扣费后）
```

这套拆解直接支撑 V4 的 **N2 换手率**与 **N4 进出场归因**，本期先产出数据。

## 五、结果结构

```python
@dataclass
class PortfolioBacktestResult:
    strategy_name: str
    symbols: list[str]
    start_date / end_date / initial_cash / final_value
    metrics: BacktestMetrics                 # 组合级，复用现有 compute_metrics
    equity_curve: pd.Series
    fills: list[dict]
    daily_results: list[PortfolioDailyResult]
    per_symbol_metrics: dict[str, BacktestMetrics]   # 逐标的归因
    report: dict
```

---

## 六、性能要求

**50 标的 × 3 年日线（约 750 时点）< 30 秒**（DEVPLAN_V4 §七 验收标准）。

- `histories` **不可**每时点切片重建 DataFrame（现有单标的引擎的 `df_all.iloc[:i+1]` 在多标的下会是 O(N×M) 的灾难）
- 采用 numpy 数组 + 游标，只在策略真正访问时才物化 DataFrame（lazy view）
- 若无法达标，先保证正确性并在契约中记录实测数值，J2 向量化再优化

---

## 七、验收

```
1. tests/regression 144 用例逐笔全绿（单标的路径经适配层或旧实现）
2. test_portfolio_engine.py:
   - 3 标的等权买入持有 → 组合净值 = 各标的净值加权和
   - 停牌标的（中间缺 bar）估值用最后已知价，不产生假回撤
   - target_weight() 产出的 diff 订单数量与方向正确（含已有持仓的增减）
   - max_open_positions 生效
   - 现金不足时按序拒单而非透支
3. test_daily_result.py：trading_pnl + holding_pnl == total_pnl；扣费后 == net_pnl
4. 性能：50×750 用例计时并记录在测试输出中
```

## 八、不做

- 多币种/汇率（Lean 有 CashBook，本期单币种）
- 跨市场组合（US+HK+A 混合回测）— 需先有 K7 日历的跨市场对齐，本期单市场
- 分钟级组合回测
