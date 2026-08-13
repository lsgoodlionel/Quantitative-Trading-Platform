# Wave K-b 契约：成交量约束滑点 + 交易日历 + 复权

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **K6 / K7 / K8** · Agent-Kb
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression` 144 用例必须全绿。三项能力**默认关闭/无感**。

---

## 一、K6 成交量约束与市场冲击滑点

### 1.1 现状

`app/engine/backtest/slippage.py` 已有 `SlippageModel` 抽象与 `FixedSlippage / VolumeSlippage / NoSlippage`。
问题：**滑点只调价格，不限制成交数量** — 一笔 100 万股的单子在日成交量 1 万股的 bar 上也会全额成交。

### 1.2 新增：成交量上限

```python
# app/engine/backtest/slippage.py

@dataclass
class FillLimit:
    """一次撮合允许成交的最大数量。"""
    max_qty: int
    reason: str | None = None   # 被截断时写入 Order.reject_reason 供诊断


class SlippageModel(ABC):
    def apply(self, price, direction, bar) -> float: ...          # 现有，不变

    def fill_limit(self, order_qty: int, bar: Bar) -> FillLimit:  # 新增，默认不限制
        return FillLimit(max_qty=order_qty)
```

**默认实现返回不限制** → 现有三个模型行为不变 → 回归基线全绿。

### 1.3 新增两个模型（移植自 zipline，Apache-2.0）

| 模型 | 来源 | 语义 |
|------|------|------|
| `VolumeShareSlippage` | `refs/zipline-LHJY/zipline/finance/slippage.py:247` | 单 bar 最多成交 `volume_limit × bar.volume`（默认 2.5%）；滑点 = `price_impact × (成交占比)²` |
| `MarketImpactSlippage` | 同上 `:377,524` | 冲击 ∝ `sqrt(成交占比)`，更贴近大单实际 |

### 1.4 部分成交

`process_bar` 的 `_try_fill` 在 `fill_limit.max_qty < order.qty` 时：

- 产生一笔数量为 `max_qty` 的 Fill
- **订单保留在挂单队列**，`qty` 减去已成交部分，`status = PARTIAL`
- 需新增 `OrderStatus.PARTIAL`（OMS 侧 `LiveOrderStatus.PARTIAL` 已存在，此处对齐命名）

⚠️ 这会让「一根 bar 一笔 Fill」的隐含假设失效，`roundtrips.py` / `metrics.py` 需能处理同一 order_id 的多笔 Fill。

---

## 二、K7 交易日历

### 2.1 现状

全库 grep `trading_calendar|market_calendar|exchange_calendar` **零命中**。
`BacktestEngine.run` 直接 `for i, bar in enumerate(bars)` 顺序迭代，日历完全由数据源隐式决定。

### 2.2 设计：日历是**校验与对齐层**，不是驱动层

```python
# app/engine/calendar/base.py（新目录）

class TradingCalendar(ABC):
    name: str
    def is_session(self, d: date) -> bool: ...
    def sessions_in_range(self, start: date, end: date) -> list[date]: ...
    def next_session(self, d: date) -> date: ...
    def sessions_count(self, start: date, end: date) -> int: ...

# app/engine/calendar/{us,hk,a_share}.py
#   US: NYSE 假日表 + 半日市
#   HK: 港交所假日表 + 午休（12:00-13:00，午市 13:00 开市 / 16:00 收市）
#   A : 上交所假日表 + 午休（11:30-13:00）+ 春节/国庆长假
```

**数据来源**：假日表用静态表（`app/engine/calendar/holidays/*.py`），不引新依赖（`exchange_calendars` 是重依赖，违反 HANDOFF §3.6）。
A 股假日表可从已有的 `data/providers/akshare_calendar_provider.py` 拉取后固化。

### 2.3 接入点（**必须是可选的**）

```python
class BacktestConfig:
    calendar: TradingCalendar | None = None   # None = 现状：按 bar 顺序，不做日历校验
```

`calendar` 非空时，引擎在 `run()` 开头做三件事，**都不改变撮合逻辑**：

1. **校验**：bar 序列中出现非交易日 → 记 warning（不抛错，数据源脏数据很常见）
2. **补缺**：交易日历中有但 bar 序列缺失的日期 → 记入 `BacktestResult.report["data_gaps"]`
3. **年化基准**：`trading_days_per_year` 改用日历实测值，替代现有的 `TRADING_DAYS_US/HK/A` 常数

> 第 3 点会改变指标数值 → **必须在 `calendar=None` 时保持原常数**，否则回归基线红。

---

## 三、K8 复权与公司行为

### 3.1 现状

只有 `endpoints/calendar.py` 的分红**日历展示**端点；回测取到什么价就用什么价。
后果：跨除权日的回测会在除权当天出现虚假暴跌，所有趋势策略被误触发。

### 3.2 设计

```python
# app/data/adjustments.py（新文件，参考 zipline data/adjustments.py，Apache-2.0）

@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: date
    kind: Literal["split", "dividend", "merger"]
    ratio: float | None = None    # split: 1拆N 则 ratio=N
    amount: float | None = None   # dividend: 每股现金分红

def build_adjustment_factors(
    actions: list[CorporateAction], sessions: list[date]
) -> pd.Series: ...   # 逐日累乘的复权因子（口径见 §3.3）

def apply_adjustments(bars: list[Bar], factors: pd.Series) -> list[Bar]: ...
```

### 3.3 接入点

```python
class BacktestConfig:
    adjust_prices: bool = False   # 默认 False = 现状（用原始价）
```

- `True` 时在 `run()` 载入 bars 后、撮合前统一调整 OHLC 与 volume
- 采用**前复权（qfq）**：以最新价为基准回调历史价，保证 **最新价 = 真实价**，便于与实盘对账。

  > ⚠️ 本契约初稿此处写的是「后复权」，与同句的「最新价 = 真实价」自相矛盾 ——
  > 后者才是前复权的定义（后复权是以最早价为基准、把最新价往上推）。
  > **以「最新价 = 真实价」这个语义要求为准**，术语已更正。
- 分红同时产生**现金流入**：`broker.add_cash(qty × amount)` 于除权日

### 3.4 数据来源

优先级：`yfinance`（`Ticker.actions` 已含 splits/dividends）→ `AkShare`（A 股 `stock_zh_a_daily(adjust="qfq")` 直接给前复权）→ 无则跳过并 warning。
**不新增依赖**：两个 provider 都已在 `data/providers/` 中。

---

## 四、验收

```
1. tests/regression 144 用例全绿（三项默认关闭）
2. test_volume_slippage.py：单 bar 成交量上限、部分成交后挂单残留、同 order_id 多笔 Fill 的回合配对
3. test_calendar.py：三市场 2024 全年交易日数与公开数据一致；午休时段判定；数据缺口检出
4. test_adjustments.py：已知拆股案例（如 AAPL 2020-08-31 1拆4）复权后价格连续；分红现金流入账
5. calendar=None / adjust_prices=False 时，逐笔结果与基线完全一致
```

## 五、不做

- 分钟级 session/minute 映射（zipline 有，本期只做日线日历）
- 连续期货合约的滚动调整（O5 范围）
- 合并/分拆重组（`kind="merger"` 先留字段不实现）
