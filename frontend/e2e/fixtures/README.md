# E2E 夹具：形状来源对照表

这些夹具**不是照着 `src/hooks/*.ts` 的 TS 类型手写的**。TS 类型是前端对后端的
*期望*，后端真实返回的才是*事实* —— 两者一旦分叉，照类型写出来的夹具会让 E2E
全绿而线上全崩。所以每份夹具都对应一个具体的 FastAPI 响应模型，改后端时用这张
表反查要同步改哪份夹具。

核对方式：逐个打开下表的后端文件，把 Pydantic 模型的**字段名、可空性、默认值**
与夹具对象逐字段比对；对于返回 `dict`（无 `response_model`）的端点，比对的是
handler 里 `return {...}` 的字面量键名与其数据源 dataclass。

| 夹具文件 · 导出 | 后端响应模型 | 定义位置 |
|---|---|---|
| `auth.ts` · `LOGIN_TOKEN` | `Token` | `backend/app/api/v1/endpoints/auth.py:73` |
| `auth.ts` · `CURRENT_USER` | `UserInfo` | `backend/app/api/v1/endpoints/auth.py:79` |
| `market.ts` · `MARKET_OVERVIEW` | `MarketOverviewResponse` / `MarketOverviewItem` | `backend/app/api/v1/endpoints/bars.py:74` / `:64` |
| `market.ts` · `SPOT_QUOTES` | `SpotQuotesResponse` / `SpotQuote` | `backend/app/api/v1/endpoints/bars.py:218` / `:202` |
| `market.ts` · `barsResponse()` | `BarsListResponse` / `BarResponse` | `backend/app/api/v1/endpoints/bars.py:47` / `:25` |
| `market.ts` · `latestBar()` | `BarResponse`（`/bars/latest` 返回 `BarResponse \| None`） | `backend/app/api/v1/endpoints/bars.py:121` |
| `market.ts` · `INDICATORS` | 无 `response_model`，形状见 handler 的 `result` 字典 | `backend/app/api/v1/endpoints/bars.py:451`（`{"time": [...], "rsi": [...]}`） |
| `screener.ts` · `SCREENER_RUN` | `ScreenerRunResponse` / `CandidateOut` | `backend/app/api/v1/endpoints/screener.py:93` / `:65` |
| `screener.ts` · `SCREENER_PRESETS` | `PresetOut` | `backend/app/api/v1/endpoints/screener.py:101` |
| `screener.ts` · `SCREENER_SECTORS` | `list[str]` | `backend/app/api/v1/endpoints/screener.py:140` |
| `screener.ts` · `SCREENER_MOVERS` | `MoversResponse` | `backend/app/api/v1/endpoints/screener.py:108` |
| `backtest.ts` · `STRATEGY_PRESETS` | `PRESET_STRATEGIES` 字面量（端点直接返回 `list[dict]`） | `backend/app/api/v1/endpoints/strategies.py:20` |
| `backtest.ts` · `BACKTEST_RESULT` | `BacktestResponse` / `BacktestMetricsResponse` | `backend/app/api/v1/endpoints/backtests.py:83` / `:53` |
| `backtest.ts` · `BACKTEST_RESULT.equity_curve` 等序列 | `build_report()` 的 `_equity_to_points` / `_series_to_points` | `backend/app/engine/backtest/report.py:101` / `:120` |
| `portfolio.ts` · `OPTIMIZE_RESULT` | `PortfolioOptResponse` | `backend/app/api/v1/endpoints/portfolio_opt.py:111` |
| `portfolio.ts` · `OPTIMIZE_RESULT.frontier[]` | `_compute_frontier()` 的 `{vol, ret, sharpe}` | `backend/app/engine/portfolio/optimizer.py:224` |
| `portfolio.ts` · `REBALANCE_PREVIEW` | `RebalancePreviewResponse` / `RebalanceLegSchema` | `backend/app/api/v1/endpoints/rebalance.py:82` / `:60` |
| `portfolio.ts` · `POSITIONS` | `PositionResponse` | `backend/app/api/v1/endpoints/positions.py:13` |
| `portfolio.ts` · `ACCOUNT` | `AccountResponse` | `backend/app/api/v1/endpoints/positions.py:24` |
| `portfolio.ts` · `ATTRIBUTION` | 无 `response_model`，见 handler 末尾的 `return {...}` | `backend/app/api/v1/endpoints/orders.py:304` |
| `factor.ts` · `FACTOR_LIST` | `AVAILABLE_FACTORS` 字面量 | `backend/app/quant/factor_analysis.py:249` |
| `factor.ts` · `FACTOR_ANALYSIS` | handler `return {...}`，序列形状见 `FactorAnalysisResult` | `backend/app/api/v1/endpoints/quant.py:292` / `backend/app/quant/factor_analysis.py:96` |
| `factor.ts` · `FORMULA_META` | `{features, operators, presets}` = `FEATURE_META` / `OP_META` / `PRESET_FORMULAS` | `backend/app/api/v1/endpoints/quant.py:321` / `backend/app/quant/formula_factor.py:433` |
| `factor.ts` · `FACTOR_FITNESS` | handler `return {...}` | `backend/app/api/v1/endpoints/factor_processors.py:255` |

## 刻意与真实响应不同的地方

1. **数据量**。真实 `/bars` 返回上百根 K 线、真实 `market-overview` 每市场几十只，
   夹具各只留几条。冒烟测的是流程能不能走通，不是渲染多少行。
2. **`generated_at` / `updated_at` 用固定时间戳**，不用 `new Date()`。测试要可复现，
   而且这些字段前端不做展示断言。
3. **数值挑成「好看」的**（正夏普、正收益）。这样结果面板走的是主分支，不会因为
   一个负夏普把 CTA 文案切成另一支导致断言失配 —— 那种失配调试起来纯浪费时间。
