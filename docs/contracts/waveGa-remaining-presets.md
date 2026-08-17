# Wave G-a 契约：剩余预设的指标预算改造

> 承接 [Wave E-a](waveEa-precomputed-indicators.md)（框架已就位）· Agent-Ga
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）**逐字节不变且全绿**。
> 这一项按定义不该让基线变色 —— 一旦变色，说明改出了行为差异，停下来查。

---

## 零、框架已经做好了，这一项只是把它用起来

E-a 建好了 `declare_indicators` / `ctx.ind` 全套，并改造了 3 个预设；
我随后又改了 2 个。**范式已经稳定，照抄即可**：

- `app/strategy/presets/double_ma.py`（单输出 × 2）
- `app/strategy/presets/macd.py`（元组输出）
- `app/strategy/presets/bollinger.py`（元组输出 + 热身守卫不可删）
- `app/strategy/presets/rsi_mean_reversion.py`（单输出 + NaN 语义）
- `app/strategy/presets/momentum.py`（**不走指标库**，自定义因果函数）

对拍测试范式见 `tests/test_preset_parity.py`。

---

## 一、待改造的 11 个

```
adx_trend.py          atr_breakout.py       donchian_breakout.py
grid_trading.py       keltner_breakout.py   multi_factor.py
pairs_trading.py      stochastic.py         supertrend.py
triple_ma.py          vwap_reversion.py
```

**不要求全改完。** 能做到逐笔一致的就改，做不到的**保留原写法**并在报告里
说明原因 —— 一个快 10 倍但结果不同的回测没有价值。

> **实际结果：改了 10 个。** 端到端加速比 2.2×–40.9×（策略侧扣掉引擎地板后
> 5.4×–105×），对拍矩阵从 5 个预设扩到 15 个共 108 条用例，回归基线一次未变色。
>
> 未改的两处：`grid_trading` 是**纯价格网格、一条指标都不用**（这一项本就
> 不该算进「11 个」，真实上限是 10）；`pairs_trading` 的模式 B 见 §三。

> ⚠️ **加速比门禁不能用端到端比值一刀切。** E-a 的 `_MIN_SPEEDUP=2.5` 是为
> O(n²) 策略校准的；`pairs_trading` 改造前是 O(n·lookback)（窗宽固定），
> 与引擎同阶，端到端比值天然封顶在 ~2.3× —— 拿它当门禁会把一个**完全正确的
> 改造判成「优化失效」**。要扣掉空策略的引擎地板，且每个计时点取多次最小值
> （单次采样的噪声能让同一用例的策略侧成本在 0 与 37 ms 之间跳）。

---

## 二、四条必须做对的

1. **每改一个都要逐笔对拍**：时间 / 方向 / 数量 / 价格四元组完全相同，
   ≥3 组参数 × 2 个市场。不是「笔数一致」，不是「指标接近」。
2. **不要顺手删热身守卫。** `bollinger` 的 `len(df) < period + 1` 就**不是**冗余的：
   第 `period-1` 根 bar 上轨道已经有值，守卫把它跳过了，删掉会多出一笔成交。
   每个预设的守卫都要单独判断，默认**保留**。
3. **非因果指标改不了。** `indicators.py` 的 `ichimoku` 有
   `chikou_span = close.shift(-kijun)` —— 声明进预算会被因果抽检拦下（这是对的）。
   碰到这类只能保留原写法。
4. ~~**`supertrend` 一类的递归指标要特别小心**，对拍不过就保留原写法。~~

   > **这条的前提是错的，而且会让人放弃最大的一块收益。**
   > `supertrend` 的循环是 `for i in range(1, n)` 且只读 `i-1` ——
   > **严格前向递推**，全帧与任意前缀逐位相等（5 个切点验过 `array_equal`）。
   > 它是加速比最大的两个之一（**40.9×**）。
   >
   > 真正会出问题的是「**引用未来**的递归」，不是「递归」本身。
   > 判据应该是「这个指标在前缀上重算是否与全帧一致」，而不是「它是否递归」。

5. **（原稿的真空白）`rolling` 会静默破坏逐位等价。**
   pandas 的 `rolling(n).mean()/.std()` 是**增量式**实现，与改造前
   「尾窗切片 + `Series.mean()/.std()`」**不是同一个浮点运算序列**。实测：

   ```
   rolling vs 切片      逐位相等   0/341   最大相对差 1.07e-13
   sliding_window_view  逐位相等 341/341   最大相对差 0
   ```

   1e-13 的差极少翻转比较，所以**对拍很可能碰巧通过 —— 那是运气不是等价**。
   要逐位一致必须用 `sliding_window_view` + 轴向归约（整段 pairwise 求和）。

   ⚠️ 这个等价依赖 pandas **未走 bottleneck**（本项目未装、也未声明依赖，已确认）。
   哪天装上会让对拍用例变红 —— 那正是对拍该起的作用，不要去改用例迁就。

6. **（原稿漏了）`add_multi` 的两条硬约束**：序列输出必须**给满**所有输出的名字
   （长度不等直接抛），所以 `donchian` 只用 upper/lower 也得声明 mid；
   且指标名**全局唯一**，同一函数用两组参数声明两次必须换名。

---

## 三、这一项特别容易出的错

⚠️ ~~**`pairs_trading` 是组合策略**，指标要走 `ctx.ind(symbol)`。~~
**这条是错的。** `PairsTradingStrategy(StrategyBase)` 是**单标的**策略
（`presets/pairs_trading.py:32`），上下文是 `StrategyContext`，
正确写法就是 `ctx.ind`。照原稿做只会得到一个不可调用的 `IndicatorView`。

> 它真正的难点在别处：**模式 B 的价差依赖 `on_bar` 里逐根 append 的
> `_price_b_history`**，配对标的价格由 `params` 注入、不在行情帧里 ——
> 它**不是行情帧的函数**，`declare_indicators` 表达不了。那条路径只能保留原写法。

⚠️ ~~**`multi_factor` 可能引用因子库而非 `indicators.py`**。~~ 不成立：
它只 import `indicators.macd/rsi` + 内联动量，与 `app/quant` 无关。

⚠️ **`grid_trading` 可能根本不用指标**（纯价格网格）。若如此，
**不要为了改而改** —— 在报告里说明它无需改造即可。

---

## 四、验收

```
1. tests/regression 146 用例全绿，且 git diff tests/regression/ 为空
2. tests/test_preset_parity.py:
   - 每个改造过的预设：≥3 组参数 × 2 市场，成交逐笔一致
   - _Legacy* 参照类是改造前 on_bar 的原样搬运（不要「凭理解重写」）
3. 未改造的预设行为一字不变（不必新增用例，但不能碰它们）
4. ruff check app tests → All checks passed! · pytest -q 全绿
5. 报告里给出：改了哪几个、每个的实测加速比、没改的分别为什么
```

## 五、不做

- 改 `indicators.py` 内部实现（那是另一件事，且要先重测）
- 改 `StrategyContext` / `precompute.py` 等框架层
- 为了凑数而强改对拍不过的预设
