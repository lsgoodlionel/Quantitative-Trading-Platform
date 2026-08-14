# V3 Wave E-a 契约：指标预算（J2 重定向）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **J2**，方向已按实测推翻重定
> （见 [docs/profiling-backtest-j2.md](../profiling-backtest-j2.md)）· Agent-Ea
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）**逐字节不变且全绿**。
> 本项工作按定义不该让它们变色 —— 一旦变色，说明改到了不该改的地方。

---

## 零、为什么不是「向量化引擎」

J2 原文写「回测性能向量化（参考 vectorbt 思想，用 numpy）」。实测否掉了它：

| 原设想 | 实测 |
|---|---|
| 引擎逐 bar 循环是瓶颈 | 单标的 **5%**，组合 **1.5%** |
| 数据量大导致慢 | 数据量涨 67×，成本只涨 1.56×（64% 是 pandas 固定调用开销） |
| — | **86%（单标的）/ 97.6%（组合）在策略的 `on_bar`/`on_bars` 里** |

**热点是「每根 bar 对全量历史重算指标」，不是引擎。**
预算指标的实测天花板：单标的 9.93×、组合 10.8×–11.0×，成交逐笔一致。

---

## 一、要做的事

给 `StrategyBase` / `PortfolioStrategyBase` 一条**可选**的指标预算路径：
策略在开跑前声明要哪些指标，框架算好并按时点游标喂给它。

```python
class DoubleMaStrategy(StrategyBase):
    def declare_indicators(self, spec: IndicatorSpec) -> None:
        """可选钩子。声明本策略要用的指标 —— 框架一次算完。"""
        fast = self.param("fast_period", 10)
        slow = self.param("slow_period", 30)
        spec.add("fast", sma, fast)
        spec.add("slow", slow_ma_fn, slow)

    def on_bar(self, ctx: StrategyContext) -> None:
        # ctx.ind 只能取到「截至当前时点」的值 —— 见 §2
        if ctx.ind.crossed_up("fast", "slow") and ctx.qty == 0:
            ...
```

---

## 二、头号风险：预算指标让策略握有未来值

这是整件事**唯一真正难的地方**，其余都是工程。

一次算完整条均线，意味着那个 Series 里装着未来。若策略能拿到
`ind["fast"]` 这个完整 Series，它就能 `.iloc[-1]` 取到回测结束时的值 ——
**一个看起来正常、跑出漂亮曲线、而结果完全无效的回测。**

### 2.1 四条硬要求

1. **必须由框架保证，不能靠策略作者自觉。** `ctx.ind` 暴露的接口
   **不得返回完整 Series**。只提供按当前游标求值的方法
   （`value(name)` / `crossed_up(a, b)` / `series(name, n)` 取最近 n 个）。
   任何返回完整序列的接口都是这条红线的缺口。
2. **`series(name, n)` 必须做上界裁剪**，裁到当前游标为止 —— 不是从末尾数，
   是从**当前时点**往回数。这两者在回测中途完全不同。
3. ~~**必须过 `bias_detection.py`**，并有一条故意制造泄漏的测试证明它能被抓到。~~

   > **这一条基于错误前提，实测已推翻。** `bias_detection` 不是引擎里的可插拔
   > 检查点，是个独立的重跑对比工具（`run_bias_check(run_fills, bars)`），
   > 没有「接进去」的钩子。
   >
   > 更要紧的是**它抓不到这类泄漏**。我构造了一个持有完整帧、偷看 H 根之后
   > 收盘价的策略跑真实引擎：
   >
   > | 偷看 | 成交 | checked | changed | |
   > |---|---|---|---|---|
   > | 1 根 | 185 | 129 | 0 | ❌ 漏报 |
   > | 2 根 | 135 | 97 | **1** | ✅ 检出（97 里只变了 1 个） |
   > | 3 根 | 99 | 68 | 0 | ❌ 漏报 |
   > | 5 根 | 83 | 58 | 0 | ❌ 漏报 |
   >
   > 根因是 `detect_lookahead` **只在单个切点扰动**（默认保留前 70%），
   > 而偷看 H 根的策略只在切点前 H 根内决策会变 —— 400 根里那是几根的事，
   > 灵敏度天然约等于 H/N。
   >
   > **所以防线必须建在框架层，不能指望这个检测器兜底。** 实际做法是
   > `IndicatorView` 在构造时就 `arr[:cursor]` 物理裁剪（numpy 视图，O(1)），
   > 未来根本没进过这个对象 —— 绕过公开方法去摸私有属性也只摸到过去。
   > 另加一道「因果性抽检」：在两个切点用前缀重算并与全帧整段比对，
   > `close.shift(-1)` 这类在 `on_start` 就抛 `StrategyContractError`。
4. **预算的窗口热身期语义要与现状一致。** 预算版在热身期内 `value()` 应返回
   `None` 而非 `NaN` 或 0 —— `NaN` 参与比较会静默得到 `False`，
   把「还没数据」和「条件不成立」混为一谈。

   > ⚠️ **但不要顺手删掉策略里的 `len(df) < period` 守卫。** 原稿把它说成
   > 可以由 `None` 语义替代的冗余，对 `double_ma` 确实如此，
   > 但 **`bollinger` 的守卫不是冗余的**：第 `period-1` 根 bar 上轨道已经有值，
   > 守卫把它跳过了。照原稿暗示改会多出一笔成交。

5. **（原稿漏了）`on_bar` 的调用点不止回测引擎，共 5 处。**
   `portfolio_engine` 两处（组合 / 单标的适配器）、`paper_sim.run_paper_simulation`
   （实盘启动必跑）、`live_runner`、`LegacyStrategyAlphaAdapter._insights_for`。
   只接回测那一条的话，改造过的预设（都是注册表默认策略）**一进实盘就抛
   `StrategyContractError`** —— 部署阻断级。每条都要接上并有用例钉住。

6. **（原稿漏了）`spec.add` 只够单输出指标。** §3.2 要改造的预设里
   macd / bollinger / stochastic / keltner / donchian 全是元组输出，
   需要 `add_multi`。

7. **（原稿漏了）组合策略怎么用 `ctx.ind`。** `PortfolioContext` 下没有
   「当前标的」概念，做成 `ctx.ind(symbol)` 方法，与 `history(symbol)` 形状一致。

8. **（原稿漏了）`indicators.py` 里已有一个非因果指标**：`ichimoku` 的
   `chikou_span = close.shift(-kijun)`。把它声明进预算会被因果抽检拦下（正确），
   也意味着它只能走旧路径。

---

## 三、兼容性

### 3.1 旧写法必须继续可用

用户策略在 `user_strategies_dir`（仓库外），**不能要求他们改**。
没实现 `declare_indicators` 的策略走原路径，一行不变。

### 3.2 改造范围：只改预设策略，且逐个对拍

`app/strategy/presets/` 下有十几个预设。**不要一次全改**。

**每改一个，都必须断言改造前后成交序列逐笔一致** ——
不是「笔数一致」，不是「指标接近」，是逐笔（时间、方向、数量、价格）。
测量脚本里我只比了笔数，那是快速筛查；这里要的是逐笔。

⚠️ 若某个预设改造后无法做到逐笔一致，**保留原写法并在报告里说明原因**。
一个快 10 倍但结果不同的回测没有价值。

---

## 四、验收

```
1. tests/regression 146 用例全绿，且 git diff tests/regression/ 为空
2. tests/test_indicator_precompute.py:
   - ctx.ind 不提供任何返回完整 Series 的接口（反射断言公开方法集）
   - series(name, n) 在回测中途取到的末位 == 当前 bar，不是最后一根 bar
   - 热身期内 value() 返回 None（不是 NaN、不是 0）
   - 故意构造一个「取未来值」的策略 → 被 bias_detection 抓到
   - 未实现 declare_indicators 的策略走原路径，行为不变
3. tests/test_preset_parity.py:
   - 每个改造过的预设：改造前后**成交逐笔一致**（时间/方向/数量/价格）
   - 参数化覆盖至少 3 组参数、2 个市场
4. 性能：给出改造前后的实测数字（单标的 + 组合各一组），
   **数字要与成交一致性断言在同一个测试里**，防止「快了但不对」
5. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 五、不做

- **向量化引擎事件循环**（见 §零：占 1.5%–5%，负收益）
- **`indicators.py` 内部改用 numpy** —— 指标只算一次之后，
  每次调用的固定开销乘的是 1 而不是 N。**先落地本项，重新测，再决定**。
- 改造用户策略或要求用户迁移
- 多进程/多线程并行寻优（另一件事，且要先确认 GIL 不是瓶颈）
