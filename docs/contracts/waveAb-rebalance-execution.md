# V3 Wave A-b 契约：组合再平衡执行（G2）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **G2** · Agent-Ab
> **前置已就绪**：Wave K-c 的 `PortfolioContext.target_weight()` 已实现「目标权重 → 增量 diff 订单」
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、V3 记录的断链，以及现状

V3 原文：`PortfolioOptimizer.tsx:799` 优化算完权重后**只有一个 `<Link to="/orders">`**，
用户得自己把权重抄成订单。

现状比 V3 记录的稍好一点，但仍未打通：

| 已有 | 缺口 |
|---|---|
| `POST /portfolio/rebalance`（`api/v1/endpoints/risk.py:221`）算再平衡指令 | 只算**金额** `delta_value`，不落到**股数**；不执行 |
| `compute_rebalance()`（`app/risk/portfolio.py:168`）按市值算权重差 | 与 K-c 的 `target_weight()` 是**两套 diff 逻辑** |
| `OrderManager.submit_order` 具备熔断/审计/L1 控制器 | 再平衡结果没有送进去 |

## 二、必须先解决的一致性问题

**现在有两套「目标权重 → 调仓」逻辑，本契约不能再加第三套。**

| | `compute_rebalance()`（既有） | `PortfolioContext.target_weight()`（K-c） |
|---|---|---|
| 输出 | 金额 `delta_value` | **股数**增量订单 |
| 用途 | API 展示 | 回测引擎内实际下单 |
| 取整 | 无（浮点金额） | 有（整股） |

**要求**：本契约的执行路径**必须复用 K-c 的股数级 diff 逻辑**，
把 `target_weight` 中「权重 → 目标股数 → 减去现有持仓」的算法抽成**不依赖 context 的纯函数**：

```python
# app/engine/portfolio/rebalance.py（新文件）

@dataclass(frozen=True)
class RebalanceLeg:
    symbol: str
    current_qty: int
    target_qty: int
    delta_qty: int          # 正=买入，负=卖出
    price: float
    delta_value: float
    reason: str             # "increase" / "decrease" / "open" / "close"

def plan_rebalance(
    target_weights: dict[str, float],
    current_qty: dict[str, int],
    prices: dict[str, float],
    portfolio_value: float,
    *,
    min_trade_value: float = 0.0,
    lot_size: int = 1,
) -> list[RebalanceLeg]: ...
```

然后 **`PortfolioContext.target_weight()` 改为调用它**，保证回测与实盘调仓算法只有一份。

> ⚠️ 改动 `target_weight()` 会触碰 K-c 已验证的路径。
> **必须先让 `tests/regression` 与既有 `test_portfolio_engine.py` 全绿，再继续。**
> 若无法做到逐笔一致，保留 `target_weight()` 原实现，在报告中说明并把 `plan_rebalance`
> 仅用于 API 路径 —— 但要明确记为技术债，不要假装统一了。

---

## 三、两步执行 API

```
POST /api/v1/portfolio/rebalance/preview
     { target_weights, market, min_trade_value?, lot_size? }
  → { legs: [RebalanceLeg], total_buy_value, total_sell_value, estimated_commission,
      warnings: [...] }        # 持仓/价格从 OMS 实时拉

POST /api/v1/portfolio/rebalance/execute
     { legs: [...], confirm_token }
  → { submitted: [order_id], rejected: [{symbol, reason}] }
```

### 3.1 `confirm_token` 是必需的，不是可选装饰

预览与执行之间价格会变。**执行时必须校验**：

1. `confirm_token` 由预览端点签发，内含预览时的持仓快照哈希与时间戳
2. 执行时若持仓已变化，或 token 超过 `REBALANCE_TOKEN_TTL_SECONDS`（建议 60），**拒绝执行**并要求重新预览
3. 不接受客户端直接传 legs 而不带 token —— 那等于让前端决定下什么单

> 这是一个**真金白银**的端点。没有这道校验，一次陈旧的预览就能在市场大幅波动后被执行。

### 3.2 执行必须走 OMS

逐 leg 调 `OrderManager.submit_order`，**不得直连 gateway**。
这样自动获得：`_pre_trade_risk_check` · L-a 的 `TradingControl` · protections 熔断 · 审计日志。

**部分失败要如实返回**：某些 leg 成功、某些被拒是常态，返回体里两个数组都要填，
不得在任何一个 leg 失败时把整批回滚（做不到原子性就不要假装原子）。

---

## 四、前端

`PortfolioOptimizer.tsx` 优化结果区：把 `<Link to="/orders">` 换成
「预览调仓」→ 展示 legs 表格（含买卖方向、股数、金额、预估佣金）→「确认执行」。

执行后展示成功/失败明细，失败项给出原因（直接用后端返回的 `reason`）。

⚠️ 不要编辑 `App.tsx` / `Sidebar.tsx` / `types/index.ts` / `router.py`，需改动时给集成片段。

---

## 五、验收

```
1. tests/regression 146 用例全绿；test_portfolio_engine.py 全绿
   （若改了 target_weight，这两项是它未破坏 K-c 的证据）
2. tests/test_rebalance_plan.py:
   - 已有 100 股、目标 150 股 → delta_qty=+50（不是 150）
   - 目标权重为 0 → 全部卖出，reason="close"
   - min_trade_value 过滤掉碎单
   - lot_size 取整（港股/A股整手）
   - 权重和 != 1 时报错而非静默归一化
3. tests/test_api_rebalance.py:
   - preview → execute 正常链路
   - token 过期 → 拒绝
   - 持仓在两步之间变化 → 拒绝
   - 部分 leg 被 OMS 拒绝 → 返回体两个数组都正确，不整批回滚
   - execute 走的是 OrderManager.submit_order（mock 断言调用路径）
4. ruff check app tests → All checks passed! · 前端 lint/type-check/test/build 全过
5. pytest -q 全绿
```

## 六、不做

- 定时/触发式自动再平衡（E2，Wave M）
- 跨市场再平衡（需先有跨市场组合，K7 已备日历但组合引擎目前单市场）
- 做空腿的再平衡（`allow_short` 在保证金模型 A7 落地前不对实盘开放）
