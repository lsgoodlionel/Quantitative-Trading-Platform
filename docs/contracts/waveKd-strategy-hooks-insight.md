# Wave K-d 契约：策略级止损/ROI + Alpha→Insight→Target→Execution 三段式

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **K4 / K5** · Agent-Kd
> 依赖：K-c（组合引擎）先合入
> 状态：📋 待审阅
>
> **K5 是本次升级的核心抽象** — 它同时解决 V3 记录的两条断链：
> 「因子挖掘→策略是死胡同」与「组合优化→下单只有一个 `<Link to="/orders">`」。

---

## 一、K4 策略级止损 / ROI / 追踪止损

### 1.1 现状

`app/strategy/` 下 grep `stoploss|trailing|minimal_roi` **零命中**。
16 个 preset 全靠信号反转平仓（如 `macd.py` 的 `crossunder` → `sell_all()`），**没有任何风险闸门**。

### 1.2 设计（接口参考 freqtrade IStrategy — **GPL-3.0，仅读设计，独立实现**）

```python
class StrategyBase:
    # ── 类级声明式配置（不配置 = 现状行为）──────────────────
    stoploss: float | None = None           # -0.10 = 亏 10% 止损
    trailing_stop: bool = False
    trailing_stop_positive: float | None = None        # 盈利后改用的追踪距离
    trailing_stop_positive_offset: float = 0.0         # 盈利达到此值才启用追踪
    minimal_roi: dict[int, float] | None = None        # {持仓分钟数: 目标收益率}
                                                       # {0: 0.10, 60: 0.05, 120: 0}

    # ── 运行期钩子（默认返回 None = 用类级配置）──────────────
    def custom_stoploss(self, ctx, trade, current_profit: float) -> float | None: ...
    def custom_roi(self, ctx, trade, current_profit: float) -> float | None: ...
```

### 1.3 引擎接入顺序（每个时点，**在 `on_bar` 之前**）

```
1. broker.advance_day()            # 现状
2. broker.process_bar(bar)         # 现状：撮合上一 bar 挂单
3. ★ check_exit_conditions()       # 新增：ROI → 止损 → 追踪止损，命中则立即挂平仓单
4. strategy.on_bar(ctx)            # 现状
```

**为什么在 `on_bar` 之前**：风险闸门必须先于策略意图执行，否则策略可能在已该止损的仓位上继续加仓。

**平仓单的 `exit_reason`** 分别写 `"roi"` / `"stop_loss"` / `"trailing_stop_loss"`，
直接进现有 `tag_metrics.py` 的分组统计，无需额外改动。

### 1.4 `Trade` 概念（新增）

现有代码只有 `Position`（聚合持仓），没有「一笔交易」的概念，无法算「本笔持仓多久 / 本笔浮盈多少」。

```python
# app/engine/backtest/trade.py（新文件）
@dataclass
class Trade:
    symbol: str; direction: Literal["long","short"]
    open_time: datetime; open_price: float; qty: int
    entry_tag: str | None
    max_profit_seen: float = 0.0     # 追踪止损用
    min_profit_seen: float = 0.0

    def current_profit(self, price: float) -> float: ...   # 带方向的收益率
    def duration_minutes(self, now: datetime) -> int: ...
```

`SimulatedBroker` 维护 `open_trades: dict[str, Trade]`，与 `Position` 并存（Position 管成本，Trade 管本笔状态）。

### 1.5 回归基线保证

`stoploss=None` 且 `trailing_stop=False` 且 `minimal_roi=None` 时，第 3 步是 no-op → **144 用例逐笔不变**。
16 个 preset **本期一律不配置**这些字段（配置属于行为变更，另开 PR 并重新生成基线）。

---

## 二、K5 Alpha → Insight → PortfolioTarget → Execution

### 2.1 为什么需要这层抽象

| V3 记录的断链 | 根因 | 本抽象如何解决 |
|--------------|------|--------------|
| 因子挖掘结果只存 Redis 实验记录，无法交易 | 因子产出的是「打分」，策略要的是「下单」，中间没有承接物 | 因子打分 → `Insight`（方向+置信度），由组合构建模型转成仓位 |
| 组合优化算出权重后只能手抄下单 | 权重是「目标状态」，OMS 要的是「增量订单」 | `PortfolioTarget` + `ExecutionModel` 负责 diff 与拆单 |
| 16 个 preset 与 ML 策略、Topk 各走各的路 | 没有统一的信号中间表示 | 三者都产 `Insight` |

### 2.2 数据结构（参考 Lean `Algorithm.Framework`，Apache-2.0，Python 源码可直接翻译）

```python
# app/engine/framework/insight.py

class InsightDirection(int, Enum):
    DOWN = -1; FLAT = 0; UP = 1

@dataclass(frozen=True)
class Insight:
    symbol: str
    direction: InsightDirection
    period: timedelta                    # 观点有效期
    generated_at: datetime
    magnitude: float | None = None       # 预期收益率
    confidence: float | None = None      # 0..1
    weight: float | None = None          # 建议权重（因子打分可直接给）
    source: str = ""                     # 产出者名，用于归因
    tag: str = ""
    # ── 以下两个字段是 2026-08-07 核对 Lean 源码后补入，理由见下 ──
    insight_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    group_id: str | None = None          # 同组观点必须原子执行（配对交易的多空两腿）

    @property
    def is_expired(self) -> bool: ...


def group_insights(*insights: Insight) -> list[Insight]:
    """把若干 Insight 标成同一组，返回带 group_id 的新实例（frozen，不就地改）。

    已属于其他组的 Insight 必须报错，不能静默改组 —— 否则配对关系会被悄悄拆散。
    """
```

> **为什么必须现在就加这两个字段**（核对 Lean `Common/Algorithm/Framework/Alphas/Insight.cs` 后补记）：
>
> Lean 的 `Insight` 有 17 个字段，本契约初稿只取了 10 个。多数省略是合理的（`Score` /
> `ReferenceValue` / `EstimatedValue` / `InsightType` 都服务于 N4 归因，本期确实不做），
> 但有两个不能等：
>
> 1. **`group_id` —— 缺了会导致配对策略出错，不是少个功能而已。**
>    Lean 的 `BasePairsTradingAlphaModel` 靠 `Insight.Group()` 把多空两腿绑在一起。
>    没有它，组合构建模型可能执行了多头腿、却因权重约束丢掉空头腿，
>    留下一条**裸露的单边敞口** —— 这与策略意图正好相反。
>    只要框架里会出现配对/价差类 Alpha（V4 的 M 系列已规划），这个字段就是必需的。
> 2. **`insight_id` —— 现在加是一行，将来加是一次数据迁移。**
>    N4（进出场归因）要把成交回溯到产生它的那条观点。本期即使不做归因，
>    也应让每条 Insight 从诞生起就有稳定标识，否则历史数据永远补不回来。
>
> **仍然明确不做**：`Score`（方向/幅度的事后打分）、`ReferenceValue(Final)`、
> `EstimatedValue`、`InsightType`(Price/Volatility)、`CloseTimeUtc`（本期用
> `generated_at + period` 推导有效期即可；Lean 额外提供绝对结束时刻，是为了表达
> 「到月末为止」这类跨日历的有效期，本期用不到）。

```python
# app/engine/framework/target.py
@dataclass(frozen=True)
class PortfolioTarget:
    symbol: str
    quantity: int                        # 目标持仓数量（绝对值，非增量）
    tag: str = ""
```

### 2.3 四个基类

```python
# app/engine/framework/alpha.py
class AlphaModel(ABC):
    name: str
    @abstractmethod
    def update(self, ctx: PortfolioContext) -> list[Insight]: ...
    def on_symbols_changed(self, added: list[str], removed: list[str]) -> None: ...

# app/engine/framework/portfolio_construction.py
class PortfolioConstructionModel(ABC):
    @abstractmethod
    def create_targets(self, ctx, insights: list[Insight]) -> list[PortfolioTarget]: ...
    def should_rebalance(self, now: datetime) -> bool: ...   # 再平衡节奏

# app/engine/framework/risk.py
class RiskManagementModel(ABC):
    @abstractmethod
    def manage_risk(self, ctx, targets: list[PortfolioTarget]) -> list[PortfolioTarget]: ...

# app/engine/framework/execution.py
class ExecutionModel(ABC):
    @abstractmethod
    def execute(self, ctx, targets: list[PortfolioTarget]) -> list[Order]: ...
```

### 2.4 本期实现的最小集合

| 类型 | 实现 | 说明 |
|------|------|------|
| Alpha | `LegacyStrategyAlphaAdapter` | **把现有 16 个 preset 原样包成 AlphaModel**（拦截其 buy/sell 调用转成 Insight），零重写 |
| Alpha | `FormulaFactorAlphaModel` | RPN 公式因子 → 按分位阈值出 Insight — **这就是 V3 的 G1** |
| Portfolio | `EqualWeightingPCM` | 等权，Lean 同名模型翻译 |
| Portfolio | `InsightWeightingPCM` | 按 `insight.weight` 加权 |
| Portfolio | `OptimizerPCM` | 桥接**现有** `engine/portfolio/optimizer.py` 的 8 种方法 |
| Risk | `NullRiskModel` | 直通（默认） |
| Execution | `ImmediateExecutionModel` | 目标 → 市价 diff 单（默认） |

> L2（5 个风控模型）与 L3（VWAP/Spread/StandardDeviation 执行模型）在 Wave L 补齐，本期只建抽象与最小实现。

### 2.5 编排

```python
class FrameworkStrategy(PortfolioStrategyBase):
    """把四个模型串成一个可跑的组合策略。"""
    def __init__(self, alpha, portfolio_construction, risk=None, execution=None): ...

    def on_bars(self, ctx: PortfolioContext) -> None:
        insights = self.alpha.update(ctx)
        self._active = [i for i in self._active + insights if not i.is_expired]
        if not self.pcm.should_rebalance(ctx.now):
            return
        targets = self.pcm.create_targets(ctx, self._active)
        targets = self.risk.manage_risk(ctx, targets)
        for order in self.execution.execute(ctx, targets):
            ctx.submit(order)
```

**同一个 `FrameworkStrategy` 必须能在回测与实盘跑**（DEVPLAN_V4 §七 验收）：
`PortfolioContext` 在实盘由 `StrategyEngine` 提供等价实现，`ctx.submit` 路由到 OMS。
这一点本期只需保证**接口可行**，实盘接线在 Wave L 完成。

---

## 三、验收

```
1. tests/regression 144 用例逐笔全绿（K4 三项默认不配置；K5 不影响旧路径）
2. test_strategy_stoploss.py:
   - 固定止损在跌破阈值的下一 bar 平仓，exit_reason="stop_loss"
   - minimal_roi 按持仓时长阶梯生效
   - trailing_stop 在 offset 之前不启用，之后跟随极值
   - custom_stoploss 返回值覆盖类级配置
3. test_framework.py:
   - LegacyStrategyAlphaAdapter 包装 macd preset 后，产出的 Insight 序列与原策略买卖点一一对应
   - EqualWeightingPCM: N 个 UP insight → 每个目标权重 1/N
   - OptimizerPCM 桥接 HRP 后产出的 targets 权重和为 1
   - ImmediateExecutionModel: 已有持仓 100 股、目标 150 股 → 产出买 50 股（而非买 150）
4. 端到端：FormulaFactorAlphaModel + OptimizerPCM(HRP) + ImmediateExecution
   在 5 标的上跑通组合回测并出 tearsheet
```

## 四、不做

- 实盘接线（Wave L）
- Universe Selection 模型（Lean 的第五个模块，V4 归入 F2/F3，Wave M）
- Insight 的历史归因分析（N4）
