# Wave 1 接口契约索引

> v2.0 升级 Wave 1（快赢 + 基础）的接口契约文档。
> 这些是**设计契约**（API schema + TypeScript 类型 + 文件规划），**非实现代码**。
> 审阅确认后进入标准把关的并行实现阶段。

## 契约清单

| 契约 | 特性 | 新增后端文件 | API 变更 | 状态 |
|------|------|------------|---------|------|
| [wave1a](wave1a-factor-processors.md) | B1 横截面处理 · B4 因子适应度 | `quant/{processors,processing_pipeline,panel,factor_fitness}.py` | +3 端点（processors/meta·preview·factor/fitness） | 📋 待审阅 |
| [wave1b](wave1b-risk-allocation.md) | D1 收缩协方差 · D2 离散分配 | `portfolio/{risk_models,expected_returns,discrete_allocation}.py`（3 新文件） | 扩展 `POST /optimize` + 新 `POST /allocate` | 📋 待审阅 |
| [wave1c](wave1c-backtest-tearsheet.md) | C6 回测报告 · C7 tearsheet | `engine/backtest/{roundtrips,trade_analytics,periodic_stats,rolling_stats,drawdown_periods,tag_metrics,report_sections}.py`（7 新文件） | 扩展 `BacktestResponse`（+5 可空 section） | 📋 待审阅 |
| [wave1d](wave1d-protections-notify.md) | E1 防护熔断 · E2 通知 | `oms/protections/*` + `notify/*` | +协议端点 + `GET/PUT /notify/config` + `POST /notify/test` | 📋 待审阅 |

## 关键设计决策（跨契约）

1. **零新增重依赖** — W1b 发现无 cvxpy/pypfopt，改用已装的 `sklearn.covariance.ledoit_wolf` + `scipy.optimize.milp`，`requirements.txt` 不变。
2. **全部向后兼容** — 所有 API 变更为「扩展现有请求（带默认值）」或「新增端点」，不破坏现有前端。
3. **防泄漏优先** — W1a 的 infer/learn 处理器分离是整个 Epic B 的基础，train 窗口外不 fit 统计量。
4. **防护仅拦入场** — W1d 熔断在 `submit_order` 内、风控检查后、路由前介入，且只 gate 入场单，出场单永不被困。
5. **路径已校正** — 契约中的实际路径以各文档为准（如组合优化在 `engine/portfolio/optimizer.py` + `portfolio_opt.py`，非蓝图初稿路径）。

## 下一步

审阅 4 份契约后，按 Wave 1 编排启动实现：
- 4 个实现 agent 并行（worktree 隔离）
- 每特性 code-review agent 审查
- tsc/pytest + Docker 冒烟
- 汇总验收 → 推送
