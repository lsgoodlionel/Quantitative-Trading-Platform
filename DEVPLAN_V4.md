# QuantBot v4.0 — 四大开源框架对标升级蓝图

> 版本: v4.0 | 制定: 2026-08-07 | 基线: v2.0 已交付（118 端点 · 16 页 · 593 单测）· v3.0 蓝图已定未开工
> 对标物: **freqtrade** · **zipline** · **vnpy(VeighNa 4.4)** · **Lean(QuantConnect)**
> 配套: [DEVPLAN_V2.md](DEVPLAN_V2.md)（已交付）· [DEVPLAN_V3.md](DEVPLAN_V3.md)（串联/简化/AI 原生）· [HANDOFF.md](HANDOFF.md)
>
> **本蓝图与 V3 的关系**：V3 是**内部审计驱动**（把已有功能串起来、页面合并、AI 原生）；
> V4 是**外部框架对标驱动**（补齐引擎内核层面的能力缺口）。两者正交，**V4 的 K1/K2 是 V3 的 G1/G2 的前置条件**——
> 没有多标的 + 做空 + 目标权重执行的引擎内核，V3 的「因子一键变策略」「组合一键调仓」只能做半成品。
> 建议执行顺序：**V4 Wave K（引擎内核）→ V3 Wave A（串联）→ V4 Wave L/M → V3 Wave B/C**。

---

## 零、来源与读取方式

| 仓库 | 本地路径 | 读取方式 | 版本锚点 |
|------|---------|---------|---------|
| zipline-LHJY | `refs/zipline-LHJY/` | git clone（17MB，完整源码） | quantopian/zipline fork（`014f1fc` 上游 merge，未二次修改） |
| freqtrade-LHJY | `refs/freqtrade-LHJY/` | codeload zip（默认分支 `develop`） | **2026.8-dev**；注意本地 `refs/freqtrade/` 是 2026.5-dev，旧三个月 |
| vnpy-LHJY | `refs/vnpy-LHJY/` | codeload zip | VeighNa 4.4.0，与 `refs/vnpy/` 同源 |
| Lean-LHJY | `refs/Lean-LHJY/` | GitHub Trees/Raw API 全目录枚举 + 关键文件精读（仓库 586MB，C#） | master |

> 四个 LHJY 仓库均为**上游原版 fork**（无自有提交），因此功能对标以上游源码为准，
> 本地 `refs/` 中已有的同源完整副本（freqtrade / vnpy / zipline-reloaded）用作交叉验证。
> 网络对 GitHub 限速严重（~70KB/s），Lean 采用 API 枚举 + 定点精读，已覆盖其全部 21 个顶层模块。

### 全量源码到位后的交叉核对（2026-08-07）

`refs/Lean-LHJY/` 已完成完整下载（286MB / 4828 个源文件），对 API 枚举阶段的结论做了逐条复核：

| 蓝图原结论 | 核对结果 |
|---|---|
| A3「Lean 10 种订单类型」 | ❌ **实为 12 种**：Market · Limit · StopMarket · StopLimit · MarketOnOpen · MarketOnClose · TrailingStop · **LimitIfTouched** · OptionExercise · ComboMarket · ComboLimit · ComboLegLimit。<br>注：§1.4 的详表本就列全了 12 种，是 A3 缺口表自己少算 —— 属**蓝图内部不一致**，非调研遗漏 |
| E5「`Algorithm.Framework/Risk/` 5 个模型」 | ✅ 确认 5 个，且名称与描述一一对应（每标的最大回撤 / 组合最大回撤 / 行业敞口 / 未实现盈利上限 / 追踪止损） |
| L3「VWAP/Spread/StandardDeviation 执行模型」 | ✅ `Algorithm.Framework/Execution/` 恰为这 3 个 |
| E3「Lean 有 SectorWeighting / MeanReversion PCM」 | ✅ 两者均在 `Algorithm.Framework/Portfolio/`（该目录共 16 个模型/优化器） |

**freqtrade 补充核对**（`refs/freqtrade-LHJY/` 已下完，**2026.8-dev**）：

⚠️ 本地既有的 `refs/freqtrade/` 是 **2026.5-dev**，比上游 develop **旧三个月** ——
零章原表「与 `refs/freqtrade/` 同源」的说法不准确，蓝图的 freqtrade 结论建立在 2026.5-dev 上。
按新版本逐项复核后：

| 蓝图原结论 | 核对结果 |
|---|---|
| 19 个 Pairlist 插件 | ✅ `IPairList` 子类恰为 19 个（目录里另有基类与 helper，数文件会得到 21，别数错） |
| 4 个 Protection | ✅ 恰为 4 个（第 5 个文件是 `iprotection.py` 基类） |
| **11 个 hyperopt loss 函数，「含 SQN」** | ❌ **实为 12 个，且其中没有 SQN**。SQN 只出现在 `data/metrics.py` 与报告/RPC 层，是**报告指标**而非可优化目标。实际 12 个是：sharpe · sharpe_daily · sortino · sortino_daily · calmar · max_drawdown · max_drawdown_relative · max_drawdown_per_pair · profit_drawdown · multi_metric · onlyprofit · short_trade_dur |

**两个对后续 Wave 有实质影响的新发现**：

1. **`LimitIfTouched` 是 waveKa 契约漏掉的订单类型**。它与 STOP_LIMIT 镜像（STOP 是「突破触发」，
   LIT 是「回落触发」），常用于挂单抄底/摸顶。§1.4 详表里它在，写契约时却没带过去，
   而契约的「不做」清单只排除了 Combo 与 OptionExercise —— 所以这是**契约撰写疏漏，
   不是有意取舍，也不是调研没查到**。补做成本低（复用 STOP_LIMIT 的触发+限价两段结构），
   建议并入 Wave L。
2. **Lean 的 Framework 模型每个 `.cs` 都配有同名 `.py`**。这意味着 K-d/Wave L 的
   AlphaModel / PCM / RiskModel / ExecutionModel **可直接移植 Python 实现**，
   而非从 C# 翻译 —— 工作量与出错率都显著低于契约的估计。

---

## 一、四框架能力画像

### 1.1 freqtrade — 「单机实盘交易机器人的完成度天花板」

| 维度 | 关键机制 | 源码位置 |
|------|---------|---------|
| 策略接口 | `IStrategy` 暴露 **40+ 钩子**：`custom_stoploss` / `custom_roi` / `adjust_trade_position`(DCA+部分平仓) / `confirm_trade_entry\|exit` / `custom_entry_price\|exit_price` / `adjust_order_price` / `check_entry\|exit_timeout` / `leverage` / `informative_pairs`(多周期) / `bot_loop_start` | `freqtrade/strategy/interface.py` |
| 回测 | 多 pair 同时回测；`time_pair_generator` 双层时间×标的循环；detail 数据（低周期精撮合）；订单替换/取消/超时；funding fee | `freqtrade/optimize/backtesting.py` |
| 参数优化 | Optuna 驱动，**12 个内置 loss 函数**（Sharpe/Sortino(±daily)/Calmar/MaxDD(3 变体)/ProfitDrawdown/MultiMetric/OnlyProfit/ShortTradeDur；**不含 SQN**，SQN 是报告指标）+ epoch 过滤器 | `freqtrade/optimize/hyperopt/`, `hyperopt_loss/` |
| 偏差检测 | **lookahead-bias 分析器** + **recursive-bias 分析器**（独立命令，非事后统计） | `freqtrade/optimize/analysis/lookahead.py`, `recursive.py` |
| 标的池 | **19 个可链式组合的 Pairlist 插件**：VolumePairList / MarketCapPairList / PercentChange / Volatility / RangeStability / Spread / Age / Price / Precision / Performance / Shuffle / Offset / FullTrades / Remote / Producer / CrossMarket / Delist | `freqtrade/plugins/pairlist/` |
| 熔断 | 4 个 Protection：StoplossGuard / MaxDrawdown / LowProfitPairs / CooldownPeriod | `freqtrade/plugins/protections/` |
| 自适应 ML | **FreqAI**：滚动再训练 + 漂移检测 + 特征工程钩子 + PyTorch/XGBoost/LightGBM/RL（5 种动作空间环境） | `freqtrade/freqai/` |
| 报告 | 按 pair / entry_tag / exit_reason / 周期 多维分组统计；SQN、expectancy、streak、underwater、CAGR；拒绝信号统计 | `freqtrade/optimize/optimize_reports/`, `data/metrics.py` |
| 数据 | feather/parquet/json 三种 handler；trades→ohlcv 转换；**orderflow（逐笔盘口）**；数据补齐 | `freqtrade/data/history/datahandlers/`, `data/converter/` |
| 运维 | Telegram（全指令）/ Discord / Webhook / WebSocket 消息总线（生产者-消费者跨实例） | `freqtrade/rpc/` |

### 1.2 zipline — 「机构级事件驱动 + Pipeline 截面计算的范式」

| 维度 | 关键机制 | 源码位置 |
|------|---------|---------|
| Pipeline | **声明式截面因子图**：Factor/Filter/Classifier 三类项 + DAG 引擎 + 分块执行 + `domain` 多市场；内置 30+ 因子（含 RollingPearson/Spearman/LinearRegression/SimpleBeta） | `zipline/pipeline/` |
| 资产体系 | Equity / Future / **ContinuousFuture(连续合约+滚动规则)** / ExchangeInfo / 资产生命周期（上市退市边界） | `zipline/assets/` |
| 数据 | bcolz 日线 + minute bars + HDF5；**adjustments（拆股/分红/合并）**；`data_portal` 统一取数；history 窗口加载器；FX 汇率 | `zipline/data/` |
| 撮合 | Blotter 抽象；**7 种滑点模型**（VolumeShare / MarketImpact / VolatilityVolumeShare / FixedBasisPoints…）；**8 种佣金模型**（PerShare/PerContract/PerTrade/PerDollar…）；CancelPolicy | `zipline/finance/` |
| 交易控制 | **TradingControl**：MaxOrderCount / MaxOrderSize / MaxPositionSize / LongOnly / AssetDateBounds / RestrictedList；**AccountControl**：MaxLeverage / MinLeverage | `zipline/finance/controls.py` |
| 指标 | metrics 插件化：Returns / PNL / CashFlow / AlphaBeta / MaxLeverage / BenchmarkReturnsAndVolatility / 经典风险指标集 | `zipline/finance/metrics/` |
| 调度 | `schedule_function` + 日历事件规则（月初/周末/开盘后 N 分钟…） | `zipline/utils/events.py` |
| 日历 | 交易日历 + 分钟级 session/minute 映射 | `zipline/utils/calendars.py` |

### 1.3 vnpy(VeighNa 4.4) — 「国内多市场实盘 + AI 因子投研一体化」

| 维度 | 关键机制 | 源码位置 |
|------|---------|---------|
| 网关 | **20+ 交易接口**：CTP/Mini/SOPT/XTP/Tora/EMT/OST（A股+期货+ETF期权）、IB/TAP/DA（海外）、RQData/迅投研（行情） | 独立包 `vnpy_*` |
| 应用 | cta_strategy / cta_backtester / **spread_trading(价差)** / **option_master(期权定价+波动率曲面+希腊值)** / portfolio_strategy / algo_trading(TWAP/Sniper/Iceberg/BestLimit) / script_trader / paper_account / **portfolio_manager(子账户)** / risk_manager(流控) / data_manager / data_recorder / web_trader | 独立包 |
| **alpha.dataset** | 表达式特征引擎（polars）：**ts_* 23 个时序算子** + **cs_* 5 个截面算子** + math/ta 函数；**Alpha158**（qlib 移植）+ **Alpha101**（WorldQuant）；processor（缺失值/去极值/标准化/特征删除）；train/valid/test 分段 | `vnpy/alpha/dataset/` |
| **alpha.model** | 统一 `AlphaModel` 模板：Lasso / LightGBM / MLP；一致 API 便于横向对比 | `vnpy/alpha/model/` |
| **alpha.strategy** | **截面多标的**与**时序单标的**两类策略模板；组合级回测引擎（含逐合约/组合日盈亏、撮合、基准对比） | `vnpy/alpha/strategy/backtesting.py`(944行) |
| **alpha.lab** | **投研产物管理**：`save/load_dataset` `save/load_model` `save/load_signal`；指数成分股跟踪（component data + filters）；合约设置 | `vnpy/alpha/lab.py` |
| 数据库 | 7 种适配器：SQLite/MySQL/PostgreSQL/QuestDB/DolphinDB/TDengine/MongoDB | `vnpy/trader/database.py` |
| 事件引擎 | 轻量事件驱动内核 + RPC 跨进程 | `vnpy/event/`, `vnpy/rpc/` |

### 1.4 Lean(QuantConnect) — 「多资产类别 + 五模块算法框架的工业级实现」

| 维度 | 关键机制 | 源码位置 |
|------|---------|---------|
| **算法框架** | **五模块可插拔**：`Selection`(24 个宇宙选择模型) → `Alpha`(输出 **Insight**) → `Portfolio`(输出 **PortfolioTarget**，15 个构建模型) → `Risk`(5 个风控模型) → `Execution`(3 个执行模型) | `Algorithm.Framework/` |
| Alpha 模型 | Constant / EmaCross / HistoricalReturns / Macd / Rsi / BasePairsTrading / PearsonCorrelationPairsTrading | `Algorithm.Framework/Alphas/` |
| 组合构建 | EqualWeighting / InsightWeighting / ConfidenceWeighted / Accumulative / **BlackLitterman** / **MeanVariance** / **RiskParity** / **MeanReversion(OLMAR)** / **SectorWeighting** / MaximumSharpe / MinimumVariance / Unconstrained MV | `Algorithm.Framework/Portfolio/` |
| 风控模型 | MaximumDrawdownPercentPerSecurity / PerPortfolio / **TrailingStop** / MaximumUnrealizedProfitPercentPerSecurity / **MaximumSectorExposure** | `Algorithm.Framework/Risk/` |
| 执行模型 | Spread / StandardDeviation / **VolumeWeightedAveragePrice**（含 `maximum_order_quantity_percent_volume` 成交量约束） | `Algorithm.Framework/Execution/` |
| 订单类型 | Market / Limit / StopMarket / StopLimit / **LimitIfTouched** / **TrailingStop** / **MarketOnOpen** / **MarketOnClose** / **Combo(Market/Limit/LegLimit)** / OptionExercise + **TimeInForce**(GTC/Day/GTD) | `Common/Orders/` |
| 撮合/费用 | Fill 模型（Equity/Future/FutureOption/Immediate/LatestPrice）；**35 个券商费率模型**；滑点（Constant/VolumeShare/**MarketImpact**） | `Common/Orders/Fills\|Fees\|Slippage/` |
| 证券模型 | **BuyingPowerModel**（保证金/杠杆/现金账户）· MarginCallModel · SettlementModel(T+N) · ShortableProvider · 多资产：Equity/Option/Future/FutureOption/IndexOption/Forex/Cfd/Crypto/CryptoFuture | `Common/Securities/` |
| 指标库 | **200+ 指标**（含 CandlestickPatterns 全套、HurstExponent、ARIMA、Greeks、AdvanceDecline 市场宽度类） | `Indicators/` |
| 报告 | 29 个 ReportElement：CAGR/PSR/Sharpe/Sortino/InformationRatio/**EstimatedCapacity(容量)**/**Turnover(换手)**/**LeverageUtilization**/**Crisis(危机时期)**/**MaxDrawdownRecovery**/RollingBeta/RollingSharpe/AssetAllocation/Exposure | `Report/ReportElements/` |
| 优化 | GridSearch / **EulerSearch**（自适应细化） | `Optimizer/Strategies/` |
| 研究 | `QuantBook` — 回测引擎直接作为 Notebook 研究后端（同一份数据/指标代码） | `Research/` |

---

## 二、QuantBot 现状基线（2026-08-07 代码实测）

**规模**：后端 33,078 行 Python / 237 个 `.py`；前端 25,300 行 TS/TSX；118 端点；593 单测。

**已建成且达到或超过对标框架水平的部分**（不在本蓝图升级范围）：

| 能力 | 证据 |
|------|------|
| 因子库 Alpha158 风格 | `quant/factor_lib/loader.py` — 11 个分组 × 多窗口族 + KBAR 形态族，含 Slope/Rsquare/Resi/Corr/Cord/Quantile/IdxMax 等 qlib 同款 |
| 遗传因子挖掘 | `quant/mining/genetic.py` + `expression_tree.py`（RPN 表达式树 + 交叉/变异） |
| 组合优化 | `engine/portfolio/optimizer.py:47` 8 种方法：MaxSharpe / MinVol / **RiskParity** / **MinCVaR** / **MinCDaR** / EqualWeight / **HRP** / **BlackLitterman** + Ledoit-Wolf 风险模型 + **离散分配** + TopkDropout |
| 交易质量指标 | `engine/backtest/metrics.py:27-57` 已含 **SQN** / **expectancy** / **profit_factor** / **max_consecutive_wins\|losses** / omega；`drawdown_periods.py` 已含 **underwater**；报告含 **CAGR** — **已对齐 freqtrade** |
| Hyperopt 目标函数 | `engine/backtest/hyperopt.py:104` **11 个 loss**（sharpe/sortino/calmar/omega/sqn/profit/annual/max_drawdown/profit_drawdown/profit_factor/multi_metric）— **与 freqtrade 的 11 个持平** |
| 回测稳健性 | `significance.py`（显著性检验）+ `mc_robustness.py`（逐笔蒙特卡洛）+ `bias_detection.py` + `walkforward.py` + `hyperopt.py` — **强于 zipline/vnpy** |
| 回测报告 | tearsheet / roundtrips / tag_metrics / periodic_stats / rolling_stats / drawdown_periods / trade_analytics |
| 熔断防护 | `oms/protections/` 4 件套（freqtrade 同款）+ 已接入下单前后 |
| 订单算法 | `oms/algos/` TWAP / VWAP / Iceberg |
| 平台化 | RBAC 三级 + 审计日志 + 多源数据通道动态切换 + Telegram/Webhook 通知 |
| 数据广度 | 6 源（Alpaca/富途/yfinance/AkShare/Stooq/Demo）+ 基本面 + 新闻 + 日历 + 期权链 + TimescaleDB |

---

## 三、缺口矩阵（按能力域，含代码证据与可复用源码）

> 差距等级：🔴 结构性缺失（引擎内核层，影响后续所有上层能力）· 🟠 功能缺失 · 🟡 实现不完善

### 域 A — 回测引擎内核

| # | 缺口 | QuantBot 现状（证据） | 对标实现 | 等级 |
|---|------|---------------------|---------|------|
| A1 | **多标的组合回测** | `engine/backtest/engine.py:83` `run(strategy, bars, ...)` 只接单一 symbol 的 bar 序列；全库无 `portfolio_backtest` | freqtrade `time_pair_generator` 双层循环 · vnpy `alpha/strategy/backtesting.py` PortfolioDailyResult · zipline/Lean 原生组合 | 🔴 |
| A2 | **做空 / 双向持仓** | `backtest/broker.py` 只有 BUY/SELL 且 `sell_all` 依赖 `pos.qty > 0`；16 个 preset 全为多头满仓 | freqtrade `LongShort` + `can_short` · Lean `PositionSide` · zipline `LongOnly` 控制（可关） | 🔴 |
| A3 | **回测订单类型** | `broker.py:Order` **无 `order_type` 字段**，仅 next-bar open 市价撮合 | Lean **12** 种订单类型 + TimeInForce · zipline `execution.py` | 🔴 |
| A4 | **交易日历 / 交易时段** | 全库 grep `trading_calendar\|market_calendar` **零命中**；bar 序列直接顺序迭代 | zipline `utils/calendars.py` · Lean `Data/market-hours/` | 🟠 |
| A5 | **复权 / 公司行为** | 仅有分红**日历展示**端点（`endpoints/calendar.py`），回测不做价格调整 | zipline `data/adjustments.py`(拆股/分红/合并) · Lean `Data/Auxiliary/` | 🟠 |
| A6 | **多周期（informative）** | `Bar` 有 `MIN_1/DAY_1` 枚举，但策略上下文只有单一 `history` | freqtrade `informative_pairs` + `@informative` 装饰器 · Lean Consolidators | 🟠 |
| A7 | **保证金 / 杠杆 / 期货** | `gateway/futures_base.py` 存在但回测 broker 无保证金概念 | Lean `Securities/BuyingPowerModel` · freqtrade `leverage/liquidation_price.py` | 🟡 |
| A8 | **成交量约束撮合** | `slippage.py` 有滑点但不限制单 bar 最大成交量 → 可无限量成交 | zipline `VolumeShareSlippage`(`LiquidityExceeded`) · Lean `OrderSizing.get_order_size_for_percent_volume` | 🟡 |

### 域 B — 策略接口与交易语义

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| B1 | **策略级止损/止盈/追踪止损** | grep `stoploss\|trailing\|minimal_roi` 在 `app/strategy/` **零命中**（只有 OMS 的 exit_reason 字符串推断） | freqtrade `custom_stoploss`/`ft_stoploss_reached`/`minimal_roi`/`custom_roi` · Lean `TrailingStopRiskManagementModel` | 🔴 |
| B2 | **仓位调整（DCA / 部分平仓）** | 无。`context.py` 只有 `buy/sell/buy_value/sell_all` | freqtrade `adjust_trade_position` + `_adjust_trade_position_internal` | 🟠 |
| B3 | **下单确认钩子** | 无 | freqtrade `confirm_trade_entry\|exit` / `custom_entry_price\|exit_price` / `adjust_order_price` | 🟠 |
| B4 | **未成交订单超时/替换** | `broker.py` 挂单只在下一 bar 撮合或 `cancel_all_pending` | freqtrade `check_entry\|exit_timeout` + `check_order_replace` | 🟡 |
| B5 | **Alpha/Insight 抽象** | 策略直接下单，信号与执行耦合 → **这正是 V3 记录的「因子挖掘→策略」断链的根因** | Lean `AlphaModel → Insight → PortfolioConstructionModel → PortfolioTarget → ExecutionModel` | 🔴 |
| B6 | **策略级标的锁定** | protections 在 OMS 层，策略无 `lock_pair` API | freqtrade `lock_pair`/`unlock_reason`/`is_pair_locked` | 🟡 |

### 域 C — 数据层

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| C1 | **本地历史数据归档（bundle）** | DataService 缓存优先 + feed 回退链，但**无离线数据集**；回测每次依赖在线源 | freqtrade `data/history/datahandlers/`(feather/parquet/json) + `download-data` 命令 · vnpy `AlphaLab.save_bar_data` · zipline bundles | 🟠 |
| C2 | **数据质量检查与缺口补齐** | 无 | freqtrade `history_utils.py` 缺口检测 + 增量补齐 | 🟠 |
| C3 | **逐笔/盘口数据** | 仅 OHLCV bar | freqtrade `data/converter/orderflow.py` · Lean Tick/QuoteBar | 🟡 |
| C4 | **资产元数据体系** | `data/symbol_dict.py` 静态字典 | zipline `assets/`（生命周期/退市/连续合约） · Lean SymbolProperties DB | 🟡 |

### 域 D — 因子与研究

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| D1 | **截面算子进公式引擎** | `quant/formula_factor.py` 的 `OPS` 全为时序/逐元素算子（RANK/ZSCORE 是**滚动**版本）；截面处理只在 `processors.py` 独立管线 | vnpy `alpha/dataset/cs_function.py`（cs_rank/cs_mean/cs_std/cs_sum/cs_scale） · zipline Pipeline 原生截面 | 🟠 |
| D2 | **Alpha101 因子集** | 仅 Alpha158 风格 | vnpy `alpha/dataset/datasets/alpha_101.py`（330 行，可直接移植） | 🟠 |
| D3 | **投研产物版本管理** | `quant/experiments/recorder.py` 只存指标记录（Redis，有容量上限） | vnpy `AlphaLab` save/load dataset·model·signal | 🟠 |
| D4 | **统一 ML 模型模板** | `quant/ml_strategy.py` / `double_ensemble.py` / `models/sequence.py` 各自为政 | vnpy `AlphaModel` 统一模板（Lasso/LGB/MLP 同 API） · freqtrade `IFreqaiModel` | 🟡 |
| D5 | **自适应再训练 / 漂移检测** | 无 | freqtrade FreqAI `data_drawer` + `data_kitchen`（滚动再训练 + DI 漂移指标） | 🟠 |
| D6 | **Pipeline 式声明因子图** | 因子逐个计算，无 DAG / 依赖复用 | zipline `pipeline/engine.py` | 🟡 |

### 域 E — 组合构建与执行

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| E1 | **PortfolioTarget → 执行通路** | `PortfolioOptimizer.tsx:799` 只有 `<Link to="/orders">`，用户手抄下单（V3 G2 已记录） | Lean `PortfolioTarget` + `ExecutionModel` + `OrderSizing.get_unordered_quantity` | 🔴 |
| E2 | **常驻再平衡调度** | 优化是一次性 API 调用，无定时/触发式再平衡 | Lean `set_rebalancing_func`(Resolution/timedelta/DateRules) | 🟠 |
| E3 | **行业权重 / 均值回归 组合** | 8 种方法齐备（`optimizer.py:47` 含 RiskParity/CVaR/CDaR/HRP/BL）；缺 **SectorWeighting（行业权重约束）** 与 **MeanReversion(OLMAR)** | Lean `SectorWeightingPortfolioConstructionModel` / `MeanReversionPortfolioConstructionModel` | 🟡 |
| E4 | **交易控制（TradingControl）** | `oms/manager.py:_pre_trade_risk_check` 单点检查，无可组合控制族 | zipline `finance/controls.py`（8 个控制器，**可直接移植**） | 🟠 |
| E5 | **组合级风控模型** | `risk/engine.py` 有 VaR/组合风险，但无「每标的最大回撤/未实现盈利上限/行业敞口」自动减仓 | Lean `Algorithm.Framework/Risk/` 5 个模型 | 🟠 |
| E6 | **执行模型抽象** | TWAP/VWAP/Iceberg 是**订单算法**，不是「目标权重驱动的执行模型」 | Lean `Execution/`（Spread/StandardDeviation/VWAP，消费 PortfolioTarget） | 🟠 |

### 域 F — 标的池与筛选

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| F1 | **Pairlist 插件化链式过滤** | `data/pairlist.py` 有 `PairlistRule` + `apply_chain`，但规则是**内置字典驱动**，非可注册插件；覆盖约 6 个指标 | freqtrade 19 个 pairlist 插件 + `pairlistmanager` + `pairlist_resolver` | 🟡 |
| F2 | **市值/成分股宇宙** | 无 MarketCap 榜单、无指数成分股 | freqtrade `MarketCapPairList` · Lean `QC500UniverseSelectionModel`/`ETFConstituents` · vnpy `load_component_symbols` | 🟠 |
| F3 | **基本面驱动宇宙选择** | screener 只用自身 panel 快照（V3 G4 已记录） | Lean `CoarseFundamental → FineFundamental` 两段式 | 🟠 |

### 域 G — 报告与分析

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| G1 | **容量估计 / 换手率 / 杠杆利用率** | 无 | Lean `EstimatedCapacityReportElement` / `TurnoverReportElement` / `LeverageUtilizationReportElement` | 🟠 |
| G2 | **危机时期表现 / 回撤恢复期** | 有 `drawdown_periods.py`，无危机区间对照、无恢复期统计 | Lean `Crisis.cs` + `MaxDrawdownRecoveryReportElement` | 🟡 |
| G3 | **进出场原因归因分析** | 有 `tag_metrics.py` 按 tag 分组，无「信号被拒绝」统计与进出场组合归因 | freqtrade `data/entryexitanalysis.py` + `generate_rejected_signals` | 🟡 |
| ~~G4~~ | ~~SQN / expectancy / streak~~ | ✅ **已实现**：`metrics.py:27-57` 含 sqn/expectancy/profit_factor/max_consecutive_wins\|losses；`drawdown_periods.py` 含 underwater；报告含 CAGR | — | ✅ |
| ~~G5b~~ | ~~Hyperopt loss 函数族~~ | ✅ **已实现**：`hyperopt.py:104` 11 个 loss，与 freqtrade 持平 | — | ✅ |
| G5 | **前瞻/递归偏差专项分析器** | 有 `bias_detection.py`（事后统计） | freqtrade `analysis/lookahead.py` + `recursive.py`（**主动注入式检测**，更严格） | 🟡 |

### 域 H — 工程与运维

| # | 缺口 | QuantBot 现状 | 对标实现 | 等级 |
|---|------|--------------|---------|------|
| H1 | **CI/CD** | 无（V3 J1 已列） | 三家均有完整 GH Actions | 🟠 |
| H2 | **CLI 工具链** | 只有 Makefile | freqtrade `commands/`（17 个子命令：download-data/backtesting/hyperopt/plot/list-*/new-strategy…） | 🟡 |
| H3 | **策略热加载/解析器** | `strategy/registry` 静态注册 16 个 preset | freqtrade `resolvers/iresolver.py`（目录扫描 + 动态加载用户策略） | 🟠 |
| H4 | **策略模板生成** | 无 | freqtrade `templates/` + `deploy_commands.py` | 🟡 |
| H5 | **通知事件覆盖面** | `dispatch_event` 仅 `tasks/notify.py` 与 OMS 调用；7 种事件类型 | freqtrade `RPCMessageType` 全生命周期覆盖 | 🟠 |

---

## 四、v4.0 Epic 定义

> 命名接续 V3（G/H/I/J 已占用），v4.0 使用 **K / L / M / N / O**。

### Epic K — 引擎内核重构（Core Engine）🔴 最高优先级

> **一句话**：把「单标的·只做多·只市价·信号即下单」的回测内核，升级为
> 「多标的·可做空·多订单类型·信号→目标→执行 三段式」的组合引擎。这是所有上层能力的地基。

| # | 特性 | 内容 | 复用来源 | 复杂度 |
|---|------|------|---------|--------|
| **K1** | **组合回测引擎** | 新增 `engine/backtest/portfolio_engine.py`：`run(strategy, bars_by_symbol, ...)`，双层时间×标的循环；`PortfolioBroker` 管理多标的持仓/现金/组合净值；`ContractDailyResult`+`PortfolioDailyResult` 日度盈亏拆解。**保留现有单标的 `BacktestEngine` 作为其特例包装**（零破坏迁移） | vnpy `alpha/strategy/backtesting.py:112-935` · freqtrade `backtesting.py:1573 time_pair_generator` | L |
| **K2** | **做空与双向持仓** | `Position` 增加 `direction`；`SimulatedBroker` 支持负仓位、卖空开仓/买入平仓、做空成本与利息；`StrategyContext` 增加 `short()`/`cover()`；`LongOnly` 作为可选交易控制而非硬约束 | zipline `finance/position.py` + `controls.py:LongOnly` · Lean `PositionSide` | M |
| **K3** | **订单类型体系** | `Order` 增加 `order_type`/`limit_price`/`stop_price`/`trailing_amount`/`time_in_force`；实现 Market / Limit / StopMarket / StopLimit / TrailingStop / MarketOnOpen / MarketOnClose；撮合器按类型分派 | Lean `Common/Orders/*.cs` 语义 · zipline `finance/execution.py` | M |
| **K4** | **策略级止损/ROI/追踪止损** | `StrategyBase` 增加 `minimal_roi` 表、`stoploss`、`trailing_stop`(+offset)、`custom_stoploss(ctx, trade, current_profit)` 钩子；引擎在每 bar 先执行 ROI/止损检查再调 `on_bar` | freqtrade `interface.py:1516 ft_stoploss_adjust` / `1590 ft_stoploss_reached` / `1695 min_roi_reached` | M |
| **K5** | **Alpha/Insight → Target → Execution 三段式** | 新增 `engine/framework/`：`Insight`(symbol/direction/period/magnitude/confidence/weight) · `AlphaModel` 基类 · `PortfolioConstructionModel` 基类（产出 `PortfolioTarget`） · `RiskManagementModel` 基类 · `ExecutionModel` 基类。**现有 16 个 preset 用 `LegacyStrategyAlphaAdapter` 包装接入，零重写** | Lean `Algorithm.Framework/`（Python 版可直接翻译） | L |
| **K6** | **成交量约束与市场冲击** | 撮合限制单 bar 成交量占比（默认 2.5%，可配）；新增 `VolumeShareSlippage` / `MarketImpactSlippage` | zipline `finance/slippage.py:247,377,524`（**可直接移植**） | S |
| **K7** | **交易日历** | `engine/calendar/`：US/HK/A 三市场交易日与时段；回测按日历对齐、跳过非交易日；A 股午休处理 | zipline `utils/calendars.py` · Lean `Data/market-hours/` | M |
| **K8** | **复权与公司行为** | 拆股/分红调整因子表 + 回测取数时应用；`data/adjustments.py` | zipline `data/adjustments.py` | M |

### Epic L — 交易控制与风控模型（Controls & Risk）

| # | 特性 | 内容 | 复用来源 | 复杂度 |
|---|------|------|---------|--------|
| **L1** | **TradingControl 族** | `engine/controls/`：MaxOrderCount / MaxOrderSize / MaxPositionSize / LongOnly / AssetDateBounds / RestrictedList / MaxLeverage / MinLeverage；回测与实盘 OMS **共用同一套控制器** | zipline `finance/controls.py`（**几乎可直接复制**，8 个类 ~450 行） | M |
| **L2** | **组合风控模型** | `engine/framework/risk/`：MaximumDrawdownPerSecurity / MaximumDrawdownPortfolio / TrailingStop / MaximumUnrealizedProfit / MaximumSectorExposure，输出减仓 `PortfolioTarget` | Lean `Algorithm.Framework/Risk/*.py`（**Python 源码，可直接翻译**） | M |
| **L3** | **执行模型** | `engine/framework/execution/`：Immediate / VWAP（含成交量占比约束）/ StandardDeviation / Spread；消费 `PortfolioTarget`，产出订单；**实盘复用现有 `oms/algos/` 作为底层** | Lean `Algorithm.Framework/Execution/*.py` | M |
| **L4** | **仓位调整钩子** | `adjust_position(ctx, trade, current_profit)` → 返回增量金额（正=加仓/DCA，负=部分平仓） | freqtrade `interface.py:650 adjust_trade_position` | S |
| **L5** | **下单确认与价格自定义钩子** | `confirm_entry\|exit` / `custom_entry_price\|exit_price` / `entry\|exit_timeout` | freqtrade `interface.py:354-560,300-340` | S |

### Epic M — 数据与因子深化（Data & Alpha）

| # | 特性 | 内容 | 复用来源 | 复杂度 |
|---|------|------|---------|--------|
| **M1** | **本地数据归档** | `data/archive/`：parquet handler（读写/列出/缺口检测/增量补齐）+ `POST /data/download` 端点 + Celery 任务；回测优先读归档，miss 才走在线源 | freqtrade `data/history/datahandlers/parquetdatahandler.py` + `history_utils.py` · vnpy `AlphaLab.save_bar_data` | M |
| **M2** | **截面算子进公式引擎** | `formula_factor.OPS` 增加 `CS_RANK`/`CS_ZSCORE`/`CS_SCALE`/`CS_MEAN`/`CS_STD`/`CS_DEMEAN`；公式引擎支持 panel 上下文（多标的同时求值）；遗传挖掘因此可搜索**截面 alpha** | vnpy `alpha/dataset/cs_function.py`（**64 行，可直接移植**） | M |
| **M3** | **Alpha101 因子集** | `quant/factor_lib/alpha101.py` — WorldQuant 101 因子，注册进因子库，与 Alpha158 并列可选 | vnpy `alpha/dataset/datasets/alpha_101.py`（330 行） | M |
| **M4** | **投研产物库（Lab）** | `quant/lab/`：dataset / model / signal 三类产物的保存·加载·列出·删除·版本；落 TimescaleDB + 对象存储（本地目录）；实验记录器升级为引用产物 ID | vnpy `alpha/lab.py`（480 行，接口可直接对齐） | M |
| **M5** | **统一 ML 模型模板** | `quant/models/template.py` 统一 `AlphaModel` 接口（fit/predict/detail），把现有 ml_strategy / double_ensemble / sequence 收编；新增 Lasso / LightGBM 实现 | vnpy `alpha/model/template.py` + `models/` | M |
| **M6** | **自适应再训练 + 漂移检测** | Celery 定时滚动再训练；DI（Dissimilarity Index）漂移指标 + 异常样本剔除 | freqtrade `freqai/data_kitchen.py`(DI 计算) + `data_drawer.py` | L |
| **M7** | **市值/成分股宇宙** | MarketCap 榜单（yfinance/AkShare）+ 指数成分股跟踪（历史变化）；接入 screener 与 pairlist | freqtrade `MarketCapPairList.py` · vnpy `load_component_symbols/filters` | M |

### Epic N — 报告与分析补全（Analytics）

| # | 特性 | 内容 | 复用来源 | 复杂度 |
|---|------|------|---------|--------|
| ~~N1~~ | ~~交易质量指标补全~~ | ✅ **核查后取消**：SQN/expectancy/streak/underwater/CAGR/profit_factor 均已在 `metrics.py` + `drawdown_periods.py` 实现 | — | ✅ |
| **N2** | **容量·换手·杠杆** | 策略容量估计（基于 ADV 与冲击成本）· 换手率 · 杠杆利用率曲线 | Lean `Report/ReportElements/EstimatedCapacity\|Turnover\|LeverageUtilization` | M |
| **N3** | **危机时期与回撤恢复** | 内置危机区间（2008/2015A股/2020疫情/2022加息…）分段表现对照 + 最大回撤恢复期 | Lean `Report/Crisis.cs` + `MaxDrawdownRecoveryReportElement` | S |
| **N4** | **进出场归因 + 拒绝信号** | entry_tag × exit_reason 交叉归因表；记录「产生信号但被过滤/拒绝」的统计 | freqtrade `data/entryexitanalysis.py` + `generate_rejected_signals` | M |
| **N5** | **主动式偏差检测** | 在现有 `bias_detection.py` 之外，新增 freqtrade 式**注入检测**：截断数据重跑对比（lookahead）+ 起始点敏感性（recursive） | freqtrade `optimize/analysis/lookahead.py` + `recursive.py` | M |
| ~~N6~~ | ~~Hyperopt loss 函数族~~ | ✅ **核查后取消**：`hyperopt.py:104` 已有 11 个 loss，与 freqtrade 持平 | — | ✅ |

### Epic O — 工程与可扩展性（Platform）

| # | 特性 | 内容 | 复用来源 | 复杂度 |
|---|------|------|---------|--------|
| **O1** | **CI/CD**（= V3 J1） | GH Actions：ruff + mypy + pytest(cov≥80) / tsc + eslint + build；PR 门禁 | 三家通用 | S |
| **O2** | **用户策略热加载** | `strategy/resolver.py`：扫描 `user_data/strategies/` 动态加载；策略上传端点 + 沙箱校验 | freqtrade `resolvers/iresolver.py` + `strategy_resolver.py` | M |
| **O3** | **CLI 工具链** | `python -m app.cli`：download-data / backtest / hyperopt / list-strategies / new-strategy | freqtrade `commands/` | S |
| **O4** | **通知事件全覆盖**（= V3 G5） | 新增事件：回测完成 / 优化完成 / 挖掘完成 / 再训练完成 / 数据源降级 / 数据缺口 / 对账差异 / 再平衡执行 | freqtrade `rpc/rpc_types.py` | S |
| **O5** | **多资产扩展预留** | `data/models` 增加 asset_class；期货连续合约与期权链的最小可用支持（承接已有 `gateway/futures_base.py`、`options_provider.py`） | zipline `assets/continuous_futures.pyx` · Lean `Securities/Future\|Option` | L |

---

## 五、交付节奏（三波，与 V3 交错）

```
Wave K（引擎内核，3-4 周）  ──▶  V3 Wave A（串联闭环）  ──▶  Wave L+M（控制/数据因子，4-5 周）
                                                          ──▶  V3 Wave B（信息架构+Copilot）
                                                          ──▶  Wave N+O（分析/工程，3 周）+ V3 Wave C
```

### 🌊 Wave K — 引擎内核（3-4 周｜必须先行）

> **不加任何 UI，只改引擎。** 全程以「现有 593 单测保持全绿 + 新增引擎单测」为红线。

| 顺序 | 特性 | 依赖 | 破坏性风险控制 |
|------|------|------|--------------|
| K0 | **O1 CI 先行** | 无 | 并行开发护栏 |
| 1 | K3 订单类型体系 | 无 | `Order` 加字段，默认值 = 现有市价行为 |
| 2 | K2 做空与双向持仓 | K3 | `Position.direction` 默认 long，现有测试不变 |
| 3 | K6 成交量约束 + 冲击滑点 | K3 | 新滑点模型可选，默认沿用现有 |
| 4 | K1 组合回测引擎 | K2,K3 | **新文件**，`BacktestEngine` 改为其单标的包装 |
| 5 | K4 策略级止损/ROI/追踪 | K1 | 未配置时行为完全不变 |
| 6 | K5 Insight 三段式框架 | K1,K4 | **新目录**，旧策略经 Adapter 接入 |
| 7 | K7 交易日历 | K1 | 无日历时回退为「按 bar 顺序」 |
| 8 | K8 复权与公司行为 | K7 | 无调整因子时回退为原始价 |

**验收**：同一策略 + 同一数据，新引擎单标的路径结果与旧引擎**逐笔一致**（回归基线测试）；
多标的路径能跑通「Topk 选股 + 月度再平衡」示例并产出组合级 tearsheet。

### 🌊 Wave L+M — 控制、执行、数据、因子（4-5 周）

| 特性 | 依赖 |
|------|------|
| L1 交易控制族 | K3 |
| L2 组合风控模型 | K5 |
| L3 执行模型 | K5 |
| L4/L5 仓位调整与确认钩子 | K4 |
| M1 本地数据归档 | 无 |
| M2 截面算子进公式引擎 | 无（`quant/panel.py` 已有 panel 结构） |
| M3 Alpha101 因子集 | M2 |
| M4 投研产物库 Lab | 无 |
| M5 统一 ML 模型模板 | M4 |
| M7 市值/成分股宇宙 | M1 |

**验收**：`筛选 → 多选 → 因子打分 → 组合构建 → 风控调整 → 执行模型下单` 全链路在**回测**与**模拟盘**跑通同一份代码。

### 🌊 Wave N+O — 分析与工程（3 周）

| 特性 | 依赖 |
|------|------|
| N3 危机时期与恢复期 | 无 |
| N2 容量/换手/杠杆 | K1 |
| N4 进出场归因 + 拒绝信号 | K1 |
| N5 主动式偏差检测 | K1 |
| M6 自适应再训练 | M5 |
| O2 用户策略热加载 | K5 |
| O3 CLI 工具链 | M1 |
| O4 通知事件全覆盖 | 无 |
| O5 多资产扩展预留 | K7,K8 |

---

## 六、多 Agent 并行编排（沿用 HANDOFF §4 已验证模式）

```
契约先行 → N agent 并行 implement→review(pipeline) → 主循环集成共享文件 → tsc/pytest/Docker 冒烟 → 推送
共享文件禁改（agent 返回集成片段）：router.py / App.tsx / Sidebar.tsx / main.py / requirements.txt / types/index.ts
```

**Wave K（8 特性 → 4 agent，强顺序依赖，不宜过度并行）**
- Agent-Ka: K3 + K2（`engine/backtest/broker.py` `position.py` `execution.py`）
- Agent-Kb: K6 + K7 + K8（`engine/backtest/slippage.py` · **新** `engine/calendar/` · **新** `data/adjustments.py`）
- Agent-Kc: K1（**新** `engine/backtest/portfolio_engine.py`，等 Ka 完成后启动）
- Agent-Kd: K4 + K5（`strategy/base.py` 扩展 + **新** `engine/framework/`，等 Kc 完成后启动）
- 主循环: O1 CI 工作流 + 回归基线测试

**Wave L+M（10 特性 → 5 agent，弱耦合可高并行）**
- Agent-La: L1 交易控制族（**新** `engine/controls/`）
- Agent-Lb: L2 + L3（**新** `engine/framework/risk/` `execution/`）
- Agent-Lc: L4 + L5（`strategy/base.py` + `portfolio_engine.py` 钩子接线）
- Agent-Ma: M1 + M7（**新** `data/archive/` + pairlist 扩展）
- Agent-Mb: M2 + M3 + M4 + M5（`quant/` 域内）

**Wave N+O（11 特性 → 5 agent）**
- Agent-Na: N3 + N2（报告类，纯函数移植，最易并行）
- Agent-Nb: N4（进出场归因，依赖组合引擎输出）
- Agent-Nc: N5 主动式偏差检测
- Agent-Oa: O2 + O3（解析器 + CLI）
- Agent-Ob: M6 + O4 + O5

**风险与约束**
- **K1/K5 是最大破坏面**：必须先建「旧引擎逐笔回归基线」测试，任何提交都要通过。
- 坚持零新增重依赖（HANDOFF §3.6）：K/L/N 全部用 numpy/pandas 手写；
  M3 Alpha101 用 pandas 而非 polars（vnpy 用 polars，移植时改写）；
  M5 的 LightGBM 走 **lazy import + 501 降级**（同 B8 torch 模式）。
- zipline 是 **Apache-2.0**、freqtrade 是 **GPL-3.0**、Lean 是 **Apache-2.0**、vnpy 是 **MIT**。
  **⚠️ 从 freqtrade 直接复制代码会引入 GPL 传染**——freqtrade 的部分（K4 止损/ROI 语义、N5 偏差检测、L4/L5 钩子、M1 归档设计、M6 漂移检测、O2 解析器）
  必须**按算法重写而非复制**，并在文件头注明「算法参考自 freqtrade，独立实现」。
  zipline / Lean / vnpy 的代码可在保留版权声明的前提下复制或翻译。

---

## 七、验收标准（v4.0 完成的定义）

```
引擎:  同一策略在新旧引擎单标的路径下逐笔结果一致（回归基线全绿）
       多标的组合回测：50 标的 × 3 年日线 < 30s，产出组合级 tearsheet
       做空策略可回测；限价/止损/追踪止损单在回测中正确触发
       A 股回测按交易日历跳过非交易日；分红除权后价格连续无跳空

框架:  同一个 AlphaModel 既能在回测跑，也能在模拟盘跑（同一份代码路径）
       Topk 选股 → RiskParity 组合 → TrailingStop 风控 → VWAP 执行 全链路一键跑通

数据:  离线归档命中率 > 90%（回测不再依赖在线源）；缺口检测能发现并补齐断档

因子:  截面算子可用于遗传挖掘，挖出的截面 alpha 能直接进组合构建
       Alpha101 + Alpha158 双因子集可切换；dataset/model/signal 可存可复现

分析:  回测报告新增 容量/换手/杠杆/危机分段/回撤恢复期/SQN/expectancy/进出场归因
       主动式 lookahead 检测能识别人为注入的未来函数

工程:  CI 全绿门禁；593+ 单测保持全绿且引擎新增覆盖 ≥ 80%
       用户可上传自定义策略文件并在 UI 中回测
```

---

## 八、精确复用映射表（实现时直查）

| 目标模块 | 直接复用来源（路径 : 行号） | 许可 | 处理方式 |
|---------|--------------------------|------|---------|
| K2 做空持仓 | `refs/zipline-LHJY/zipline/finance/position.py` | Apache-2.0 | 复制改写 |
| K3 订单类型 | `refs/zipline-LHJY/zipline/finance/execution.py` · Lean `Common/Orders/{Limit,StopMarket,StopLimit,TrailingStop,MarketOnOpen,MarketOnClose}Order.cs` | Apache-2.0 | 翻译 |
| K1 组合回测 | `refs/vnpy/vnpy/alpha/strategy/backtesting.py:112-935`（`load_data`/`new_bars`/`cross_order`/`PortfolioDailyResult`） | MIT | 复制改写（polars→pandas） |
| K4 止损/ROI | `refs/freqtrade/freqtrade/strategy/interface.py:1516,1590,1695` | **GPL-3.0** | **仅读算法，独立重写** |
| K5 Insight 框架 | Lean `Algorithm.Framework/{Alphas,Portfolio,Risk,Execution}/*.py` | Apache-2.0 | 翻译（Python 源码） |
| K6 滑点 | `refs/zipline-LHJY/zipline/finance/slippage.py:247,377,524,602` | Apache-2.0 | 复制 |
| K7 日历 | `refs/zipline-LHJY/zipline/utils/calendars.py` · `tradingcalendar.py` | Apache-2.0 | 复制改写（补 HK/A） |
| K8 复权 | `refs/zipline-LHJY/zipline/data/adjustments.py` | Apache-2.0 | 复制改写 |
| L1 交易控制 | `refs/zipline-LHJY/zipline/finance/controls.py:106-436`（8 个类） | Apache-2.0 | **可直接复制** |
| L2 风控模型 | Lean `Algorithm.Framework/Risk/*.py`（5 个） | Apache-2.0 | 翻译 |
| L3 执行模型 | Lean `Algorithm.Framework/Execution/*.py`（3 个） | Apache-2.0 | 翻译 |
| L4/L5 钩子 | `refs/freqtrade/freqtrade/strategy/interface.py:354-767` | **GPL-3.0** | **仅读接口设计，独立实现** |
| M1 数据归档 | `refs/freqtrade/freqtrade/data/history/datahandlers/parquetdatahandler.py` | **GPL-3.0** | **仅读设计，独立实现**（parquet 读写本身无版权问题） |
| M2 截面算子 | `refs/vnpy/vnpy/alpha/dataset/cs_function.py`（64 行） | MIT | **可直接移植**（polars→pandas） |
| M3 Alpha101 | `refs/vnpy/vnpy/alpha/dataset/datasets/alpha_101.py`（330 行） | MIT | **可直接移植** |
| M4 Lab 产物库 | `refs/vnpy/vnpy/alpha/lab.py`（480 行） | MIT | 接口对齐，存储层改 Timescale |
| M5 模型模板 | `refs/vnpy/vnpy/alpha/model/template.py` + `models/{lasso,lgb,mlp}_model.py` | MIT | 复制改写 |
| M6 漂移检测 | `refs/freqtrade/freqtrade/freqai/data_kitchen.py`（DI 指标） | **GPL-3.0** | **仅读算法，独立实现** |
| N2 容量/换手 | Lean `Report/ReportElements/EstimatedCapacityReportElement.cs` · `Common/CapacityEstimate.cs` | Apache-2.0 | 翻译 |
| N3 危机分段 | Lean `Report/Crisis.cs`（危机区间定义表） | Apache-2.0 | 复制区间表 |
| N5 偏差检测 | `refs/freqtrade/freqtrade/optimize/analysis/{lookahead,recursive}.py` | **GPL-3.0** | **仅读方法，独立实现** |
| O2 策略解析 | `refs/freqtrade/freqtrade/resolvers/iresolver.py` | **GPL-3.0** | **仅读设计，独立实现** |
| Pipeline 参考 | `refs/zipline-LHJY/zipline/pipeline/`（D6，v4.0 暂不实现，留档） | Apache-2.0 | — |

---

## 九、与 V3 蓝图的合并视图

| V3 特性 | V4 中的前置/关联 | 合并说明 |
|---------|----------------|---------|
| G1 公式因子策略适配器 | **K5**（Insight 框架） | G1 直接实现为 `FormulaFactorAlphaModel`，比原设计更通用 |
| G2 组合再平衡执行 | **E1/K5/L3** | 由 `PortfolioTarget + ExecutionModel` 天然提供，G2 降为「前端两步确认 UI」 |
| G3 Screener 多选贯通 | M7 | 不变 |
| G4 基本面进筛选/因子库 | F3 / M7 | 合并 |
| G5 统一事件总线 | **O4** | 合并，O4 覆盖面更大 |
| G6 实盘对账 | 不变 | — |
| H1-H6 信息架构 | 不变 | V4 不动前端，两波可并行 |
| I0-I5 AI 原生 | 不变 | I2 自动因子循环因 **M2 截面算子** 而搜索空间更大 |
| J1 CI | **= O1** | 合并 |
| J2 回测向量化 | **K1 之后再做** | 组合引擎定型后再优化，避免重复优化 |
| J3-J5 | 不变 | — |

---

## 十、执行进度

### ✅ 步骤 1 — 引擎回归基线（Wave K 安全网）— 2026-08-07 完成

`backend/tests/regression/`：**144 个用例**（16 preset × 3 市场 × 3 价格情景），锁住 **1364 笔逐笔成交** + 全部绩效指标 + 净值锚点。

- `fixtures.py` — 固定种子 PCG64 生成确定性 OHLCV，不触网、不读库
- `snapshot.py` — 回测结果 → 稳定 JSON（价格/金额 6 位、比率 8 位取整，抹平 BLAS 末位差异）
- `golden/engine_baseline.json` — 260 KB 基线快照
- `generate_baseline.py` — 有意变更行为时重新生成，须审阅 diff
- `test_engine_baseline.py` — 逐笔比对 + 两个元测试（基线用例齐全 / 基线确实锁住了成交而非一堆空跑）

**踩到的坑**：首版用 `hash(market.value)` 派生随机种子 → CPython 对 str 的哈希**按进程随机化**（PYTHONHASHSEED），
基线永远对不上（144 个用例全红）。改为固定偏移表后，已用 `PYTHONHASHSEED=12345/999` 交叉验证跨进程可复现。

### ✅ 步骤 2 — O1 CI — 2026-08-07 完成

`.github/workflows/ci.yml`（此前仓库**无任何 CI**）：

| Job | 步骤 | 状态 |
|-----|------|------|
| backend | ruff check | **拦截** ✅ 已本地验证（`All checks passed!`） |
| backend | pytest（含覆盖率 ratchet 57） | **拦截** ✅ 已本地验证（739 passed） |
| backend | mypy（strict 在存量代码上告警多） | `continue-on-error`，Epic O 收敛后转门禁 |
| frontend | eslint(`--max-warnings 0`) · tsc · vitest · build | **拦截** ✅ 已本地验证 |

### ruff 全量收敛（2026-08-07）：`691 → 0`

起点：`[tool.ruff] select` 此前错写在顶层键（ruff ≥0.2 起归属 `[tool.ruff.lint]`）被静默忽略 ——
**这份代码从未真正被 ruff 检查过**。

| 阶段 | 手段 | Δ |
|------|------|---|
| 摸底 | `ruff check --statistics` | 691 |
| 安全自动修 | `ruff check --fix`（仅 safe fixes） | **−565**（`Optional[X]`→`X \| None`、import 排序、`datetime.UTC`、删未用导入…），148 文件改动 |
| 误报消除 | 改配置而非改代码 | **−65** |
| B904 异常链 | AST 脚本批量补 `from e` / `from None` | **−65** |
| B905 `zip` | 全部改 `strict=True` | **−20** |
| PT011 `raises` | 补 `match=`，用哨兵抓真实消息回填 | **−27** |
| F841 / 其余 | 逐处判断后修复 | **−49** |
| **结果** | | **0，CI 转为拦截** |

**四类改配置而非改代码的（都是框架/领域惯用法被误判）**：

1. `B008` — FastAPI 的 `Depends()` 就是「在参数默认值里调用」，另两处是 `@dataclass(frozen=True)`，
   不存在跨调用共享变更 → `flake8-bugbear.extend-immutable-calls`
2. `N812` — 项目刻意把 `from datetime import date as Date`，避开局部变量 `date` 遮蔽 + 对齐类型 PascalCase
3. `N806` — 量化数学记号（`S`/`K`/`T`/`X`/`W`，与 BSM 及因子模型论文一致）+ 函数内 UPPER_SNAKE 常量
4. `PT` 规则整体不作用于 `app/**` — 它是 pytest 风格规则，否则会把 FastAPI 靠依赖注入鉴权的
   `_user: Annotated[UserInfo, Depends(require_role(...))]` 误判成 pytest fixture；
   `strategy/base.py` 的 `on_start`/`on_stop` 是**可选**钩子，空实现即正确语义，加 `@abstractmethod` 与设计相悖

**过程中发现的三个真问题**（不是格式问题）：

- `zip(strict=True)` 立刻抓到一处**真实长度不匹配**。查证后是 `zip(xs, xs[1:])` 的 pairwise 滑窗惯用法，
  长度本就差 1 —— 这一处（且仅这一处）改回 `strict=False` 并注明理由。
- `tests/test_expected_returns.py` 有个**空心测试**：调用 `capm_return()` 却完全不用返回值，
  自己重算 β 再断言，测的是测试夹具的性质而非被测函数。已补一条针对 `capm_return` 输出的断言
  （CAPM 下 μ 是 β 的线性函数 → 相关系数应为 ±1，不写死年化口径）。
- 代码库里有**两个同名 `RiskViolation`**：`app/oms/manager.py` 是异常类、`app/risk/models.py` 是记录
  dataclass。按 N818 把异常改名 `RiskViolationError`，顺带消除这个冲突。

> **为何不用 `--unsafe-fixes` 一把梭**：它对 B905 的修法是给所有 `zip()` 加 `strict=False` ——
> 只消掉告警、把长度不匹配的隐患原样留着；F841 的自动删除可能连带删掉有副作用的赋值
> （`_override_service(...)` 就是一例）。这类站点必须逐处判断。

顺带修掉的既有问题（都是「配了但从未生效」的配置）：

1. **`[tool.ruff] select` 写在顶层** → ruff ≥0.2 起该键归属 `[tool.ruff.lint]`，写在顶层被静默忽略 = **规则集从未启用**。已迁移。
2. **前端有 `lint` 脚本但无 ESLint 配置文件** → `npm run lint` 一直直接报错。已补 ESLint 9 flat config，且只用 devDependencies 里**已有**的包（不为 lint 扩依赖）。
3. **覆盖率门禁 `--cov-fail-under=80` 长期不可达**（实际 55.06%）→ 所有人用 `--no-cov` 绕过，等于没有门禁。
   改为 **ratchet=57**（略低于当前 57.40%），让它真正拦住倒退；**80% 仍是目标**，每次提升后同步上调。
4. `backend/requirements.txt` 是运行时专用（Docker 镜像无 pytest）→ 新增 `requirements-dev.txt` 供 CI 使用。

ESLint 首次运行找出 8 个问题，全部**修复而非豁免**：
- 6 处静默吞异常（localStorage 不可用 / URL 参数畸形）→ 补明意图注释，把「静默吞」变成「有据的降级」
- `Market.tsx` 组件名与 `import type { Market }` 同名遮蔽 → 重命名为 `MarketPage`（对齐既有 `AlertsPage` 惯例）
- `StockPanel.tsx` 的 `?? []` 每次渲染新建数组导致 useMemo 失效 → 包进 useMemo

**验证**：后端 `pytest` 739 passed / 覆盖率门禁通过；前端 lint 0 error · tsc 0 error · vitest 24 passed · build 成功。

### ✅ 步骤 3 — Wave K 接口契约 — 2026-08-07 完成

按 agent 分工组织（而非按数据结构切分），4 份契约 + 索引，见 [docs/contracts/waveK-README.md](docs/contracts/waveK-README.md)：

| 契约 | 特性 | 依赖 |
|------|------|------|
| [waveKa](docs/contracts/waveKa-order-position.md) | K3 订单类型体系（7 种 + TimeInForce）· K2 做空与双向持仓 | 无 |
| [waveKb](docs/contracts/waveKb-microstructure-calendar.md) | K6 成交量约束滑点（含部分成交）· K7 三市场交易日历 · K8 后复权与分红现金流 | 无 |
| [waveKc](docs/contracts/waveKc-portfolio-engine.md) | K1 多标的组合引擎（时间轴并集对齐 · 停牌估值 · 日度盈亏拆解 · `target_weight()`） | K-a |
| [waveKd](docs/contracts/waveKd-strategy-hooks-insight.md) | K4 止损/ROI/追踪止损（含 `Trade` 概念）· K5 Insight 三段式（含 `LegacyStrategyAlphaAdapter` 零重写接入 16 个 preset） | K-c |

契约中固化的 4 条硬约束：**回归基线是红线**（新能力一律默认关闭）· 零新增重依赖 ·
许可证红线（freqtrade GPL 部分只可读算法独立重写）· 共享文件由主循环 wire。

**实现顺序**（强依赖，不宜过度并行）：`K-a → K-c → K-d`，`K-b` 独立可与 K-a 并行。

### ✅ 步骤 4 — Wave K 四项全部实现并合入 — 2026-08-07

按 `K-a ∥ K-b → K-c → K-d` 的依赖顺序，4 个 agent 在独立 worktree 中实现，主循环负责合并与验收。

| | 特性 | 结果 |
|---|---|---|
| K-a | K3 订单类型（7 种 + TimeInForce）· K2 做空与双向持仓 | 合入 |
| K-b | K6 成交量约束滑点与部分成交 · K7 三市场日历 · K8 复权与分红现金流 | 合入 |
| K-c | K1 组合引擎（时间轴并集 · 停牌估值 · 日度盈亏拆解 · `target_weight()`） | 合入，**单标的引擎已迁移为其特例，旧实现删除** |
| K-d | K4 止损/ROI/追踪止损（含 `Trade`）· K5 Insight 三段式框架 | 合入 |

**最终门禁**：

```
回归基线   146 passed   ← 逐笔 1364 笔成交，四轮合并零漂移
全量测试  1033 passed   ← 739 → 1033
ruff       All checks passed!
覆盖率     55.06% → 61.88%（ratchet 上调至 61）
前端       lint / tsc / vitest / build 全过
性能       50 标的 × 750 时点 = 1.09s（预算 30s）
```

#### 合并中发现并修复的缺陷

| 来源 | 问题 | 严重度 |
|---|---|---|
| 独立审查 | 成交量上限与现金约束同时生效时，被现金砍掉的股数**既不成交、也不挂单、也不拒单**，凭空消失（1000 股的单只留下 97+500） | HIGH |
| 独立审查 | 已拒单仍被挂回队列，下一根 bar 可能真的成交 —— 对外报「拒绝」，账上却成交了 | MEDIUM |
| 独立审查 | 分钟级数据开复权必崩（同日多 bar → 索引重复 → `float(Series)` 抛 TypeError） | MEDIUM |
| **主循环合并失误** | **K-a/K-b 三方合并时 `apply_qty(...)` 被紧随的 `apply(...)` 覆盖，按量市场冲击从未生效**。`apply()` 只能按 `volume_limit` 上限估冲击，占比是平方项 → 1% 的小单按 2.5% 上限算要**高估 6.25 倍**。既有测试的委托量总大于上限、成交量恰等于上限，两条路径结果相同，因此漏网 | HIGH |
| K-c 自查 | `max_open_positions` 与 `target_weight` 冲突：轮动时被清仓的老标的仍占名额，新标的被拒 → 策略安静地少持一只票 | — |
| K-c 自查 | 分红/融券费不经过 Fill，日度盈亏恒等式被打破（N2/N4 建在这份数据上） | — |
| K-d 自查 | `Insight.is_expired` 用墙上时钟，回测中每条观点一生成就过期 | — |

#### 两处对交付质量的额外校正

1. **K-c 的 `test_matches_legacy_engine_fill_by_fill` 是在与自己比对** —— `BacktestEngine.run`
   如今就是委托给 `run_single`，而 legacy 实现已删除。真正守住迁移的是回归基线
   （golden 快照在旧实现还在时生成）。已改名 `test_both_entry_points_agree_fill_by_fill`
   并写明它实际保障的是 `BacktestConfig → PortfolioBacktestConfig` 的字段搬运。
2. **移除 `Insight.is_expired`**，只保留 `is_expired_at(now)`。契约初稿写的是无参属性，
   但本项目以回测为主场景，一个恒返回 True 的 `is_expired` 属性是纯陷阱；
   实盘显式传 `datetime.now(tz=...)` 即可，也更好测。

#### 遗留（不阻塞，需在对应 Wave 处理）

- **做空没有保证金约束**：`allow_short=True` 时开空只贷记现金、不校验权益倍数，理论上无限杠杆。
  排在 A7（Lean `BuyingPowerModel`）。**在那之前不要对用户开放 `allow_short`**（目前默认关闭）。
- `ContractDailyResult.slippage` 恒为 0：滑点已折进成交价，Fill 上没有无滑点基准价可反解。
- `LimitIfTouched` 订单类型（契约疏漏，见零章）→ Wave L。
- ~~组合回测尚未接 API~~ → **已完成**（2026-08-11）：`POST /api/v1/backtests/portfolio`。
  最终没有按初稿设想接受 `bars_by_symbol` —— 50 标的 × 3 年日线是几十万个数字，走 HTTP 既慢又没必要，
  服务端本来就能取数。改为沿用 `/backtests/run` 的 `symbols + 区间` 形态。
  preset 经 `LegacyStrategyAlphaAdapter` 包成组合策略（传**类**而非实例，避免多标的状态互污），
  返回体独有 `per_symbol_metrics`（逐标的归因）与 `daily_results`（日度盈亏拆解）—— 正是 K-c 为 N2/N4 建的两块。

### 遗留待办

- `ruff` / `mypy` 未能本地安装验证（本机到 PyPI 限速）→ CI 中两者均为 `continue-on-error`
- `freqtrade-LHJY` / `vnpy-LHJY` / `Lean-LHJY` 仓库下载未完成（会话重启清空 `/tmp`，现改下至 `refs/.dl/`）；
  蓝图结论不依赖它们（freqtrade/vnpy 用 `refs/` 本地同源完整副本，Lean 用 GitHub API 全目录枚举 + 框架源码精读），
  下载完成后做一次交叉核对
