# QuantBot 移交文档（Handoff）

> 面向后续开发者/运维的阶段性移交。快照时间：**2026-08-17** · 版本：**v4.0**（V3 全部 + V4 全部交付）
> 配套：[README.md](README.md)（使用）· [DEVPLAN_V3.md](DEVPLAN_V3.md) / [DEVPLAN_V4.md](DEVPLAN_V4.md)（路线图）· [docs/contracts/](docs/contracts/)（每一波的接口契约）

---

## 一、当前状态一句话

多市场（美股/港股/A股）AI 量化研究 + 实盘工作站。选股 → 因子研究 → 策略验证 →
组合构建 → 实盘执行 → 对账/审计 全流程闭环，另有 LLM 网关、平台 Copilot、
AI 研报与回测诊断、自动因子研发循环、自适应再训练。

- **182** 个 API 端点 + 4 个 WebSocket · **9 主页 + 4 独立页** ·
  后端 **2861** 测试 / 前端 **285** 测试 / **6** 条 E2E 全绿 · 覆盖率 **75.10%**
- 后端 FastAPI+async · 前端 React18+TS+Vite · TimescaleDB + Redis + Celery · Docker Compose + Nginx

### ⚠️ 最重要的一条：回归基线是红线

`backend/tests/regression/` 的 **146 个用例**钉住了引擎的 **1364 笔逐笔成交**
（golden JSON）。**任何改动都不该让它变色**。变色只有两种可能：
你真的改变了引擎行为（那要想清楚是不是有意的），或者你改错了。
**绝不要改基线去迁就代码。**

验证「引擎输出真的没变」的正确方法不是跑绿，而是**重新生成基线后比 sha256**：

```bash
cd backend && python -m tests.regression.generate_baseline
git diff tests/regression/     # 必须为空
```

跑绿只证明「比对逻辑没报差异」；重新生成才证明「输出本身没变」。
动 `Bar`、`StrategyContext` 这类全局结构时，只有后者靠得住。

---

## 二、代码地图（哪块功能在哪）

### 后端 `backend/app/`

| 模块 | 职责 | 关键文件 |
|------|------|---------|
| `data/` | 多源数据层 | `service.py`(门面+缓存) · `source_registry.py`(多源/动态切换) · `feeds/*`(6源+demo) · `archive/`(列存归档) · `universe.py` · `screener.py` · `pairlist.py` · `continuous_futures.py`(期货连续合约) |
| `data/models/` | 数据结构 | `bar.py`(**被回归基线钉着**) · `asset_class.py` · `contract.py` |
| `engine/backtest/` | 回测+验证 | `portfolio_engine.py`(**主循环**，单标的是它的特例) · `broker.py`(撮合) · `hyperopt.py` · `walkforward.py` · `full_validation.py`(五步串行) · `validation_grade.py`(规则评级) · `bias_detection.py` · `capacity.py` |
| `engine/framework/` | Alpha/组合/风控/执行四段式 | `alpha.py` · `portfolio_construction.py` · `risk/` · `execution/` |
| `engine/portfolio/` | 组合优化 | `optimizer.py` · `black_litterman.py` · `hrp.py` · `cvar_opt.py` · `discrete_allocation.py` |
| `quant/` | 量化算法+因子+ML | `formula_factor.py`(RPN) · `cross_section.py` · `factor_lib/`(含 Alpha101) · `mining/`(遗传+表达式解析) · `lab/`(产物库 + **自动因子循环**) · `models/`(统一模板) · `drift.py`(漂移检测) · `retrain.py`(自适应再训练) |
| `strategy/` | 策略 | `base.py` · `context.py` · **`precompute.py`(指标预算，见 §三.7)** · `presets/`(16 个) · `resolver.py`(用户策略热加载) · `live_runner.py` · `paper_sim.py` |
| `oms/` | 订单管理 | `manager.py`(**进程内单例**，见 §三.3) · `reconcile.py`(实盘对账) · `protections/` · `algos/`(TWAP/VWAP/冰山) |
| `gateway/` | 券商网关 | `alpaca_gateway.py` · `futu_gateway.py` · `paper_gateway.py`(默认) · ib/xtp(桩) |
| `core/llm/` | LLM 网关 | `registry.py`(预设 provider) · `openai_compat.py` / `anthropic.py` · `service.py` · `config_store.py` |
| `copilot/` | 平台 Copilot | `tools.py`(**动作边界，见 §三.5**) · `engine.py` · `execute.py` · `drafts.py` |
| `ai_reports/` | AI 研报/诊断 | `stock_report.py` · `diagnosis.py` · `contradiction.py`(与规则判据的矛盾检测) |
| `core/` | 基础设施 | `rbac.py` · `audit.py`(双写 Redis+PG) · `health.py`(真实依赖探测) · `security_headers.py` · `llm_quota.py`(**AI 日配额，见 §三.6**) · `version.py` |
| `data/storage/` | 仓储 | `users.py` · `audit_log.py` · `backtest_history.py` |

### 前端 `frontend/src/`

> **v3.0 H1 做过一次页面重组**：16 个旧路由收敛成 **9 主页 + 4 独立页**。
> 旧路由（`/market-events` `/algolab` `/portfolio-optimizer` `/factor` `/orders`
> `/live-strategy`）**已经不存在**，各页的子功能改由 `?tab=` 驱动。

| 路由 | 页面 | Tab（`?tab=`） |
|------|------|------|
| `/` | Dashboard | 仪表盘 + Playbook 引导 |
| `/market` | MarketPage | 行情查询 · 自选 · 事件期权 · **AI 研报** |
| `/screener` | Screener | 选股 + 动态标的池（多选可送组合优化） |
| `/research` | ResearchPage | 因子处理 · 公式因子 · 因子库 · 挖掘 · 实验记录 |
| `/strategies` | Strategies | 16 个预设 + 用户策略 |
| `/backtest` | Backtest | 回测 · 完整验证（五步 + 评级 + **AI 诊断**）· 历史 |
| `/portfolio` | PortfolioPage | 持仓 · 优化器（含**预览调仓**）· 组合回测 |
| `/trading` | TradingPage | 实盘策略 · 订单中心 · 算法单 |
| `/settings` | Settings | 券商 · 数据源 · 通知 · 用户 · 审计 |
| `/risk` | Risk | 风控 + 熔断 |
| `/alerts` | AlertsPage | **预警规则 · 通知中心**（两个 Tab，`/notifications` 重定向到此） |
| `/lab` | Lab | 投研产物库 + 自动因子循环 |
| `/settings/models` | ModelSettings | LLM provider 与密钥 |

⌘K 命令面板可直达任意页面的具体 Tab（`components/palette/commandRegistry.ts`）。

---

## 三、关键架构决策（改代码前必读）

1. **数据源多源冗余**：`DataSourceRegistry` 持有每市场有序源链 + Redis 配置。
   全失败降级 `DemoDataFeed` —— **平台永不断供**。
2. **前端全走 Vite proxy**（同源，无 CORS）。**切勿设 `VITE_API_URL`**。
3. **OMS 是进程内单例。** `get_order_manager()` 读模块级全局，只在 FastAPI 启动时
   赋值。**Celery worker 是另一个进程，那份订单簿永远是空的** ——
   任何需要看订单簿的定时任务都拿不到有意义的结果（实盘对账为此改成了
   以进程内端点为主、定时任务加抑制）。
4. **RBAC fail-safe**：缺 role 的 JWT 降级 viewer，**绝不默认提权**。
   写用户时用 `normalize_role_strict()`（非法值抛错而非降级）。
5. **Copilot 动作边界由代码强制，不是提示词。**
   `CopilotTool.requires_confirmation` 默认 `True`（白名单式：忘记标注 = 需要确认）。
   **产生订单或改变实盘配置的动作一律要人工确认。**
   同一立场贯穿全平台：AI 研报不给买卖结论、自动因子循环只入库不注册策略、
   自适应再训练不自动上线、实盘对账只报告不纠正。
6. **AI 花费由配额约束，不由角色约束。** 四个花钱入口都保持 `Role.VIEWER` ——
   角色是权限的刻度不是花费的刻度，admin 一样能把账单打爆，
   而把助手抬到 TRADER 恰好拦住最需要它的只读用户。改用按用户日配额
   （`core/llm_quota.py`，viewer 50 / trader 200 / admin 500）。
   Redis 挂了**放行并记 ERROR**，不能让一个附属计数器成为全站 AI 的单点。
7. **指标预算（性能）**：策略可选实现 `declare_indicators`，框架整轮算一次，
   `on_bar` 通过 `ctx.ind` 按游标取值。**16 个预设已改造 15 个**，
   端到端加速 2.2×–40.9×，成交逐笔一致。
   **`ctx.ind` 绝不暴露完整 Series** —— 一次算完的指标里装着未来，
   `IndicatorView` 在构造时就 `arr[:cursor]` 物理裁剪。旧写法继续可用。
8. **torch 不启用**（结论见 [docs/torch-evaluation-j4.md](docs/torch-evaluation-j4.md)）：
   wheel 527MB 而全部依赖合计才 505MB，收益未经证实。
   现状「装了能用、不装端点 501」已是最优形态。
9. **零新增重依赖原则**：ARM64 构建拉包极慢。优先复用已装的
   numpy/pandas/scipy/sklearn/httpx。
10. **生产配置校验会让启动失败。** `ENVIRONMENT=production` 且 `SECRET_KEY`
    仍是占位值 / `ALLOWED_ORIGINS` 含 `*` / `DEBUG=true` → **拒绝启动**。
    这是刻意的：用公开已知的串签 JWT 等于任何人都能伪造 admin token，
    一条 WARNING 在容器日志里滚过去拦不住任何人。

---

## 四、多 agent 并行开发模式（本项目已大量验证）

```
契约先行 → N agent 并行（git worktree 隔离）→ 主循环集成 → 验证 → 推送
```

**最有价值的一条：要求 agent 质疑契约。** V3+V4 各波的 agent 一共报出
**四十余处契约错误**，包括不可编译的 dataclass 字段顺序、不存在的 API、
基于错误前提的要求、以及两个部署阻断级 bug（两份不兼容的 `users` 表、
归档写入必崩）。这些如果靠实现者「照做」，全都会变成线上问题。

**集成时的硬纪律**（都是踩过坑总结的）：
- **先比对基线再决定合法**：`git merge-file` 三方合并曾把新代码悄悄吃掉且测试测不出。
  现在的做法是逐文件比对 agent 基线与主线是否漂移，未漂移才整体拷贝，漂移的手工合。
- **改核心算法要独立 A/B**：取出改动前版本，同 seed 跑一遍比对输出是否逐字节一致。
  不采信 agent 自己写测试自证。
- **禁止 agent 编辑共享文件**（`router.py`/`App.tsx`/`Sidebar.tsx`/`types/index.ts`），
  让其返回集成片段。**特别注意寄生挂载**：曾有 agent 在端点模块尾部追加
  `include_router` 绕过禁令，两处同时保留会导致路由重复注册。

---

## 五、后续 backlog

### 已明确判断「暂不做」及理由

| 项 | 判断 |
|---|---|
| 向量化回测引擎 | **负收益**。引擎只占 1.5%–5%，86%–97.6% 在策略里。见 [docs/profiling-backtest-j2.md](docs/profiling-backtest-j2.md) |
| 启用 torch | 见 §三.8 |
| `_SingleSymbolAdapter` 惰性 history | 12.4ms / 15%，但要改公开 dataclass 且被基线钉着，风险不成比例 |
| `ContractSpec` 支持 FX/CRYPTO | 真要接时再改（缺 currency/exchange/settlement，pip 报价与资金费率也建模不了）|

### 真缺口

- **`bias_detection` 只在单个切点扰动**，灵敏度约等于 H/N。实测偷看 1 根漏报、
  2 根仅 1/97 个信号变化、3/5 根均漏报。它抓不到「预算指标含未来」这类泄漏 ——
  框架层的物理裁剪 + 因果抽检才是真防线，这个检测器只能算兜底。
- `grid_trading` / `pairs_trading` 模式 B 未做指标预算（有实据，见
  [waveGa 契约](docs/contracts/waveGa-remaining-presets.md)）。
- I3 新闻情绪因子未做（已判定与 AI 研报无耦合，是独立的因子路径）。
- PIT 基本面（point-in-time，防前视）。
- 更多数据源：BaoStock / Tushare / Polygon / Finnhub。

---

## 六、已知限制与坑

| 项 | 说明 |
|----|------|
| A股实时数据 | Docker 容器内访问中国金融 API 受限；容器内走演示兜底，本地 host 正常 |
| 港股实时 | 富途需本地 OpenD；无 OpenD 时走 AkShare 日线（非实时） |
| A股新闻 | **没有数据源**。AI 研报的 `sources` 对 A 股恒为空，消息面恒走「本期无可用新闻」 |
| 期权 IV | **仅美股**。港股/A 股写一条 `data_note` 说明 |
| `passlib` | **不可用**且已从依赖移除。它读 `bcrypt.__about__`，而 bcrypt 5.0 已删该属性。直接用 `bcrypt` |
| `docker compose restart` | 绕过 `depends_on healthy`，backend 可能抢跑崩。用 `stop && up -d` |
| pytest | 在 host `backend/.venv`；单跑子集加 `--no-cov`（否则覆盖率门禁会因只跑子集而红） |
| Playwright | 自带 Chromium 下载可能受网络限制；本机验证时可 `PW_CHROMIUM_CHANNEL=chrome` 用系统 Chrome。CI 不设此变量 |
| E2E 非阻塞 | CI 里 `continue-on-error: true`。收紧条件写在 workflow 注释里：连续 20 次绿 |

---

## 七、验收基线（回归自查清单）

```bash
# 1. 后端全测试
cd backend && .venv/bin/pytest -q                    # 期望 2861 passed，覆盖率 ≥74%

# 2. 回归红线（最重要）
.venv/bin/pytest tests/regression -q --no-cov        # 期望 146 passed
python -m tests.regression.generate_baseline
git diff tests/regression/                           # 必须为空

# 3. 导入环（防「看起来是绿的」循环依赖）
.venv/bin/pytest tests/test_import_cycles.py -q --no-cov

# 4. 前端
cd frontend && npm run lint && npx tsc --noEmit && npm test -- --run && npm run build

# 5. E2E（桩住后端，不连真实服务）
npm run test:e2e                                     # 期望 6 passed

# 6. 启动冒烟
docker compose -f infra/docker-compose.yml up -d
curl localhost:8000/health                           # liveness，不查依赖
curl localhost:8000/health/ready                     # readiness，逐项探测依赖
```

**访问**：http://localhost:3000 · 账号 `admin`/`admin123`
（⚠️ 三个内置账户的密码公开写在源码与文档里，**暴露在公网前必须先改**）。

---

## 八、参考仓库 `refs/` 与许可证

qlib · vnpy · **freqtrade** · jesse · OpenBB · PyPortfolioOpt · backtrader ·
zipline-reloaded · alpaca-py · py-futu-api · AlphaGPT。

⚠️ **freqtrade 是 GPL-3.0**：本项目从它那里**只读设计思路、独立实现**，
一行代码都没抄（K4 止损语义、N5 偏差检测、L4/L5 钩子、M1 归档设计、
M6 漂移检测、O2 解析器均如此）。zipline / Lean 是 Apache-2.0（可参照），
vnpy 是 MIT。**加新参考前先确认许可证。**
