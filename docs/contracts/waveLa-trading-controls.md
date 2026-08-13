# Wave L-a 契约：TradingControl 控制族 + LimitIfTouched 补漏

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **L1**，外加 Wave K 遗留的 `LIMIT_IF_TOUCHED` · Agent-La
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例 / 1364 笔逐笔成交）必须全绿。
> 新控制器**默认一个都不装**，`controls=[]` 时行为与今天逐笔一致。

---

## 一、现状（实测）

**回测侧**：无任何交易控制。`SimulatedBroker.submit_order` 只做三件事 ——
qty>0 校验、订单规格校验（`validate_order_spec`）、非做空时的持仓/T+1 校验。

**实盘侧**：`app/oms/manager.py:259 _pre_trade_risk_check` 是**单点硬编码**检查，
共 4 条规则（qty>0 · qty ≤ `MAX_ORDER_QTY` · LIMIT 需 limit_price · limit_price>0），
写死在方法体里，不可配置、不可组合、不可复用。

后果：回测能跑通的策略，实盘可能被 OMS 拒；反之实盘的限制在回测里完全体现不出来。
**L1 的核心价值不是「多几个控制器」，而是让回测与实盘共用同一套控制器。**

---

## 二、控制器抽象

参考 `refs/zipline-LHJY/zipline/finance/controls.py`（Apache-2.0，**可直接复制并保留版权头**）。
zipline 有 8 个类：`MaxOrderCount` / `RestrictedListOrder` / `MaxOrderSize` / `MaxPositionSize` /
`LongOnly` / `AssetDateBounds` + `AccountControl` 基类下的 `MaxLeverage` / `MinLeverage`。

```python
# app/engine/controls/base.py（新目录）

@dataclass(frozen=True)
class ControlViolation:
    control: str          # 控制器名，用于日志与拒单原因
    reason: str           # 面向用户的中文说明

class TradingControl(ABC):
    """下单前校验。返回 None 表示放行，返回 ControlViolation 表示拦截。"""

    @abstractmethod
    def validate(self, ctx: ControlContext) -> ControlViolation | None: ...

class AccountControl(ABC):
    """账户级校验，在每次成交后检查（杠杆类约束事后才能判定）。"""

    @abstractmethod
    def validate(self, ctx: ControlContext) -> ControlViolation | None: ...
```

`ControlContext` 是**回测与实盘的公共视图**，这是共用同一套控制器的关键：

```python
@dataclass(frozen=True)
class ControlContext:
    symbol: str
    market: Market
    side: str                      # "BUY" / "SELL"
    qty: int
    order_type: OrderType
    limit_price: float | None
    price: float | None            # 参考价（回测用 bar.close，实盘用最新报价）
    now: datetime
    current_qty: int               # 该标的当前持仓（带符号）
    portfolio_value: float
    cash: float
    orders_today: int              # 当日已下单数
    leverage: float                # 当前杠杆
```

> ⚠️ **不要让控制器直接持有 broker 或 OMS 引用**。一旦持有，回测与实盘就必须各写一套。
> 全部所需信息由 `ControlContext` 传入，控制器保持纯函数式。

## 三、本期实现的 8 个控制器

| 控制器 | 参数 | 语义 |
|---|---|---|
| `MaxOrderCount` | `max_count`, `on_error` | 单日下单数上限 |
| `MaxOrderSize` | `max_shares`, `max_notional` | 单笔数量/金额上限 |
| `MaxPositionSize` | `max_shares`, `max_notional` | 单标的持仓上限（含本单成交后） |
| `LongOnly` | — | 禁止净持仓为负。**K2 做空的对立面：做空能力保留，是否允许由它决定** |
| `RestrictedList` | `restricted: set[str]` | 黑名单标的 |
| `AssetDateBounds` | `bounds: dict[str, tuple[date, date]]` | 标的上市/退市边界外拒单 |
| `MaxLeverage` | `max_leverage` | 账户级，成交后校验 |
| `MinLeverage` | `min_leverage`, `deadline` | 账户级，指定时点前须达到最低杠杆 |

`on_error` 语义（对齐 zipline）：`"fail"` 抛错中断回测 / `"log"` 记警告并拒单。**默认 `"log"`**
—— 回测中途因一次超限而整体崩掉，比拒掉这一单更糟。

## 四、接入点

### 4.1 回测

```python
class BacktestConfig:
    controls: list[TradingControl] = field(default_factory=list)        # 默认空 = 现状
    account_controls: list[AccountControl] = field(default_factory=list)
```

`SimulatedBroker.submit_order` 在**现有校验之后**逐个跑 `controls`；命中则
`status=REJECTED`、`reject_reason=f"[{violation.control}] {violation.reason}"`。
`account_controls` 在 `process_bar` 产生 Fill 之后检查。

### 4.2 实盘

`_pre_trade_risk_check` 的 4 条硬编码规则**保留不动**（它们是参数合法性校验，不是策略约束），
在其后追加同一套 `controls` 的执行。`OrderManager.__init__` 增加 `controls` 参数，默认空列表。

> **务必确认**：同一个 `MaxPositionSize(max_notional=50_000)` 实例，
> 在回测与实盘必须给出相同判定。契约验收里有专门用例。

---

## 五、LimitIfTouched（Wave K 契约疏漏补做）

`waveKa` 契约漏了这个订单类型，且「不做」清单也没排除它，详见
[DEVPLAN_V4.md 零章交叉核对](../../DEVPLAN_V4.md)。

它与 `STOP_LIMIT` **互为镜像**：

| | 触发条件（BUY） | 触发条件（SELL） | 用途 |
|---|---|---|---|
| `STOP_LIMIT` | `bar.high >= stop_price` | `bar.low <= stop_price` | 突破追单 |
| `LIMIT_IF_TOUCHED` | `bar.low <= trigger_price` | `bar.high >= trigger_price` | 回落抄底 / 反弹摸顶 |

实现：`OrderType` 增加 `LIMIT_IF_TOUCHED`；`Order` 增加 `trigger_price: float | None`；
`match_order()` 里复用 STOP_LIMIT 已有的「触发 → 转限价」两段结构（`order._triggered` 状态机
原样可用），只反转触发方向的比较符。`validate_order_spec` 要求同时给 `trigger_price` 与 `limit_price`。

**成交价与 LIMIT 一致**：取对下单方更不利的一侧（`min(bar.open, limit_price)` / `max(...)`），
不得用触发价直接成交。

---

## 六、验收

```
1. tests/regression 146 用例全绿（controls 默认空，LIMIT_IF_TOUCHED 不影响既有类型）
2. tests/test_trading_controls.py:
   - 8 个控制器各自的放行/拦截边界
   - on_error="fail" 抛错、"log" 拒单且回测继续
   - 多控制器组合时按顺序短路，拒单原因带控制器名
   - LongOnly 在 allow_short=True 时仍能拦住开空（两者正交，不是互斥开关）
3. tests/test_controls_parity.py ★ 本契约的核心用例：
   - 同一个控制器实例 + 等价的 ControlContext，回测路径与 OMS 路径判定必须一致
   - 至少覆盖 MaxOrderSize / MaxPositionSize / LongOnly / RestrictedList
4. tests/test_order_types.py 扩充：LIMIT_IF_TOUCHED 的 BUY/SELL × 触发/未触发 × 成交价
5. ruff check app tests → All checks passed!
6. pytest -q 全绿（当前 1033 passed，覆盖率门禁 61）
```

## 七、不做

- 保证金/买入力模型（A7，Lean `BuyingPowerModel`）—— `MaxLeverage` 只做**事后校验**，不做事前拦截
- 控制器的 API 配置端点（本期只到引擎层，前端配置留给后续 Wave）
- 组合单（Combo/Leg）、期权行权单（`OptionExercise`）—— 仍在 Wave K 的「不做」清单内
