# Wave L 接口契约索引 — 交易控制、风控与执行模型

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) Epic L。前置 [Wave K](waveK-README.md) 已全部合入。
> 沿用「契约先行 → 并行 agent → 主循环集成」模式。

## 契约清单

| 契约 | 特性 | 新增文件 | 依赖 | 状态 |
|------|------|---------|------|------|
| [waveLa](waveLa-trading-controls.md) | L1 TradingControl 控制族（8 个）· 补做 `LIMIT_IF_TOUCHED` | `engine/controls/`（新目录） | 无 | 📋 实现中 |
| [waveLb](waveLb-risk-execution-models.md) | L2 组合风控模型（5 个）· L3 执行模型（3 个新增） | `framework/risk/` `framework/execution/`（模块变包） | K-d | 📋 实现中 |
| [waveLc](waveLc-position-order-hooks.md) | L4 仓位调整钩子 · L5 下单确认与价格钩子 | `strategy/base.py` 扩展 | K-d | 📋 实现中 |

三者弱耦合，可并行。合并后再做**实盘接线**（K-d 与本 Wave 均已将其列为独立步骤）。

## 贯穿全 Wave 的约束（与 Wave K 一致）

### 1. 回归基线是红线

`backend/tests/regression`（146 用例 / 1364 笔逐笔成交）**必须始终全绿**，
且**绝不允许**修改其中任何文件。新能力一律默认关闭：

| 特性 | 默认值 |
|---|---|
| L1 控制器 | `controls=[]` / `account_controls=[]` |
| L2 风控 | 仍是 `NullRiskModel` |
| L3 执行 | 仍是 `ImmediateExecutionModel` |
| L4 仓位调整 | `position_adjustment_enable=False` |
| L5 钩子 | 基类默认实现全部返回 None / True |

### 2. 零新增依赖

### 3. 许可证红线

| 来源 | 许可 | 可否复制 |
|------|------|---------|
| zipline `finance/controls.py`（L1，8 个类 ~450 行） | Apache-2.0 | ✅ 可直接复制，保留版权头 |
| Lean `Algorithm.Framework/{Risk,Execution}/*.py`（L2/L3） | Apache-2.0 | ✅ **是 Python 源码，可直接翻译** |
| **freqtrade `strategy/interface.py`（L4/L5 钩子语义）** | **GPL-3.0** | ❌ **只可读设计，独立重写** |

### 4. 共享文件由主循环统一 wire

agent 不得编辑：`api/v1/router.py` · `main.py` · `requirements.txt` ·
`strategy/presets/__init__.py` · `App.tsx` · `Sidebar.tsx` · `types/index.ts`。

## 本 Wave 最容易做错的四件事

1. **L1 的价值是「回测与实盘共用同一套控制器」**，不是多几个控制器。
   控制器不得持有 broker/OMS 引用，全部信息经 `ControlContext` 传入。
2. **L2 输出的是修正后的 `PortfolioTarget`，不是订单**。清仓 = `quantity=0`，由执行模型算 diff。
3. **区分 K6 撮合层成交量约束与 L3 执行层拆单**：前者券商侧硬约束、策略无法规避；
   后者策略主动拆单降低冲击。两者叠加是正常的。
4. **L5 的 `custom_*_price` 必须产出 LIMIT 单**，不是「用该价格市价成交」（那是偷看未来价）。

## Wave K 遗留，本 Wave 处理

- `LIMIT_IF_TOUCHED` 订单类型（waveKa 契约疏漏）→ 并入 L-a

## Wave K 遗留，本 Wave **不**处理

- **做空无保证金约束**（A7 `BuyingPowerModel`）。`MaxLeverage` 只做事后校验，不是替代品。
  **在 A7 落地前不要对用户开放 `allow_short`。**
- `ContractDailyResult.slippage` 恒为 0（滑点已折进成交价，无基准价可反解）
- ~~组合回测未接 API~~ → **已完成**：`POST /api/v1/backtests/portfolio`（未采用 `bars_by_symbol`，改为服务端取数）
