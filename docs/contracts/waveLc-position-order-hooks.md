# Wave L-c 契约：仓位调整钩子 + 下单确认与价格自定义钩子

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **L4 / L5** · Agent-Lc
> 依赖：K-d（`Trade` / `broker_exits.py` / `StrategyBase` 的类级配置已就位）
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）必须全绿。
> 所有钩子的**基类默认实现一律返回 None = 不干预**，16 个 preset 一个都不覆盖它们。

> ⚠️ **许可证**：L4/L5 的钩子语义参考 freqtrade `strategy/interface.py`（**GPL-3.0**）。
> **只可阅读其设计后独立实现，绝对不可复制任何代码**。文件头注明
> 「设计参考自 freqtrade IStrategy，独立实现」。GPL 传染会波及整个 QuantBot。

---

## 一、现状（K-d 交付后）

`StrategyBase` 已有 K4 的类级声明式配置与两个运行期钩子：

```python
stoploss / trailing_stop / trailing_stop_positive / trailing_stop_positive_offset / minimal_roi
def custom_stoploss(...) -> float | None
def custom_roi(...) -> float | None
```

`StrategyContext` 已有 `buy / sell / buy_value / sell_all / short / cover / close_all`。
`Trade`（`engine/backtest/trade.py`）已能表达单笔交易的开仓价、持仓时长、收益率极值。

**缺的是**：开仓后无法追加/减少仓位（DCA、分批止盈），以及下单前无法否决或改价。

---

## 二、L4 仓位调整钩子

```python
class StrategyBase:
    #: 是否允许在已有持仓上追加/减少。默认 False —— 关掉时整条路径是 no-op
    position_adjustment_enable: bool = False
    #: 单笔交易最多允许的调整次数（防止策略把一笔交易无限加仓）
    max_position_adjustments: int = 10

    def adjust_position(
        self, ctx: StrategyContext, trade: Trade, current_profit: float
    ) -> float | None:
        """返回本次调整的**增量金额**（币种同 initial_cash）。

        - 正数 = 加仓（DCA）
        - 负数 = 部分平仓
        - None = 不调整（默认）

        返回负数且绝对值 >= 当前持仓市值时，视为全平。
        """
        return None
```

### 2.1 执行时机

接在 K-d 已建的风险闸门之后、`on_bar` 之前：

```
1. advance_day()
2. 除权日派息
3. process_bars()            撮合上一时点挂单
4. check_exit_conditions()   K4 风险闸门：ROI → 止损 → 追踪止损
5. ★ adjust_positions()      L4 仓位调整（新增）
6. strategy.on_bar(ctx)
7. 记录净值
```

**为什么在风险闸门之后**：已经该止损的仓位不应该再被加仓。
**为什么在 `on_bar` 之前**：与风险闸门同理，仓位调整属于持仓管理，先于新的策略意图。

### 2.2 三个必须做对的点

1. **加仓改变止损基准**。K-d 已处理同向加仓时重置收益率极值（否则追踪止损会在毫无回撤时
   打掉新仓位）。L4 的加仓必须走**同一条路径**，不要另写一份持仓更新逻辑。
2. **部分平仓要产生带 `exit_reason` 的 Fill**。建议 `exit_reason="partial_exit"`，
   直接进 `tag_metrics.py` 的分组统计。
3. **调整次数上限必须真的生效**。`Trade` 需要记录本笔已调整次数；超过
   `max_position_adjustments` 时忽略并记 warning，**不要静默忽略**。

---

## 三、L5 下单确认与价格自定义钩子

```python
class StrategyBase:
    def confirm_entry(
        self, ctx, symbol: str, qty: int, price: float, entry_tag: str | None
    ) -> bool:
        """返回 False 否决本次开仓。默认 True。"""
        return True

    def confirm_exit(
        self, ctx, symbol: str, qty: int, price: float, exit_reason: str
    ) -> bool:
        """返回 False 否决本次平仓。默认 True。

        ⚠️ 否决**风险闸门**产生的平仓（stop_loss / roi / trailing_stop_loss）是危险操作：
        止损被策略否决后，仓位会一直留着。实现时必须记 warning 并在
        docstring 里写明这一点，不要让它悄无声息。
        """
        return True

    def custom_entry_price(self, ctx, symbol: str, proposed: float) -> float | None:
        """自定义开仓价（用于挂限价单）。返回 None = 用 proposed。"""
        return None

    def custom_exit_price(self, ctx, symbol: str, proposed: float, exit_reason: str) -> float | None:
        return None

    #: 挂单超时（分钟）。超时未成交则撤单。None = 不超时（沿用 TimeInForce）
    entry_timeout_minutes: int | None = None
    exit_timeout_minutes: int | None = None
```

### 3.1 与 K3 TimeInForce 的关系

K-a 已实现 `TimeInForce`（GTC/DAY/GTD）。L5 的 `*_timeout_minutes` 是**更细粒度的补充**：
TIF 按自然日过期，timeout 按分钟。**两者同时配置时取更早者**，并在 docstring 写明。

### 3.2 `custom_*_price` 会把市价单变成限价单

返回非 None 的价格意味着策略想在特定价位成交 —— 这必须转成 `OrderType.LIMIT` 挂单，
而不是「用这个价格市价成交」（后者是偷看未来价）。实现时显式设置 `order_type=LIMIT`
与 `limit_price`，并在测试里断言产出的确实是限价单。

---

## 四、接入点

- `app/strategy/base.py` — 新增上述配置与钩子（全部带默认实现）
- `app/engine/backtest/broker_exits.py` 或新建 `broker_adjust.py` — L4 的调整循环
  （若 `broker_exits.py` 加完超过 600 行，请拆新文件）
- `app/engine/backtest/portfolio_engine.py` — `_step` 中接入第 5 步
- `app/strategy/context.py` — 若 L4 需要新的下单入口（如带 `exit_reason` 的部分平仓）

`PortfolioStrategyBase` 也应能用这些钩子；`_SingleSymbolAdapter` 需透传（K-d 已有透传三个钩子的先例，照做）。

---

## 五、验收

```
1. tests/regression 146 用例全绿
   （position_adjustment_enable=False 且所有钩子未覆盖 → 整条路径 no-op）
2. tests/test_position_adjustment.py:
   - DCA 加仓后 Trade.open_price 与追踪止损基准的变化符合 K-d 既定语义
   - 部分平仓产生 exit_reason="partial_exit" 的 Fill，且回合配对正确
   - max_position_adjustments 生效并记 warning（用 caplog 断言）
   - 返回负数且超过持仓市值 → 全平，不产生负持仓
3. tests/test_order_hooks.py:
   - confirm_entry 返回 False → 不产生 Fill
   - confirm_exit 否决风险闸门平仓 → 仓位保留 + 有 warning
   - custom_entry_price 返回价格 → 产出的是 LIMIT 单且 limit_price 正确
   - entry_timeout_minutes 与 TimeInForce.DAY 同时配置时取更早者
4. ruff check app tests → All checks passed!
5. pytest -q 全绿（当前 1033 passed，覆盖率门禁 61）
```

## 六、不做

- 实盘接线（本 Wave 独立步骤）
- `adjust_order_price`（freqtrade 有，用于已挂单的改价）—— 需要订单修改语义，本期只做撤单重挂
- 分批建仓的资金曲线优化（属策略层，不是引擎层）
