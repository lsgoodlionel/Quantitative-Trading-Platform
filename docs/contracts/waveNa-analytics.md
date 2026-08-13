# Wave N-a 契约：容量·换手·杠杆（N2）+ 危机区间（N3）+ 交叉归因与拒绝信号（N4）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **N2 / N3 / N4** · Agent-Na
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 零、先看清已有什么（开工前已复核，避免重复造）

| | 已有 | 缺口 |
|---|---|---|
| N2 | 无 | 容量估计 / 换手率 / 杠杆利用率 **全缺** |
| N3 | `drawdown_periods.py` 的 peak→valley→recovery **已完整** | **危机区间分段对照** |
| N4 | `tag_metrics.py` 的 `by_entry_tag` / `by_exit_reason` **各自分组已有** | **交叉表** + **拒绝信号统计** |

**不要重写 `drawdown_periods.py` 与 `tag_metrics.py` 的既有部分。**

---

## 一、N2 容量 · 换手 · 杠杆

参考 Lean `Report/ReportElements/{EstimatedCapacity,Turnover,LeverageUtilization}`（Apache-2.0）。

```python
# app/engine/backtest/capacity.py（新文件）

def turnover_series(daily: list[PortfolioDailyResult], equity: pd.Series) -> pd.Series:
    """日换手率 = 当日成交额 / 当日组合净值。"""

def leverage_series(daily, equity) -> pd.Series:
    """杠杆利用率 = 持仓总市值 / 净值。做空时用**绝对值**加总，否则多空对冲会算成 0 杠杆。"""

def estimate_capacity(
    daily, bars_by_symbol, *, adv_window: int = 20, max_adv_share: float = 0.05
) -> CapacityEstimate:
    """策略容量：在「单标的单日成交不超过 ADV 的 max_adv_share」约束下，
    策略最多能管多少钱。"""
```

**三个容易做错的点**：

1. **数据来源是 K-c 的 `PortfolioDailyResult`**（已含 `turnover` / `contracts[*].end_pos`），
   不要自己从 fills 重算一遍 —— 两套算法必然漂移。
2. **杠杆用绝对值加总**。`Σ|市值|/净值`，而不是 `Σ市值/净值` ——
   后者在多空对冲时会得出「零杠杆」这种荒谬结论。
3. **容量估计要标注它是个粗估**。ADV 约束只是冲击成本的一个代理，
   真实容量还取决于冲击模型、执行算法、市场状态。返回体里带 `assumptions` 字段
   写明用了哪些前提，**不要给一个看起来很精确的单一数字**。

---

## 二、N3 危机区间分段对照

```python
# app/engine/backtest/crisis.py（新文件）

@dataclass(frozen=True)
class CrisisWindow:
    name: str
    start: date
    end: date
    markets: tuple[str, ...]      # 该危机适用的市场

CRISIS_WINDOWS: tuple[CrisisWindow, ...] = (
    CrisisWindow("2008 金融危机",   date(2007, 10, 9),  date(2009, 3, 9),  ("US", "HK")),
    CrisisWindow("2015 A股股灾",    date(2015, 6, 12),  date(2016, 1, 28), ("A",)),
    CrisisWindow("2020 疫情崩盘",   date(2020, 2, 19),  date(2020, 3, 23), ("US", "HK", "A")),
    CrisisWindow("2022 加息熊市",   date(2022, 1, 3),   date(2022, 10, 12),("US", "HK")),
)

def crisis_performance(equity: pd.Series, market: str) -> list[dict]:
    """逐个危机区间算该策略的表现。区间与回测期无交集时**跳过而非返回 0**。"""
```

⚠️ **区间与回测期无交集时必须跳过**，不能返回一行全 0 —— 那看起来像「这段时间策略没波动」，
而事实是「这段时间根本没数据」。返回体里用 `covered: bool` 或干脆不出现该行。

⚠️ **危机区间是硬编码的历史事实，日期要能溯源**。每条在注释里写明依据
（如「2008：S&P500 从 1565 峰值到 676 谷底」），不要凭印象填。

---

## 三、N4 交叉归因 + 拒绝信号

### 3.1 交叉表

```python
# app/engine/backtest/tag_metrics.py（扩展，不动既有函数）

def cross_tag_metrics(trips: list[RoundTrip], starting_balance: float) -> list[dict]:
    """entry_tag × exit_reason 交叉归因。

    回答的是「哪个入场理由配哪个出场理由最赚/最亏」——
    单看 by_entry_tag 或 by_exit_reason 都看不出这层关系。
    """
```

空组合（某 entry_tag 从未以某 exit_reason 收场）**不出现在结果里**，
不要填一堆 0 行把表撑大。

### 3.2 拒绝信号统计

参考 freqtrade `generate_rejected_signals`（**GPL-3.0 —— 只可读设计，独立实现**）。

**现状**：策略想下单但被拒的情况（现金不足、持仓不足、T+1、交易控制器拦截、
风险闸门否决）目前只写进 `Order.reject_reason`，**没有任何汇总**。
用户看到的是「策略怎么没开仓」，却不知道是被什么拦的。

```python
def rejected_signal_summary(orders: list[Order]) -> list[dict]:
    """按 reject_reason 归类的拒绝统计：次数、涉及标的数、首末时间。"""
```

⚠️ **`reject_reason` 是自由文本且带具体数值**（如「A股T+1限制: 可卖 0 股，请求卖 100 股」）。
直接按原文分组会得到几百个只出现一次的分类。**必须先归一化成类别**
（正则剥掉数字/标的名，或在拒单处补一个结构化的 `reject_code`）。
选哪种在报告里说明 —— 补 `reject_code` 更干净但要动 broker，正则更轻但脆。

---

## 四、接入

三块都进 `report_sections.py` 的报告体系（与 C7 tearsheet 同一处），
成为组合回测与单标的回测返回体里的**可空 section** —— 与既有 section 一致，
不改变现有返回体结构。

---

## 五、验收

```
1. tests/regression 146 用例全绿
2. tests/test_capacity.py:
   - 换手率 = 成交额/净值，与手算小样本对拍
   - 杠杆用绝对值加总：多空各半的组合杠杆 ≈ 1.0 而不是 0
   - 容量估计返回体含 assumptions
3. tests/test_crisis.py:
   - 回测期覆盖某危机 → 该行有数据
   - 回测期与危机无交集 → **跳过**，不出现 0 行
   - 市场不匹配（A股策略遇到 2008）→ 跳过
4. tests/test_tag_metrics.py 扩展:
   - 交叉表的行数 = 实际出现过的 (entry_tag, exit_reason) 组合数
   - 拒绝信号归一化：同类不同数值的 reject_reason 归到同一类
5. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 六、不做

- 前端展示（本期只出数据）
- 容量估计的冲击模型精细化
- 危机区间的自动识别（硬编码历史区间即可）
