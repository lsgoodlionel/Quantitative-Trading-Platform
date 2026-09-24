# Wave K-a 契约：订单类型体系 + 做空与双向持仓

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **K3**（订单类型）与 **K2**（做空）· Agent-Ka
> 状态：📋 待审阅
>
> **红线**：本契约的任何实现都必须让 `backend/tests/regression`（144 用例 / 1364 笔成交）保持全绿。
> 所有新增能力必须**默认关闭**，即「不配置时行为与今天逐笔一致」。

---

## 一、现状（实测）

`app/engine/backtest/broker.py`：

- `Order` 字段：`symbol / market / side / qty / order_id / status / created_at / filled_price / filled_at / commission / reject_reason / entry_tag / exit_reason`
  — **没有 `order_type`**，`process_bar` 无条件按 next-bar open 撮合，即全部是市价单。
- `OrderSide` 只有 `BUY / SELL`；`submit_order` 对 SELL 校验 `qty > position.qty` 即拒单 → **无法做空**。
- `Position`（`position.py`）用 FIFO `deque[_Lot]` 记多头成本，`qty` 恒为非负和。

---

## 二、K3 订单类型体系

### 2.1 新增枚举

```python
# app/engine/backtest/order_types.py（新文件）

class OrderType(str, Enum):
    MARKET          = "MARKET"           # 现状行为：下一根 bar 开盘价成交
    LIMIT           = "LIMIT"            # 价格触及 limit_price 才成交
    STOP_MARKET     = "STOP_MARKET"      # 触发后转市价
    STOP_LIMIT      = "STOP_LIMIT"       # 触发后转限价
    TRAILING_STOP   = "TRAILING_STOP"    # 跟随极值回撤触发
    MARKET_ON_OPEN  = "MARKET_ON_OPEN"   # 显式开盘价（= 现状 MARKET，语义更清晰）
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"  # 当根 bar 收盘价成交

class TimeInForce(str, Enum):
    GTC = "GTC"   # 一直有效（默认，= 现状挂单不过期行为）
    DAY = "DAY"   # 当日有效，收盘未成交则撤单
    GTD = "GTD"   # 指定日期前有效
```

### 2.2 `Order` 扩展（**全部带默认值，向后兼容**）

```python
order_type:     OrderType   = OrderType.MARKET
limit_price:    float | None = None
stop_price:     float | None = None
trailing_pct:   float | None = None   # 0.05 = 从极值回撤 5% 触发
time_in_force:  TimeInForce = TimeInForce.GTC
good_till_date: datetime | None = None
# 运行期状态（不由调用方设置）
_triggered:     bool = False          # STOP 类是否已触发
_extreme_price: float | None = None   # TRAILING_STOP 跟踪的极值
```

### 2.3 撮合规则（`_try_fill` 按类型分派）

| 类型 | 触发/成交判定（用当根 bar 的 OHLC） | 成交价 |
|------|-----------------------------------|--------|
| MARKET / MARKET_ON_OPEN | 无条件 | `bar.open`（经滑点） |
| MARKET_ON_CLOSE | 无条件 | `bar.close`（经滑点） |
| LIMIT | BUY: `bar.low <= limit_price`；SELL: `bar.high >= limit_price` | `min(bar.open, limit_price)` / `max(bar.open, limit_price)` — **保守取对下单方更不利的一侧** |
| STOP_MARKET | BUY: `bar.high >= stop_price`；SELL: `bar.low <= stop_price` | `max(bar.open, stop_price)` / `min(bar.open, stop_price)` |
| STOP_LIMIT | 先按 STOP 判定触发，`_triggered=True` 后当作 LIMIT 处理（**同一根 bar 内可连续触发+成交**） | 同 LIMIT |
| TRAILING_STOP | 每 bar 更新 `_extreme_price`（多头取 high 的历史最大）；`bar.low <= _extreme_price * (1-trailing_pct)` 触发 | 触发价（经滑点） |

**防未来函数**：所有判定只用「订单挂出之后」的 bar，且成交价一律取对下单方更不利的一侧。
禁止用 `bar.close` 判定 LIMIT/STOP 是否触发再用 `bar.open` 成交（那是偷看）。

### 2.4 TimeInForce 处理

`process_bar` 末尾统一过期检查：`DAY` 单在 bar 日期变更时撤单，`GTD` 单在 `bar.time.date() > good_till_date.date()` 时撤单。
撤单产生 `OrderStatus.CANCELLED`，**不产生 Fill**。

---

## 三、K2 做空与双向持仓

### 3.1 `OrderSide` 扩展

```python
class OrderSide(str, Enum):
    BUY   = "BUY"     # 多头开仓 / 空头平仓
    SELL  = "SELL"    # 多头平仓 / 空头开仓
```

保持两值不变，**方向由持仓状态推导**（避免 4 值枚举带来的现有代码全面改写）：

- 持仓为 0 或正 → `SELL` 超过持仓的部分即为**开空**（需 `allow_short=True`）
- 持仓为负 → `BUY` 为**平空**，超出部分转为开多

### 3.2 `Position` 支持负持仓

- `_lots` 拆为 `_long_lots` 与 `_short_lots` 两个 FIFO 队列
- `qty` = `long_qty - short_qty`（可为负）
- 新增 `direction: Literal["long", "short", "flat"]` 属性
- `avg_cost` 按当前方向的队列计算
- **A股 T+1 与做空互斥**：`Market.A` 下 `allow_short` 强制为 False（融券不在本期范围），
  且现有 `closable_qty` / `advance_day` 语义不变

### 3.3 `SimulatedBroker` 开关

```python
class BacktestConfig:
    allow_short: bool = False        # 默认关闭 = 现状行为
    short_borrow_rate: float = 0.0   # 年化融券费率，按持仓天数计提
```

`allow_short=False` 时，`submit_order` 的 SELL 校验逻辑**与今天完全一致**（这是回归基线全绿的保证）。

### 3.4 `StrategyContext` 新增方法

```python
def short(self, qty: int, symbol=None, ...) -> Order | None   # 开空
def cover(self, qty: int, symbol=None, ...) -> Order | None   # 平空
def close_all(self, symbol=None) -> Order | None              # 平掉任意方向（sell_all 的双向版）
```

`sell_all()` **保持原语义不变**（只平多头），避免影响现有 16 个 preset。

---

## 四、组合价值与指标的连带影响

| 位置 | 影响 | 处理 |
|------|------|------|
| `portfolio_value(prices)` | 空头市值为负 | `total_market_value` 按带符号 qty 计算 |
| `metrics.compute_metrics` | `total_trades` 现按「卖出方向成交」计数，做空后会重复计数 | 改为按**平仓事件**计数（`realized_pnl` 非 None 的 fill） |
| `roundtrips.py` | 回合配对假设 buy→sell | 增加 short→cover 配对分支 |
| `_fill_to_dict` | 已有 `direction` 字段但恒为 `"long"` | 填真实方向 |

---

## 五、验收

```
1. tests/regression 144 用例全绿（allow_short 默认 False，逐笔一致）
2. 新增 tests/test_order_types.py：每种 OrderType × (触发/未触发) × (BUY/SELL) 的成交价断言
3. 新增 tests/test_short_selling.py：开空→加空→部分平→全平的 FIFO 成本与已实现盈亏
4. 新增 tests/test_time_in_force.py：DAY/GTD 过期撤单
5. A 股市场下 allow_short=True 必须抛出明确错误（而非静默做空）
```

## 六、不做（本期范围外）

- 保证金/杠杆账户模型（K7 之后的 A7，Lean `BuyingPowerModel`）
- 组合单（Combo/Leg）、期权行权单
- 融券可借券源与费率曲线（`short_borrow_rate` 先用常数）

### ⚠️ 本契约的已知疏漏（2026-08-07 全量核对 Lean 源码后补记）

**`LimitIfTouched`（LIT）本该在 §2.1 的 `OrderType` 里，写契约时漏了。**

Lean 共 12 种订单类型，`DEVPLAN_V4.md` §1.4 的详表列全了，但转写进本契约时把 LIT 丢了，
而上面的「不做」清单也只排除了 Combo 与 OptionExercise —— 所以它既没实现、也没被显式排除。

LIT 与 `STOP_LIMIT` 互为镜像：

| | 触发条件（BUY） | 典型用途 |
|---|---|---|
| `STOP_LIMIT` | 价格**上破** stop_price | 突破追多 |
| `LIMIT_IF_TOUCHED` | 价格**下探**到 trigger_price | 回落抄底 |

实现成本低：复用 STOP_LIMIT 已有的「触发 → 转限价」两段结构，只需反转触发方向的比较符。
**已记入 Wave L 待办**，见 [DEVPLAN_V4.md 零章交叉核对](../../DEVPLAN_V4.md)。
