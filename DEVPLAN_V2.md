# QuantBot v2.0 — 升级蓝图与开发计划

> 版本: v2.0 | 制定: 2026-07-11 | 基线: v1.x（16策略 + 回测 + Alpaca实盘 + 12量化算法 + 公式因子）
> 定位: 从「能用的多市场量化平台」→「专业级 AI 量化研究 + 稳健实盘工作站」

---

## 一、现状盘点（v1.x 已交付）

### 已建成能力矩阵

| 领域 | 已有 | 成熟度 |
|------|------|--------|
| **数据** | US(Alpaca→yfinance→demo)、HK(Futu→yfinance→demo)、A股(AkShare→demo)；bars/spot/indicators/搜索/概览 | 🟢 仅价量数据 |
| **策略** | 16 预设策略（趋势/均值回归/突破/网格/统计套利/多因子） | 🟢 |
| **回测** | 主循环 + 佣金/滑点/持仓 + Sharpe/Sortino/Calmar + 月度热力图 + 蒙特卡洛 + 参数优化 + walk-forward | 🟡 指标分散 |
| **实盘/OMS** | PaperGateway(US/HK/A) + AlpacaGateway(US, paper/live)；手动下单 + 策略自动交易 | 🟡 仅US可实盘 |
| **风控** | 最大仓位/日亏损/单笔上限规则 + VaR/CVaR + 前置检查 | 🟡 静态规则 |
| **量化算法** | GBM/BSM/GARCH/Kelly/协整/Copula/HMM/PCA + 因子IC分析 + 公式因子引擎(RPN) + ML(逻辑/RF/GBM) | 🟢 |
| **组合优化** | max_sharpe/min_vol/risk_parity + 有效前沿 | 🟡 |
| **前端** | 14 页面 + 智能交易引导工作流 + 跨模块流程贯通 | 🟢 |
| **基础设施** | Docker(TimescaleDB/Redis/Celery/Prometheus/Grafana) | 🟢 |

### 关键短板（v2.0 要补齐）
1. **只有价量数据** — 无基本面/新闻/日历/筛选器，用户无法「发现」标的
2. **因子体系偏浅** — 固定因子 + 基础RPN，缺横截面处理、因子库、回归算子、成本感知评价
3. **回测结果单薄** — 缺统一 tearsheet、逐笔分析、稳健性检验（前视偏差/显著性）
4. **风控静态** — 无熔断/冷却/回撤保护等动态防护
5. **无通知体系** — 只有站内价格预警，无 Telegram/Webhook
6. **组合优化基础** — 缺 Black-Litterman/HRP/CVaR/收缩协方差/离散分配
7. **实盘覆盖窄** — HK(Futu)/A股网关未接入 OMS

---

## 二、v2.0 六大 Epic（升级方向）

综合 qlib / vnpy / freqtrade / jesse / OpenBB / PyPortfolioOpt / backtrader 七大参考仓库，
及 AlphaGPT 神经因子挖掘思想，划分为六个相互解耦的工作流。

### Epic A — 数据广度（Data Breadth）
> 目标：从「只有K线」→「选股+研究+事件驱动」的完整数据底座

| # | 特性 | 来源 | 复杂度 | 价值 |
|---|------|------|--------|------|
| A1 | 标准化数据提供者接口（Fetcher: transform→extract→transform + Pydantic标准模型） | OpenBB `provider/abstract/fetcher.py` | M | 底座，让新数据源接入变便宜 |
| A2 | 基本面数据（利润表/资产负债/现金流/财务比率/EPS/股息/市值） | OpenBB `standard_models/` + yfinance/AkShare | L | ⭐⭐⭐ 选股刚需 |
| A3 | 股票筛选器（按市值/PE/行业/涨跌幅/股息筛选 + 涨跌榜 + 同业对比） | OpenBB `equity_screener.py`/finviz | M | ⭐⭐⭐ 发现工作流 |
| A4 | 新闻 + 经济/财报/分红日历 | OpenBB `company_news`/`economic_calendar`/`calendar_earnings` | M | ⭐⭐⭐ 事件择时 |
| A5 | 期权链 + Greeks + IV（含异动） | OpenBB `options_chains.py`/cboe/yfinance | M/L | ⭐⭐ 期权用户 |

### Epic B — 因子与 AI Alpha（Factor & AI）
> 目标：从「固定因子」→「因子库 + AI 自动挖掘 + 无泄漏研究」

| # | 特性 | 来源 | 复杂度 | 价值 |
|---|------|------|--------|------|
| B1 | 横截面处理管道（CSRankNorm/CSZScoreNorm/RobustZScore + infer/learn 分离防泄漏） | qlib `dataset/processor.py` | S | ⭐⭐⭐ 基础，解锁下游 |
| B2 | 声明式因子库（配置生成 Alpha158/360 式数百因子：字段×算子×窗口） | qlib `contrib/data/loader.py` | M | ⭐⭐⭐ 因子动物园 |
| B3 | 表达式引擎扩展（Slope/Rsquare/Resi/Corr/Cov/WMA/EMA/Quantile/Rank + 自定义算子注册） | qlib `data/ops.py` | M | ⭐⭐⭐ 回归残差因子 |
| B4 | 成本感知因子适应度（净PnL-费用-滑点-回撤惩罚 + 活跃度门控 + 全体中位数） | AlphaGPT `backtest.py` | S | ⭐⭐⭐ 从IC→能否赚钱 |
| B5 | 神经/遗传因子挖掘（StackVM 批量执行 RPN + Transformer 采样 + REINFORCE） | AlphaGPT `vm.py`/`engine.py` | L | ⭐⭐ AI发现alpha（差异化） |
| B6 | DoubleEnsemble（样本重加权 + 特征筛选，套在现有GBM上） | qlib `double_ensemble.py` | M | ⭐⭐ 高性价比精度提升 |
| B7 | 实验记录器 + 因子排行榜（SignalRecord/SigAnaRecord: IC/RankIC/ICIR 时序） | qlib `workflow/record_temp.py` | M | ⭐⭐ 可复现研究 |
| B8 | 序列模型库（ALSTM/GRU/GATs/TRA，共享训练接口） | qlib `contrib/model/pytorch_*.py` | L | ⭐ 进阶（需GPU） |

### Epic C — 策略验证与稳健性（Validation & Robustness）
> 目标：从「一个回测数字」→「可信、抗过拟合的策略验证套件」

| # | 特性 | 来源 | 复杂度 | 价值 |
|---|------|------|--------|------|
| C1 | 参数优化 Hyperopt（Optuna 贝叶斯 + 13 种损失函数：Sharpe/Sortino/Calmar/MaxDD…） | freqtrade `optimize/hyperopt/` | L | ⭐⭐⭐ 引导调参防过拟合 |
| C2 | Walk-Forward 分析（滚动训练/测试窗口 + 样本内外分离） | freqtrade `freqai_interface.py` | M | ⭐⭐⭐ 抗曲线拟合 |
| C3 | 前视偏差/递归偏差检测（策略偷看未来数据的 linter） | freqtrade `optimize/analysis/lookahead.py` | M | ⭐⭐⭐ 独特信任特性 |
| C4 | 蒙特卡洛稳健性（逐笔重采样 + 候选路径合成 → 置信区间） | jesse `research/monte_carlo/` | M | ⭐⭐ 「是不是运气」 |
| C5 | 统计显著性检验（bootstrap 假设检验 + 规则贡献度） | jesse `research/rule_significance_testing/` | M | ⭐⭐ 教育向差异化 |
| C6 | 丰富回测报告（按入场标签/出场原因分解 + 日/周/月 + 60+指标 + 连胜连败） | freqtrade `optimize_reports.py` + jesse `metrics.py` | M | ⭐⭐⭐ 从数字到诊断 |
| C7 | Pyfolio 式 tearsheet + 逐笔分析（滚动Sharpe/回撤区间表 + 胜率/盈亏比/持仓时长） | backtrader `analyzers/pyfolio.py`/`tradeanalyzer.py` | M | ⭐⭐⭐ 标准结果页 |

### Epic D — 组合优化升级（Portfolio Optimization）
> 目标：从「均值方差」→「专业级稳健组合构建」

| # | 特性 | 来源 | 复杂度 | 价值 |
|---|------|------|--------|------|
| D1 | 收缩协方差风险模型（Ledoit-Wolf + 指数加权 + 半协方差 + PSD修复） | PyPortfolioOpt `risk_models.py` | S | ⭐⭐⭐ 快赢，最大稳健性提升 |
| D2 | 离散分配（连续权重→整数股数，给定现金预算） | PyPortfolioOpt `discrete_allocation.py` | S | ⭐⭐⭐ 快赢，可直接下单 |
| D3 | Black-Litterman（市场均衡 + 投资者观点，Idzorek 置信度加权） | PyPortfolioOpt `black_litterman.py` | M | ⭐⭐⭐ 注入观点，标杆差异化 |
| D4 | HRP 层次风险平价（相关性聚类 + 递归二分，无需矩阵求逆） | PyPortfolioOpt `hierarchical_portfolio.py` | M | ⭐⭐ 小样本更稳 |
| D5 | CVaR/CDaR/半方差优化（尾部风险/回撤风险目标） | PyPortfolioOpt `efficient_frontier/` | M | ⭐⭐ 匹配损失厌恶 |
| D6 | Topk-Dropout 组合构建（持topK + 每期dropN控换手 + 最短持仓 + 资金度） | qlib `signal_strategy.py` | S/M | ⭐⭐ 因子→可交易组合 |

### Epic E — 执行与实盘（Execution & Live）
> 目标：从「能下单」→「稳健、有防护、可远程控制的实盘」

| # | 特性 | 来源 | 复杂度 | 价值 |
|---|------|------|--------|------|
| E1 | 动态防护/熔断（止损守卫: N次止损后停 + 冷却期 + 回撤保护 + 低盈利锁定） | freqtrade `plugins/protections/` | M | ⭐⭐⭐ 防连续亏损失控 |
| E2 | Telegram/Webhook/多渠道通知（成交/盈亏/持仓/日报 + 远程 start/stop/force-exit） | freqtrade `rpc/telegram.py` + jesse `notifier.py` | M | ⭐⭐⭐ 零门槛高价值 |
| E3 | 高级订单算法（TWAP/VWAP/冰山，大单拆分降冲击） | vnpy OMS/algo 架构 | L | ⭐⭐ 大账户/低流动性 |
| E4 | Dry-Run/Live 一致性（模拟盘走完全相同实盘执行路径，同订单生命周期） | freqtrade `freqtradebot.py` + jesse `modes/` | M | ⭐⭐⭐ 闭合「测过」到「实盘」鸿沟 |
| E5 | 动态标的池筛选（按成交量/波动/价格/价差/市值/近期表现链式过滤） | freqtrade `plugins/pairlist/` | M | ⭐⭐ 固定自选→规则筛选 |
| E6 | 富途 HK 网关接入 OMS（现有 FutuGateway 类接线） | 现有代码 + py-futu-api | M | ⭐⭐ HK 实盘 |

### Epic F — 平台化（Platformization）
> 目标：多用户、可审计、生产就绪

| # | 特性 | 复杂度 | 价值 |
|---|------|--------|------|
| F1 | 多用户 + RBAC（Admin/Trader/Viewer） | M | ⭐⭐ |
| F2 | 审计日志（所有下单/配置变更留痕） | S | ⭐⭐ |
| F3 | 生产加固（Nginx+SSL / 备份 / 负载测试 / 安全扫描） | M | ⭐⭐ |
| F4 | 实验记录器 UI（因子/策略排行榜，与 B7 共享后端） | M | ⭐⭐ |

---

## 三、三波交付节奏

> 按「性价比 + 依赖关系」排序，每一波内部可多 agent 并行。

### 🌊 Wave 1 — 快赢 + 基础（2-3 周）
**主题：立即可感知的价值 + 为后续打地基**

| Epic | 特性 | 依赖 |
|------|------|------|
| B1 | 横截面处理管道（防泄漏） | 无 → 解锁 B2/B3/B7 |
| B4 | 成本感知因子适应度 | 无 |
| D1 | Ledoit-Wolf 收缩协方差 | 无 |
| D2 | 离散分配 | 无 |
| C6 | 丰富回测报告（tag/exit/周期/60+指标） | 现有回测 |
| C7 | Pyfolio tearsheet + 逐笔分析 | C6 |
| E1 | 动态防护/熔断 | 现有风控 |
| E2 | Telegram/Webhook 通知 | 现有 OMS/事件 |

### 🌊 Wave 2 — 核心差异化（3-4 周）
**主题：专业级研究与验证能力**

| Epic | 特性 | 依赖 |
|------|------|------|
| A1 | 标准化数据提供者接口 | 无 → 解锁 A2/A3/A4 |
| A2 | 基本面数据 | A1 |
| A3 | 股票筛选器 | A1 |
| B2 | 声明式因子库 | B1 |
| B3 | 表达式引擎扩展算子 | 现有公式引擎 |
| C1 | Hyperopt 参数优化 | 现有回测 |
| C2 | Walk-Forward 分析 | C1 |
| C3 | 前视/递归偏差检测 | 现有回测 |
| D3 | Black-Litterman | D1 |
| D4 | HRP | D1 |
| D5 | CVaR/CDaR 优化 | D1 |

### 🌊 Wave 3 — 进阶 / premium（4-6 周）
**主题：AI 挖掘、序列模型、高级执行**

| Epic | 特性 | 依赖 |
|------|------|------|
| B5 | 神经/遗传因子挖掘（StackVM） | B3,B4 |
| B6 | DoubleEnsemble | 现有 ML |
| B7 | 实验记录器 + 因子排行榜 | B1,B2 |
| B8 | 序列模型库（ALSTM/GATs/TRA） | B6 |
| C4 | 蒙特卡洛稳健性 | C6 |
| C5 | 统计显著性检验 | C6 |
| D6 | Topk-Dropout 组合 | B2,D1 |
| E3 | TWAP/VWAP 订单算法 | 现有 OMS |
| E4 | Dry-Run/Live 一致性 | 现有 OMS |
| E5 | 动态标的池 | A3 |
| E6 | 富途 HK 网关接入 | 现有网关 |
| A4 | 新闻 + 日历 | A1 |
| A5 | 期权链 + Greeks | A1 |
| F* | 平台化（多用户/审计/生产加固） | — |

---

## 四、多 Agent 并行开发编排

### 工作流边界（避免文件冲突）

每个 Epic 对应独立的后端模块 + 前端页面，文件路径基本不重叠：

```
Epic A 数据    → backend/app/data/providers/*  + frontend/src/pages/Screener.tsx, Fundamentals.tsx
Epic B 因子AI  → backend/app/quant/factor_lib/* + formula_factor.py 扩展 + frontend/src/pages/factor/*
Epic C 验证    → backend/app/engine/backtest/{analysis,reports,tearsheet}.py + frontend Backtest 结果Tab
Epic D 组合    → backend/app/portfolio/optimizers/* + frontend/src/pages/PortfolioOptimizer.tsx 扩展
Epic E 执行    → backend/app/oms/{protections,algos}.py + notify/* + frontend Orders/Risk 扩展
Epic F 平台    → backend/app/core/{auth,audit}.py + 全局
```

### 每波并行 agent 分配

**Wave 1（8 特性 → 4 并行 agent）**
- Agent-W1a: B1 横截面处理 + B4 因子适应度（`quant/`）
- Agent-W1b: D1 收缩协方差 + D2 离散分配（`portfolio/`）
- Agent-W1c: C6 回测报告 + C7 tearsheet（`engine/backtest/` + 前端）
- Agent-W1d: E1 防护熔断 + E2 通知（`oms/` + `notify/`）

**Wave 2（11 特性 → 5 并行 agent）**
- Agent-W2a: A1 提供者接口 + A2 基本面（`data/providers/`）
- Agent-W2b: A3 筛选器（`data/` + 前端 Screener 页）
- Agent-W2c: B2 因子库 + B3 算子扩展（`quant/factor_lib/`）
- Agent-W2d: C1 Hyperopt + C2 Walk-Forward + C3 偏差检测（`engine/backtest/`）
- Agent-W2e: D3 BL + D4 HRP + D5 CVaR（`portfolio/optimizers/`）

**Wave 3（12+ 特性 → 6 并行 agent）** — 详见执行时细化

### 编排模式（每波）
```
1. 规划 agent 产出各特性接口契约（API schema + 前端类型）
2. N 个实现 agent 并行开发（worktree 隔离，避免冲突）
3. code-review agent 逐特性对抗式审查
4. 集成 agent 合并 + TypeScript/pytest 校验 + Docker 冒烟
5. 主循环汇总 + 用户验收 + 推送
```

---

## 五、验收标准（v2.0 完成）

```
数据: 输入行业+市值区间 → 筛选器返回候选股 → 查看基本面 P/E/营收增长
因子: 一键生成 158 因子 → 横截面标准化 → IC排行榜 → AI挖掘出新公式因子
验证: 策略回测 → Hyperopt优化参数 → Walk-Forward确认稳健 → 前视偏差检测通过 → tearsheet报告
组合: 选5标的 → Black-Litterman注入观点 → HRP优化 → 离散分配到整数股 → 一键下单
执行: 策略实盘 → 触发止损守卫自动熔断 → Telegram推送告警 → 远程 force-exit
```

---

## 六、参考仓库映射（实现时直查）

| Epic | 主要参考路径 |
|------|-------------|
| A | `refs/OpenBB/openbb_platform/core/.../provider/`, `.../standard_models/`, `providers/{fmp,yfinance,finviz}/` |
| B | `refs/qlib/qlib/{data/ops.py,data/dataset/processor.py,contrib/data/loader.py,contrib/model/,workflow/record_temp.py}`, `refs/AlphaGPT/model_core/{vm.py,engine.py,backtest.py}` |
| C | `refs/freqtrade/freqtrade/optimize/{hyperopt/,analysis/,optimize_reports/}`, `refs/jesse/jesse/{services/metrics.py,research/}`, `refs/backtrader/backtrader/analyzers/` |
| D | `refs/PyPortfolioOpt/pypfopt/{risk_models.py,black_litterman.py,hierarchical_portfolio.py,efficient_frontier/,discrete_allocation.py}`, `refs/qlib/qlib/contrib/strategy/` |
| E | `refs/freqtrade/freqtrade/{plugins/protections/,plugins/pairlist/,rpc/}`, `refs/vnpy/vnpy/trader/`, `refs/py-futu-api/` |
