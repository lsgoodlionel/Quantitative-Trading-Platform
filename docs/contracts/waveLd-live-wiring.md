# Wave L-d 契约：实盘接线 —— 同一份策略代码跑回测与实盘

> 对应 Wave K-d 与 Wave L 契约中反复推迟的「实盘接线」· Agent-Ld
> 依赖：K-d（`FrameworkStrategy`）· L-a（控制器）· L-b（风控/执行模型）· L-c（钩子）**全部已合入**
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例 / 1364 笔逐笔成交）必须全绿，
> 且**绝不允许**修改 `tests/regression/` 下任何文件。

---

## 一、这不是「接根线」，先看清现状

`DEVPLAN_V4` 的验收标准写的是「**同一份代码在回测与模拟盘跑通**」。
实测下来，实盘侧与这个目标有四道结构性差距：

| # | 现状（实测） | 与目标的差距 |
|---|---|---|
| 1 | `StrategyEngine.start_strategy(symbol: str)`、`_run_loop` 只处理 `inst.symbol` 一个标的 | `FrameworkStrategy` 是**组合策略**，`on_bars` 接收多标的 |
| 2 | `LiveOrderContext` 只有 `buy(qty)` / `sell(qty)`，把请求攒成 dict | `PortfolioContext` 有 20+ 方法：`position` / `portfolio_value` / `target_weight` / `submit` / `close_all` / `history` … |
| 3 | 账户状态要 `await oms.get_account()` / `await oms.get_positions()` | `PortfolioContext` 的 `cash` / `portfolio_value` / `position()` 都是**同步**的 |
| 4 | `PaperBroker` / `PaperPortfolio`（`engine.py:225-350`）是**另一套简化实现**，与回测引擎无任何共享 | 启动时的 60 天纸面模拟与真实回测结果不可比 |

**本契约只解决 1–3。第 4 项单列在「不做」，理由见 §六。**

---

## 二、核心设计：同步采集 + 异步冲刷

`PortfolioContext` 是同步接口，OMS 是异步的。**不要把 `PortfolioContext` 改成 async**
—— 那会让回测引擎也被迫 async，代价远大于收益，且回测根本不需要。

正确做法是沿用 `LiveOrderContext` 已经在用的模式，把它补全：

```
每个时点：
  1. ★ await _snapshot_account()      异步拉账户/持仓，冻结成同步可读的快照
  2. 构造 LivePortfolioContext(快照, 本时点 bars, 历史)
  3. strategy.on_bars(ctx)            ← 同步，与回测**完全同一份代码**
     ctx.buy/sell/target_weight/...   ← 只记录意图，返回 PENDING 的 Order
  4. ★ await _flush_orders()          把收集到的 Order 逐个送进 OMS
```

`ctx.buy()` 返回的 `Order` 是本地对象、`status=PENDING`、`order_id` 为本地 id；
真实的 broker order id 在冲刷后回填。**这一点必须在 docstring 写明** ——
策略拿到 Order 后立刻读 `filled_price` 在实盘永远是 None，与回测的 next-bar 语义一致。

### 2.1 账户快照

```python
@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    portfolio_value: float
    positions: dict[str, int]          # symbol → 带符号持仓
    prices: dict[str, float]           # symbol → 最新价
    taken_at: datetime
```

拉取失败时**不得静默用 0**（那会让 `target_weight` 算出全仓清空）。
失败应：记 error → **跳过本时点**（不调用 `on_bars`）→ 下一时点重试。
连续失败 N 次（建议 3）把实例置为 `StrategyState.ERROR`。

> ⚠️ 这是本契约最容易埋雷的地方：账户拉取失败在实盘是常态（限流、断连），
> 而 `portfolio_value=0` 会让所有目标权重算成 0 股 —— 即**全部清仓**。

---

## 三、多标的实盘循环

`data_service.subscribe_bars(symbols: list[str], ...)` **已经接受列表**，可直接用。

问题是实盘各标的的 bar **异步到达**，没有回测那样的对齐时间轴。
需要一个聚合窗口：

```python
#: 同一时点的 bar 聚合等待窗口。超时后用已收到的 bar 触发一次 on_bars，
#: 缺失标的按「停牌」处理（复用回测的最后已知价估值语义）
BAR_AGGREGATION_WINDOW_SECONDS = 5
```

规则：
1. 收到某标的 bar → 记入当前时点桶（按 bar.time 归一到频率边界）
2. 桶内标的集合 == 订阅集合 → **立即**触发
3. 否则等到窗口超时 → 用已有的触发，缺失标的沿用最后已知价

**不要无限等**：某个标的停牌或数据源掉线会让整个策略永久卡住。

---

## 四、接口实现

```python
# app/strategy/live_context.py（新文件）

class LivePortfolioContext(PortfolioContext):
    """实盘版组合上下文。与回测版**接口完全一致**，实现换成快照 + 订单收集。"""
```

必须实现 `PortfolioContext` 的**全部**公开方法。逐一对应：

| 方法 | 实盘实现 |
|---|---|
| `cash` / `portfolio_value` | 读快照 |
| `position(symbol)` / `qty(symbol)` | 由快照 `positions` 构造 `Position`（只需 qty 与 avg_cost） |
| `current_prices` / `price(symbol)` | 读快照 `prices` |
| `bar(symbol)` | 本时点桶 |
| `history(symbol)` / `close_series` / `volume_series` | 各标的滚动历史 DataFrame |
| `buy/sell/short/cover/submit/buy_value/close_all` | 构造本地 `Order` 并收集，返回该 Order |
| `market_of(symbol)` | 实例配置 |
| `target_weight(weights)` | **直接复用回测版实现** |

> ### ⚠️ 本节初稿的一处事实错误（2026-08-11 实现后更正）
>
> 初稿断言「`target_weight` 只依赖 `portfolio_value`/`price`/`qty`/`submit`，不碰 broker」。
> **对 `target_weight` 本身成立，但对调用它的 PCM 不成立**：
> `EqualWeightingPCM._liquidations`（`portfolio_construction.py:114`）直接读
> `ctx.broker.positions.open_symbols`。实现时的严格 facade 当场把它抓了出来。
>
> **因此实际方案改为 `_SnapshotBroker`** —— 把 `AccountSnapshot` 投影成券商的
> **只读**接口（`cash` / `portfolio_value` / `mark_prices` / `last_known_price` /
> `positions.get` / `open_symbols`），写方法一律抛 `AttributeError`。
>
> 这比初稿方案更好：`cash` / `portfolio_value` / `position` / `qty` / `price` /
> `current_prices` **一个都不用覆盖**，`LivePortfolioContext` 只需覆盖下单接口，
> 漂移面从 12 个方法降到 6 个。副作用是 `LegacyStrategyAlphaAdapter` 的只读路径
> 也能实盘跑了 —— 16 个 preset 因此具备了走框架上实盘的条件。
>
> 后续应把 PCM 改成读 `ctx.open_symbols` 而非 `ctx.broker`，让 context 真正成为唯一门面。

---

## 五、订单冲刷与既有风控的关系

`_flush_orders` 把本地 `Order` 转成 OMS 的 `LiveOrder` 并提交。**注意不要绕过已有的闸门**：

1. `OrderManager.submit_order` 内部已有 `_pre_trade_risk_check`（参数合法性）
2. L-a 已把 **同一批 `TradingControl`** 接进 OMS（`set_controls()`）
3. 现有 `_submit_live_order` 里还有 `get_risk_engine()` 的检查与 protections

冲刷路径**必须走 `OrderManager.submit_order`**，不得自己直连 gateway。

### 5.1 回测与实盘装同一批控制器

这是 L-a 契约的核心承诺，本契约要把它**真正用起来**：

```python
# 策略实例启动时
controls = build_controls(inst.params)      # 一份配置
broker_cfg.controls = controls              # 回测/纸面
oms.set_controls(controls)                  # 实盘
```

验收里要有用例证明：同一份 `controls` 配置下，回测拒掉的单实盘也拒，反之亦然。

---

## 六、验收

```
1. tests/regression 146 用例全绿（实盘代码不影响回测路径）
2. tests/test_live_context.py:
   - LivePortfolioContext 实现了 PortfolioContext 的全部公开方法
     （用 inspect 对比两个类的方法集合，缺一个就红 —— 防止接口漂移）
   - target_weight 在实盘上下文中产出与回测**相同的 diff 订单**
     （同样的持仓/价格/净值 → 同样的股数）
   - 账户快照拉取失败时**跳过本时点**，不产生任何订单（尤其不能全仓清空）
3. tests/test_live_engine.py:
   - 多标的 bar 异步到达时的聚合：齐了立即触发 / 超时用已有的触发
   - 缺失标的按最后已知价估值，不产生假清仓
   - 订单冲刷走 OrderManager.submit_order（用 mock 断言调用路径）
4. tests/test_live_parity.py ★ 本契约的核心用例：
   - 同一个 FrameworkStrategy 实例，喂同一份 bar 序列，
     回测引擎与实盘循环（OMS 用 mock）产出的**订单序列一致**
5. ruff check app tests → All checks passed!
6. pytest -q 全绿（当前 1225 passed，覆盖率门禁 61）
```

## 七、不做（本期范围外，理由要写进代码 docstring）

- **`PaperBroker` / `PaperPortfolio` 收编进回测引擎**。
  它们目前是启动时 60 天纸面模拟的简化实现，与回测引擎无共享，
  结果与真实回测不可比。收编是正确方向，但它会改变现有 `/strategy` 端点返回的
  模拟结果数值，属于用户可见的行为变更，应单独立项并配迁移说明。
  **本期只在 `PaperBroker` 类 docstring 里记为已知债务。**
- 实盘的 `Trade` 跟踪与 K4 风险闸门（实盘止损需要独立的持仓状态机，且涉及
  与券商侧持仓的对账，风险远高于回测）
- 实盘的 L4 仓位调整钩子
- 断线重连后的状态恢复（订单/持仓对账）
- **做空**：`allow_short` 在保证金模型（A7）落地前不对实盘开放
