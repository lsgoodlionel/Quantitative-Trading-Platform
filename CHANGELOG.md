# 更新日志 / 阶段性总结

QuantBot 多市场量化交易平台的开发行程记录。最新在上。

---

## v2.0 — 专业级 AI 量化工作站（2026-07）

从「能用的多市场量化平台」升级为「专业级 AI 量化研究 + 稳健实盘工作站」。
基于 7 大参考仓库（qlib/vnpy/freqtrade/jesse/OpenBB/PyPortfolioOpt/backtrader）+ AlphaGPT，
归并为 6 大 Epic / 37 特性，以「契约先行 + 多 agent 并行 implement→review」模式分三波交付。

### 多源数据通道（`6cb6747`）
- 每市场多源可配置：US(Alpaca→yfinance→AkShare美股→Stooq)、HK(富途→yfinance→AkShare港股→Stooq)、A(AkShare)，全部演示兜底
- 新增 AkShare 美股/港股日线 + Stooq CSV（**零新增依赖**）
- 动态切换 + 手动强制(pin) + 禁用 + 排序，配置持久化 Redis
- 设置页实时状态点+延迟+当前生效标记，立即检测/自动刷新
- 端点 `/data-sources/{status,config}`

### 平台化补充（`07fb5ef` `d6d1ff9`）
- **B8 序列模型**：LSTM/GRU/ALSTM，lazy torch（未装返 501，不加 requirements 保持镜像轻量）
- **F RBAC**：Viewer<Trader<Admin，写端点鉴权，fail-safe 降级；前端角色徽章+按钮置灰
- **F 审计**：下单/撤单/券商配置/风控变更留痕（Redis stream）+ 审计端点
- 安全修复：cancel_order 补鉴权(原CRITICAL)、fail-safe role、PUT-risk/broker-test 鉴权

### Wave 3 — 因子挖掘/集成学习/稳健性/执行（`fa2e334`）
- 遗传因子挖掘（RPN StackVM 进化搜索，非神经网络）+ 实验记录/排行榜
- DoubleEnsemble（sklearn 样本重加权+特征筛选）+ Topk-Dropout 轮动组合
- 蒙特卡洛稳健性（逐笔 bootstrap）+ 统计显著性检验
- TWAP/VWAP/冰山 订单算法 + Dry-Run/Live 执行一致
- 动态标的池规则链 + 富途 HK 网关接入 OMS
- 新闻+财报/分红日历 + 期权链+Greeks（复用 BSM）
- 修复 7 个 review HIGH（topk off-by-one/空转/回撤基准、显著性方向、冰山上限、成交回读、futu枚举）

### Wave 2 — 数据广度/因子库/策略验证/组合优化（`158ca17`）
- OpenBB 式基本面数据（利润表/资产负债/现金流/比率/指标）+ 股票筛选器
- 声明式因子库（Alpha158 式）+ RPN 算子扩展（回归/截面）
- Hyperopt(11损失) + Walk-Forward + 前视/递归偏差检测
- Black-Litterman + HRP + CVaR/CDaR（scipy 实现，零新增依赖）
- 修复：A股比率按报告期匹配、akshare 废弃接口替换、HK ticker 补零、网格内存守卫

### Wave 1 — 因子处理/组合优化/回测报告/防护通知（`9988da2` `cf776ab`）
- 横截面处理管道（防泄漏 infer/learn 分离）+ 成本感知因子适应度
- Ledoit-Wolf 收缩协方差 + 离散分配（权重→整数股）
- 丰富回测报告（60+指标）+ Pyfolio tearsheet + 逐笔分析
- 动态防护熔断（止损守卫/冷却/回撤保护）+ Telegram/Webhook 通知
- 94 单测 + 拆分 Backtest.tsx(1012→136) + Fill schema 扩展

### 规划（`b369174` `2d82809`）
- v2.0 升级蓝图（6 Epic/37 特性/3 波）+ 可视化路线图
- Wave 1 四份接口契约（契约先行）

---

## v1.x — 多市场量化平台基线（2026-05 ~ 06）

- 公式化因子引擎（`5802639`，借鉴 AlphaGPT RPN）
- 实盘架构透明化：AlpacaGateway 接入 OMS（`788d184`）
- 全平台 UX 系统性优化 + 跨模块流程贯通（`0a5389a` `8fde245`）
- 实盘策略页重建：纸面交易模拟 + 三 Tab 看板（`97a33a1`）
- 16 内置策略 + 智能交易引导工作流（`601e79b` `b078b38`）
- 实时三市行情 + 数据通道状态（`50245b3`）
- Phase 1-8 核心：数据层/回测引擎/实盘执行/风控(VaR)/ML策略/价格预警（`1837a9d` 起）

---

## 里程碑

| 阶段 | 交付 | 状态 |
|------|------|------|
| v1.x 基线 | 16策略·12算法·Alpaca实盘·公式因子·三市行情 | ✅ |
| v2.0 Wave 1 | 因子处理·组合优化·回测报告·防护通知 + 94测试 | ✅ |
| v2.0 Wave 2 | 数据广度·因子库·策略验证·高级组合优化 | ✅ |
| v2.0 Wave 3 | 因子挖掘·集成学习·稳健性·订单算法·标的池·新闻期权 | ✅ |
| v2.0 平台化 | B8序列模型(lazy torch)·RBAC·审计·多源数据通道 | ✅ |
| 后续 | torch启用·多用户·实盘对账·生产加固·CI/CD | 📋 待办 |

见 [HANDOFF.md](HANDOFF.md) 第五节 backlog。
