# QuantBot v3.0 — 串联 · 简化 · 智能化 升级蓝图

> 版本: v3.0 | 制定: 2026-07-13 | 基线: v2.0（118 端点 · 16 页面 · 593 单测 · 全流程功能已建成）
> 定位: 从「功能齐全的专业工作站」→「全链路闭环、一键可达、AI 原生的量化平台」
> 配套: [DEVPLAN_V2.md](DEVPLAN_V2.md)（上一版蓝图）· [HANDOFF.md](HANDOFF.md)（代码地图/开发模式）

---

## 一、现状全貌审计（2026-07-13 实测代码）

### 1.1 能力矩阵（v2.0 已建成，不再赘述）

数据(6源+基本面+筛选器) · 因子(库/RPN/遗传挖掘/实验记录) · 验证(回测/Hyperopt/WalkForward/偏差/蒙特卡洛/显著性) · 组合(BL/HRP/CVaR/Topk/离散分配) · 执行(OMS/熔断/TWAP/VWAP/Alpaca+富途) · 平台(RBAC/审计/多源切换/通知)。**功能广度已达标，v3.0 不再铺新功能面，转向串联与体验。**

### 1.2 模块串联矩阵（核查结论，含代码证据）

| 流程链 | 状态 | 证据与缺口 |
|--------|------|-----------|
| Screener → 回测/行情 | ✅ 打通 | `Screener.tsx:265-267` navigate 带 symbol/market |
| Strategies → 回测 | ✅ 打通 | `Strategies.tsx:433` 带 strategy+symbol+market |
| Market → 回测/实盘 | ✅ 打通 | `Market.tsx:375-380` |
| 回测结果 → 实盘策略 | ✅ 打通 | `BacktestResultPanel.tsx:94-102` 带 params 跳 LiveStrategy；`Backtest.tsx:31-63` 接收 params JSON |
| 熔断防护 → OMS | ✅ 打通 | `oms/manager.py:152-162` 下单前 check_entry；`:331` 成交后 evaluate |
| **因子挖掘 → 策略/回测** | ❌ 断链 | `factor_mining.py` 挖掘结果只存 experiments（Redis 记录）；`strategy/presets/` 无公式因子策略适配器 → **挖出的 alpha 是死胡同，无法交易** |
| **因子研究页 → 策略/回测页** | ❌ 断链 | FactorAnalysis 无任何 navigate 到 Strategies/Backtest |
| **组合优化 → 下单执行** | ⚠️ 半通 | 离散分配已算出整数股数，但 `PortfolioOptimizer.tsx:799` 只有 `<Link to="/orders">`，用户须手抄逐笔下单；**无批量/调仓 API** |
| **Screener 多选 → 组合优化** | ❌ 断链 | 筛选结果只能单标的跳回测，无多选→送优化器/自选池 |
| **基本面 → 筛选器/因子库** | ⚠️ 半通 | `data/screener.py` 只用自身 panel 快照（不 import providers）；因子库仅价量字段 → 基本面数据只有独立展示端点 |
| **通知覆盖面** | ⚠️ 半通 | dispatch 仅 `oms/manager.py` + `tasks/notify.py`（订单/熔断/风控）；回测完成、挖掘完成、数据源降级均无通知 |
| Alerts 价格预警 ↔ notify 渠道 | ⚠️ 分裂 | 两套并行体系，Alerts 不走 Telegram/Webhook 渠道 |

### 1.3 同类/重复功能（可合并项）

| 重叠项 | 现状 | 合并方向 |
|--------|------|---------|
| 参数优化 ×2 | Backtest 页 `OptimizeTab`（网格）与 `HyperoptTab`（贝叶斯）并列 | 合并为一个「参数优化」Tab，方法作为选项 |
| 蒙特卡洛 ×2 | `MonteCarloTab`（权益曲线MC）与 `RobustnessTab`（逐笔MC+显著性）并列 | 合并为「稳健性」一个入口 |
| 验证表单 ×7 | Backtest 7 个 Tab 各自独立 symbol/strategy/日期表单 | 共享配置上下文 + 「一键全套验证」 |
| Market / MarketEvents | 行情与新闻/日历/期权分两页 | 合并为个股详情页聚合 |
| Portfolio / PortfolioOptimizer | 持仓与优化分两页，优化结果回不到持仓 | 合并为「组合」页（持仓+优化+一键调仓） |
| Alerts / 通知配置 | 价格预警页与 Settings 通知配置分离 | 统一「通知中心」 |
| FactorAnalysis / AlgoLab | ML 策略、序列模型与因子研究分裂在两页 | 统一「研究工作台」 |

### 1.4 易用性与引导现状

- ✅ 已有：Dashboard `TradingWorkflow` 10 步引导（选标的→行情→分析→策略→回测→评估→Kelly→仓位→模拟→实盘），`PAGE_HELP` 每页帮助。
- ❌ 缺口：引导只覆盖「单标的技术策略」一条流，不覆盖 筛选→组合流 和 因子研究流；无全局搜索/命令面板；回测结果为内存态、无历史列表（刷新即失）；4 个页面超 800 行规范（PortfolioOptimizer 1176 / LiveStrategy 1063 / AlgoLab 919 / Market 825）。

### 1.5 优先级最高的 5 个缺口

1. **因子→交易断链**（研究成果无法变现，平台核心叙事断裂）
2. **组合优化→下单半通**（差最后一公里，D2 离散分配的价值未兑现）
3. **验证 7 Tab 重复配置**（最高频操作路径上的摩擦）
4. **Screener 多选→组合断链**（「发现→构建」流不存在）
5. **通知总线覆盖窄 + Alerts 分裂**（长任务无回执，用户须守屏）

---

## 二、v3.0 三大主题

```
主题一 Connect（串联闭环）: 研究成果可交易、优化结果可执行、事件全域可通知
主题二 Simplify（简化引导）: 16页→9页信息架构、共享上下文、多条 Playbook 引导
主题三 AI-Native（智能化）  : LLM Copilot、自动因子研发循环、新闻情绪、AI 研报与诊断
```

### 前沿对标依据（2026-07-13 已联网核实前三项）

| 项目 | 核心机制 | 对 QuantBot 的借鉴 |
|------|---------|-------------------|
| **[microsoft/RD-Agent](https://github.com/microsoft/rd-agent)** (qlib 团队) | RD-Agent(Q)：Research 阶段(域先验→因子假设) + Development 阶段(代码生成→实盘回测)，多臂老虎机调度反馈方向；官方称比经典因子库少用 70% 因子获得至多 2 倍年化 | I2 自动因子研发循环——QuantBot 底座已齐：RPN 公式引擎(生成目标) + 因子适应度(评估) + 实验记录器(反馈存储)，只差 LLM 假设生成器闭环 |
| **[TauricResearch/TradingAgents](https://github.com/tauricresearch/tradingagents)** (~90k stars) | 多角色 LLM agent（基本面/情绪/新闻/技术分析师 + 多空研究员辩论 + 交易员 + 风控）协作产出决策；2026-07 v0.3.1 已修 look-ahead 过滤并支持 Claude Sonnet 5/Fable 5 | I4 个股 AI 研报（多空辩论摘要），挂进个股详情页；其 look-ahead 过滤与本平台偏差检测理念一致 |
| **[virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)** (~61k stars) | 投资大师人格 agent（Graham/Ackman/Damodaran…）各给信号再汇总；正重构为「常驻基金实体」——agent 即可回测/模拟/实盘的可插拔 alpha 模型 | I4 的轻量变体；其「agent 信号 = 可回测 alpha 模型」思路与 G1 因子策略适配器同构，佐证该抽象方向 |
| **freqtrade FreqAI** | 自适应 ML：滚动再训练 + 漂移检测，模型跟随市场更新 | I2 的运行时形态参考（定时再挖掘/再训练 Celery 任务） |
| **OpenBB Workspace/Copilot** | 终端+AI copilot：自然语言查询数据、生成图表、解读 | I1 平台 Copilot——118 个端点天然是 function-calling 工具集 |
| **NautilusTrader** | 事件驱动引擎，回测与实盘同一代码路径 | E4 已做 dry-run 一致性；J2 性能优化参考其数据处理 |
| **vectorbt(.pro)** | 向量化批量回测（数千参数组合秒级） | J2 回测性能：Hyperopt/WalkForward 的内层循环向量化 |
| **FinGPT / FinRL** | 金融 LLM 情绪信号 / RL 交易 | I3 新闻情绪因子（已有 news 端点，差 LLM 打分器） |

---

## 三、Epic 明细

### Epic G — 全链路串联（Connect）
> 主题：让每个模块的输出成为下一个模块的输入，消灭死胡同

| # | 特性 | 内容 | 复杂度 | 价值 |
|---|------|------|--------|------|
| G1 | **公式因子策略适配器** | 新增 `FormulaFactorStrategy`（挂进 strategy registry）：任意 RPN 公式因子 → 信号阈值/分位规则 → 标准策略接口；挖掘结果/因子库条目「一键回测」「注册为策略」；实验记录器加 `promote_to_strategy` | M | ⭐⭐⭐ 打通研究→交易主动脉 |
| G2 | **组合再平衡执行** | 新端点 `POST /portfolio/rebalance/preview` + `/execute`：目标权重(来自任意优化器/Topk) × 现有持仓 → diff 订单清单 → 批量送 OMS（走既有 submit_order，天然享受熔断/审计）；前端优化结果页「预览调仓→确认执行」两步 | M | ⭐⭐⭐ 兑现离散分配价值 |
| G3 | **Screener 多选贯通** | 筛选结果支持多选 → 「送组合优化」「加自选池」「批量回测」；PortfolioOptimizer 接收 symbols 参数 | S | ⭐⭐⭐ 建立「发现→构建」流 |
| G4 | **基本面进筛选器与因子库** | screener 消费 fundamentals providers（PE/PB/ROE/营收增速等条件）；因子库注册基本面字段（价量×基本面复合因子） | M | ⭐⭐ 数据层价值兑现 |
| G5 | **统一事件总线通知** | 回测完成/Hyperopt完成/挖掘完成/数据源降级/对账差异 → 全接 `dispatch_event`；Alerts 价格预警改走 notify 渠道（站内+Telegram+Webhook 统一）；通知中心页（历史+已读） | M | ⭐⭐⭐ 长任务免守屏 |
| G6 | **实盘对账**（承接 backlog） | 定时拉 Alpaca/富途持仓资金 vs 本地 OMS 差异对账，差异告警走 G5 | M | ⭐⭐ 实盘可信度 |

### Epic H — 信息架构与引导（Simplify）
> 主题：16 页 → 9 页，操作步数减半，三条 Playbook 全覆盖

| # | 特性 | 内容 | 复杂度 | 价值 |
|---|------|------|--------|------|
| H1 | **页面合并重组** | 见下方目标信息架构；合并 Market+MarketEvents、Portfolio+PortfolioOptimizer、FactorAnalysis+AlgoLab、Alerts→通知中心 | L | ⭐⭐⭐ |
| H2 | **验证套件一键化** | Backtest 页：共享配置头（symbol/strategy/日期一处填）；合并 Optimize+Hyperopt、MonteCarlo+Robustness → 4 个 Tab；「完整验证」按钮串行跑 回测→优化→WalkForward→偏差→稳健性 出综合评级报告 | M | ⭐⭐⭐ 最高频路径摩擦减半 |
| H3 | **全局 symbol 上下文 + 命令面板** | ⌘K 命令面板（搜标的/跳页面/执行动作）；当前标的上下文随页面切换保持（URL param 统一约定已有，补全局态） | M | ⭐⭐ |
| H4 | **Playbook 引导系统** | 现有 TradingWorkflow 泛化为 3 条：①单标的技术流(已有) ②发现→组合流（筛选→多选→优化→调仓执行）③因子研究流（建因子→适应度→回测→注册策略）；Dashboard 按用户进度推荐下一步 | L | ⭐⭐⭐ 引导覆盖全平台 |
| H5 | **回测/实验结果持久化** | 回测结果落库（复用实验记录器）+ 历史列表 + 对比视图；结果可从历史一键重跑/送实盘 | M | ⭐⭐⭐ 消除「刷新即失」 |
| H6 | **大页面拆分与一致性** | 4 个超标页面拆到 <800 行；统一表单组件/术语/空状态/loading | M | ⭐ 工程健康 |

**目标信息架构（9 页）：**

```
仪表盘   Dashboard（Playbook 入口 + 账户概览 + 通知摘要）
市场     Market（行情 + 个股详情聚合新闻/日历/期权 = 原Market+MarketEvents）
发现     Screener(筛选器+动态标的池+多选动作)
研究     Research 工作台（因子库/公式/挖掘/实验 + ML/序列模型 = 原FactorAnalysis+AlgoLab）
策略     Strategies（16预设 + 因子注册策略）
验证     Backtest（共享配置 + 4 Tab + 一键完整验证）
组合     Portfolio（持仓 + 优化器 + 一键调仓 = 原Portfolio+PortfolioOptimizer）
交易     Trading（实盘策略 + 订单/算法单 = 原LiveStrategy+Orders，可保留二级Tab）
风控     Risk（风控+熔断）
设置     Settings（券商/数据源/通知渠道/角色/审计）+ 通知中心
```

### Epic I — AI 原生（AI-Native）
> 主题：LLM 作为平台的第二操作界面与自动研究员。统一先建 I0 网关，模型可换（Claude API / 本地 Ollama）

| # | 特性 | 内容 | 复杂度 | 价值 |
|---|------|------|--------|------|
| I0 | **LLM 网关** | `core/llm.py`：统一 chat/function-calling 接口，供 I1-I5 复用；key 走环境变量，未配置时相关端点返 501（沿用 lazy torch 模式） | S | 底座 |
| I1 | **平台 Copilot** | 对话侧栏：自然语言 → function-calling 调既有端点（筛选/回测/查持仓/解读结果）→ 结构化卡片回复；只读动作直接执行，交易动作生成「待确认」草稿 | L | ⭐⭐⭐ 操作简便性的终极形态 |
| I2 | **自动因子研发循环**（RD-Agent 式） | LLM 读实验记录器历史 → 提因子假设 → 生成 RPN 表达式 → 适应度评估 → 结果回写实验 → 迭代 N 轮；与既有遗传挖掘互补（LLM 出方向，遗传做局部搜索）；产出自动进因子排行榜，可经 G1 一键变策略 | L | ⭐⭐⭐ 平台差异化护城河 |
| I3 | **新闻情绪因子** | LLM 对 news 端点内容打分（-1..1）→ 情绪时序入因子库 → 可进筛选器/策略 | M | ⭐⭐ |
| I4 | **AI 个股研报** | 多空双 agent 辩论（技术面+基本面+新闻输入）→ 结构化研判卡（多空论点/风险/结论）挂个股详情页 | M | ⭐⭐ 展示性强 |
| I5 | **回测 AI 诊断** | tearsheet + 验证套件结果 → LLM 解读：过拟合迹象、参数敏感性、与基准差异，给下一步建议（接 H2 综合报告） | S | ⭐⭐ 教育向 |

### Epic J — 工程与生产（承接 v2.0 backlog）

| # | 特性 | 复杂度 | 价值 |
|---|------|--------|------|
| J1 | CI/CD（GitHub Actions: ruff+mypy+pytest / tsc+eslint+build） | S | ⭐⭐⭐ 并行开发必备护栏 |
| J2 | 回测性能向量化（Hyperopt/WF 内层批量评估；参考 vectorbt 思想，坚持零新增重依赖用 numpy） | M | ⭐⭐ |
| J3 | 多用户注册/管理 + 审计落库（Redis stream → TimescaleDB） | M | ⭐⭐ |
| J4 | torch 启用评估（激活 B8 序列模型；ARM64 镜像成本测试） | S | ⭐ |
| J5 | 生产加固（Nginx+SSL/备份/负载测试）+ E2E 冒烟（Playwright 覆盖 3 条 Playbook） | M | ⭐⭐ |

---

## 四、三波交付节奏

### 🌊 Wave A — 串联闭环 + 合并快赢（2-3 周）
> 主题：不加新功能，让现有功能连起来。**J1 CI 先行落地作为并行开发护栏。**

| 特性 | 依赖 |
|------|------|
| J1 CI/CD | 无（第一个上） |
| G1 因子策略适配器 | 无 |
| G2 组合再平衡执行 | 无 |
| G3 Screener 多选贯通 | 无 |
| G5 统一事件总线通知 | 无 |
| H2 验证套件一键化 | 无 |
| H5 结果持久化 | 复用实验记录器 |

### 🌊 Wave B — 信息架构 + Copilot（3-4 周）

| 特性 | 依赖 |
|------|------|
| H1 页面合并重组 | Wave A 完成（避免与 G/H2 冲突） |
| H3 命令面板 + 全局上下文 | H1 |
| H4 Playbook×3 | G1/G2/G3（引导的是新链路） |
| I0 LLM 网关 | 无 |
| I1 平台 Copilot | I0 |
| I3 新闻情绪因子 | I0 |
| G4 基本面进筛选/因子库 | 无 |
| H6 大页面拆分 | H1 |

### 🌊 Wave C — 自动研究员 + 生产（4-5 周）

| 特性 | 依赖 |
|------|------|
| I2 自动因子研发循环 | I0, G1, H5 |
| I4 AI 个股研报 | I0, I3 |
| I5 回测 AI 诊断 | I0, H2 |
| G6 实盘对账 | G5 |
| J2 回测向量化 | H2 |
| J3 多用户 + 审计落库 | 无 |
| J4 torch 评估 | 无 |
| J5 生产加固 + E2E | 全部 |

---

## 五、多 Agent 并行开发编排（沿用 HANDOFF §4 已验证模式）

```
契约先行 → N agent 并行 implement→review(pipeline) → 主循环集成共享文件 → tsc/pytest/Docker 冒烟 → 推送
共享文件禁改（agent 返回集成片段）：router.py / App.tsx / Sidebar.tsx / main.py / requirements.txt / types/index.ts
```

**Wave A（6 特性 → 5 并行 agent）**
- Agent-Aa: G1 因子策略适配器（`strategy/` + `quant/experiments/`）
- Agent-Ab: G2 再平衡执行（`engine/portfolio/rebalance.py` + `api/.../portfolio_opt.py` 扩展 + 前端组合结果区）
- Agent-Ac: G3+G5（`data/screener.py` 多选动作 + `notify/` 事件接线；前端 Screener 多选）
- Agent-Ad: H2 验证套件（`components/backtest/*` 重组 + 后端 `/backtests/full-validation` 编排端点）
- Agent-Ae: H5 结果持久化（`quant/experiments/` 扩展 + 回测历史端点 + 前端历史列表）
- 主循环: J1 CI 工作流文件（涉及全仓，不下放 agent）

**Wave B（8 特性 → 5 并行 agent）**
- Agent-Ba: H1 页面合并（前端大改，独占 pages/；分 2 个 agent 按页面组切分亦可）
- Agent-Bb: H3 命令面板 + 全局上下文（`components/ui/CommandPalette.tsx` + context）
- Agent-Bc: H4 Playbook（`components/workflow/` 扩展）
- Agent-Bd: I0+I1 LLM 网关与 Copilot（`core/llm.py` + `api/.../copilot.py` + 前端侧栏）
- Agent-Be: I3+G4（情绪因子 + 基本面字段进筛选/因子库）

**Wave C（8 特性 → 5 并行 agent）**
- Agent-Ca: I2 自动因子循环（`quant/mining/llm_loop.py` + Celery 任务）
- Agent-Cb: I4+I5 研报与诊断（`api/.../ai_reports.py` + 前端卡片）
- Agent-Cc: G6 对账 + J3 多用户/审计落库
- Agent-Cd: J2 回测向量化（`engine/backtest/` 内部，不动接口）
- Agent-Ce: J5 生产加固 + Playwright E2E

**风险与约束**
- H1 信息架构大改必须独波独 agent，避免与功能 agent 抢 pages/ 文件。
- I 系列全部走 I0 网关 + 501 降级，保证无 key 环境（CI/演示）全绿。
- 坚持零新增重依赖原则（HANDOFF §3.6）；LLM 走 httpx 直调 API，不引 langchain 等重框架。
- 每波结束跑 HANDOFF §7 验收基线 + 新增 E2E。

---

## 六、验收标准（v3.0 完成的定义）

```
串联: 挖掘出因子 → 一键注册策略 → 回测 → 送实盘（零复制粘贴）
     优化组合 → 预览调仓订单 → 一键执行 → 持仓更新 + Telegram 回执
     筛选器多选 5 股 → 送优化器 → HRP → 离散分配 → 调仓执行
简化: 16→9 页；验证全套一键出综合评级；任意页 ⌘K 直达；回测历史可查可对比
引导: 3 条 Playbook 覆盖 技术流/组合流/因子流，新用户每条 ≤10 分钟走通
AI  : Copilot 能听懂「筛出低PE高ROE的美股并对前5名跑HRP」并执行；
     自动因子循环夜间跑 20 轮，早晨排行榜出现新因子且可一键回测；
     每份回测报告附 AI 诊断段落
工程: CI 全绿门禁；593+ 单测保持全绿并新增覆盖；E2E 冒烟 3 条 Playbook
```

---

## 七、参考映射（实现时直查）

| Epic | 参考 |
|------|------|
| G1/G2 | 本仓 `strategy/presets/multi_factor.py`（策略接口范式）· `engine/portfolio/discrete_allocation.py` |
| G5 | 本仓 `notify/dispatcher.py` · freqtrade `rpc/` 事件枚举 |
| H2/H4 | freqtrade FreqUI 工作流 · 本仓 `components/workflow/TradingWorkflow.tsx` |
| I1 | OpenBB Copilot 形态 · Claude API function calling（tools 参数） |
| I2 | microsoft/RD-Agent（RD-Agent(Q) 因子研发循环）· 本仓 `quant/mining/genetic.py` + `quant/experiments/` |
| I3/I4 | TauricResearch/TradingAgents（多空辩论）· virattt/ai-hedge-fund（人格化信号）· FinGPT（情绪打分） |
| J2 | vectorbt 向量化思想 · NautilusTrader 数据处理 |

---

## 八、实际交付记录（2026-08-14）

> 本节记录**实际发生了什么**，与 §五的事前编排必然有出入 —— 出入本身是信息，
> 所以保留而不是回头改 §五。分支 `feat/v4-engine-core-and-v3-wave-a`。

### 8.1 与事前编排的偏差

| 事前（§五） | 实际 | 原因 |
|---|---|---|
| Wave B 含 I3 新闻情绪因子 | **未做** | 见下 |
| Agent-Ca = I2 | Agent-Ca = I4+I5 | I2 依赖 H5，排在后面更顺 |
| Agent-Cb = I4+I5 | Agent-Cb = G6+J3 | 同上连锁调整 |
| Wave C 含 J2 回测向量化 | **推迟** | 没有 profiling 证据，先测再优化 |
| J5 由单个 Agent-Ce 承担 | 拆成 Wave D 的三个 agent | 加固/E2E/运维三件事互不依赖，串行没有必要 |

### 8.2 I3 新闻情绪因子：判定为不阻塞 I4

蓝图写 **I4 依赖 I3**。实际摸底后判定**不成立**：`news_provider.py` 已能抓取
公司新闻，缺的只是情绪打分。而研报本就该让 LLM **直接读原始新闻** ——
先压成一个情绪分再喂进去是有损的，还会丢掉「为什么看空」这类可解释性。

所以 I4 直接消费原始新闻条目，I3 若将来落地是**独立的因子路径**，与研报无耦合。

### 8.3 已交付

- **Wave A**：G1 因子策略适配器 · G2 再平衡执行 · G3+G5 多选与事件总线 · H2 验证套件 · H5 结果持久化 · J1 CI
- **Wave B**：H1 页面重组 · H3 命令面板与全局标的上下文 · H4 Playbook · I0 LLM 网关 · I1 Copilot · G4 基本面
- **Wave C**：I4 AI 研报 · I5 回测诊断 · G6 实盘对账 · J3 多用户与审计落库 · I2 自动因子循环
- **Wave D**（进行中）：J5 生产加固 + E2E + Nginx/备份

### 8.4 未做及其理由

| 项 | 状态 | 理由 |
|---|---|---|
| I3 新闻情绪因子 | 未做 | 见 §8.2，非阻塞 |
| J2 回测向量化 | **已交付（重定向为指标预算）** | profiling 做完了，见 [docs/profiling-backtest-j2.md](docs/profiling-backtest-j2.md)。结论：引擎只占 5%，**86% 的时间在策略每 bar 重算指标**；数据量涨 67× 成本只涨 1.56×（64% 是 pandas 固定调用开销）。「向量化引擎」是负收益。该做的是让指标只算一次 —— 实测同一策略预算指标后 9.93× 加速且成交逐笔一致。Wave E-a 已落地：单标的 6.6×、组合 16.4×，回归基线逐字节不变 |
| J4 torch 评估 | 推迟 | 属研究不属实现，产出应是一份评估结论而非代码 |

### 8.5 两个悬置问题的定调

- **`/chat` 的 RBAC**：保持 `Role.VIEWER`，「花钱」这个顾虑改由**按用户日配额**
  承担（`core/llm_quota.py`）。角色是权限的刻度不是花费的刻度 —— admin 一样能把
  账单打爆，而把助手抬到 TRADER 恰好拦住最需要它的只读用户。
- **`/alerts` 页面归属**：与 `/notifications` 合并为一页两 Tab。规则触发与通知送达
  本就是一件事的两半。`/notifications` 保留为重定向，书签与历史深链不会 404。
