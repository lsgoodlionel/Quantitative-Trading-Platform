# Wave K 接口契约索引 — 引擎内核重构

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) Epic K。
> 这些是**设计契约**（接口 + 文件规划 + 验收），**非实现代码**。审阅确认后进入并行实现。
> 沿用 [HANDOFF.md](../../HANDOFF.md) §4 已验证的「契约先行 → 并行 agent → 主循环集成」模式。

## 契约清单

| 契约 | 特性 | 新增文件 | 依赖 | 状态 |
|------|------|---------|------|------|
| [waveKa](waveKa-order-position.md) | K3 订单类型体系 · K2 做空与双向持仓 | `engine/backtest/order_types.py` | 无 | ✅ 已合入 |
| [waveKb](waveKb-microstructure-calendar.md) | K6 成交量约束滑点 · K7 交易日历 · K8 复权 | `engine/calendar/*`（新目录）· `data/adjustments.py` | 无 | ✅ 已合入 |
| [waveKc](waveKc-portfolio-engine.md) | K1 多标的组合回测引擎 | `engine/backtest/{portfolio_engine,portfolio_broker,daily_result}.py` | K-a | ✅ 已合入 |
| [waveKd](waveKd-strategy-hooks-insight.md) | K4 策略级止损/ROI · K5 Insight 三段式 | `engine/backtest/trade.py` · `engine/framework/*`（新目录） | K-c | ✅ 已合入 |

## 贯穿全 Wave 的四条硬约束

### 1. 回归基线是红线

`backend/tests/regression`（144 用例 / 1364 笔逐笔成交）**必须始终全绿**。

新能力一律**默认关闭**：`allow_short=False` · `calendar=None` · `adjust_prices=False` ·
`stoploss=None` · 滑点模型的 `fill_limit` 默认不限量。
「不配置时行为与今天逐笔一致」是每个契约的验收第一条。

> 如果某项改动确实需要变更既有行为，流程是：**先在 PR 中论证 → 重新生成基线 → 审阅 diff**，
> 而不是改测试让它通过。

### 2. 零新增重依赖

沿用 HANDOFF §3.6。具体到本 Wave：

- 交易日历用静态假日表，**不引 `exchange_calendars`**
- 复权数据用已有的 `yfinance` / `AkShare` provider
- 组合引擎的性能优化用 numpy，**不引 `polars` / `numba` / `vectorbt`**

### 3. 许可证红线

| 来源 | 许可 | 可否复制 |
|------|------|---------|
| zipline（K2 持仓 · K6 滑点 · K7 日历 · K8 复权 · L1 控制族） | Apache-2.0 | ✅ 可复制，保留版权头 |
| Lean（K3 订单语义 · K5 框架 · L2/L3 模型） | Apache-2.0 | ✅ 可翻译（Python 源码） |
| vnpy（K1 组合回测 · 日度盈亏拆解） | MIT | ✅ 可移植（polars→pandas） |
| **freqtrade**（K4 止损/ROI 语义 · K-c 时间轴对齐思路） | **GPL-3.0** | ❌ **只可读算法，独立重写**，文件头注明「设计参考自 freqtrade，独立实现」 |

GPL 传染会波及整个 QuantBot，这条没有商量余地。

### 4. 共享文件由主循环统一 wire

agent **不得**编辑：`api/v1/router.py` · `App.tsx` · `Sidebar.tsx` · `main.py` ·
`requirements.txt` · `types/index.ts` · `strategy/presets/__init__.py`。
需要改动时返回集成片段，由主循环合并。

## 实现顺序（强依赖，不宜过度并行）

```
K-a ──▶ K-c ──▶ K-d
K-b ──┘（独立，可与 K-a 并行）
```

1. **Agent-Ka**：K3 + K2（`broker.py` / `position.py` / 新 `order_types.py`）
2. **Agent-Kb**：K6 + K7 + K8（`slippage.py` / 新 `engine/calendar/` / 新 `data/adjustments.py`）— 与 Ka 并行
3. **Agent-Kc**：K1（等 Ka 合入后启动）
4. **Agent-Kd**：K4 + K5（等 Kc 合入后启动）

主循环负责：CI 维护、共享文件集成、每步跑 `pytest` + `npm run lint/type-check/test/build`。

## 前置已完成

- ✅ 回归基线 `backend/tests/regression`（2026-08-07）
- ✅ CI `.github/workflows/ci.yml`（2026-08-07）

详见 [DEVPLAN_V4.md §十](../../DEVPLAN_V4.md)。
