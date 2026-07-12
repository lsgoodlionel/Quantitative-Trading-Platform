# QuantBot 移交文档（Handoff）

> 面向后续开发者/运维的阶段性移交。快照时间：2026-07-12 · 版本：v2.0（Wave 1-3 + 平台化 完成）
> 配套：[README.md](README.md)（使用）· [DEVPLAN_V2.md](DEVPLAN_V2.md)（路线图）· [docs/contracts/](docs/contracts/)（接口契约）

---

## 一、当前状态一句话

多市场（美股/港股/A股）AI 量化研究 + 实盘工作站，**v2.0 全部三波已交付**：
选股 → 因子研究(含 AI 挖掘/序列模型) → 策略验证 → 组合构建 → 实盘执行 → 多用户/审计，全流程闭环。

- **118** 个 API 端点 · **16** 个前端页面 · **593** 单元测试全绿 · **6** 个真实数据源 + 演示兜底
- 后端 FastAPI+async · 前端 React18+TS+Vite · TimescaleDB + Redis + Celery · Docker Compose

---

## 二、代码地图（哪块功能在哪）

### 后端 `backend/app/`

| 模块 | 职责 | 关键文件 |
|------|------|---------|
| `data/` | 多源数据层 | `service.py`(门面+缓存) · `source_registry.py`(多源配置/动态切换) · `feeds/*`(6源+demo) · `providers/`(基本面) · `screener.py` · `pairlist.py` |
| `engine/backtest/` | 回测+验证 | `engine.py`(主循环) · `hyperopt.py` · `walkforward.py` · `bias_detection.py` · `mc_robustness.py` · `significance.py` · `roundtrips/trade_analytics/tearsheet` 报告 |
| `engine/portfolio/` | 组合优化 | `optimizer.py` · `risk_models.py`(Ledoit-Wolf) · `black_litterman.py` · `hrp.py` · `cvar_opt.py` · `topk_dropout.py` · `discrete_allocation.py` |
| `quant/` | 量化算法+因子+AI | `formula_factor.py`(RPN引擎) · `factor_lib/`(声明式因子库) · `mining/genetic.py`(遗传挖掘) · `double_ensemble.py` · `models/sequence.py`(LSTM/GRU/ALSTM,lazy torch) · `bsm/garch/kelly/...` |
| `oms/` | 订单管理 | `manager.py`(OMS+dry-run一致) · `protections/`(熔断) · `algos/`(TWAP/VWAP/冰山) · `futu_wiring.py` |
| `gateway/` | 券商网关 | `alpaca_gateway.py`(已接) · `futu_gateway.py`(已接) · `paper_gateway.py`(默认) · ib/xtp(桩) |
| `risk/` | 风控 | `engine.py` · `var_engine.py` |
| `notify/` | 通知 | `dispatcher.py` · `channels/{telegram,webhook}.py` |
| `core/` | 基础设施 | `rbac.py`(角色权限) · `audit.py`(审计) · `config/redis/database/logging` |

### 前端 `frontend/src/`

| 路由 | 页面 | 说明 |
|------|------|------|
| `/` | Dashboard | 仪表盘 + 交易入口卡 + 智能引导 |
| `/market` `/market-events` | Market · MarketEvents | 行情 · 新闻/日历/期权 |
| `/screener` | Screener | 选股器 + 动态标的池 |
| `/strategies` `/backtest` | Strategies · Backtest | 16 策略 · 回测/Hyperopt/WalkForward/偏差/蒙特卡洛/稳健性 |
| `/live-strategy` `/orders` | LiveStrategy · Orders | 策略自动交易 · 手动下单+高级算法单 |
| `/portfolio` `/portfolio-optimizer` | Portfolio · PortfolioOptimizer | 持仓 · BL/HRP/CVaR/Topk/离散分配 |
| `/risk` `/alerts` | Risk · Alerts | 风控+熔断 · 价格预警 |
| `/factor` `/algolab` | FactorAnalysis · AlgoLab | 因子(处理/公式/库/挖掘/记录) · 12算法+ML+序列模型 |
| `/settings` | Settings | 券商配置 · **多源数据通道** · 通知 · 角色 · 审计 |

hooks 与页面同名对应（`useFactorAnalysis`/`useDataConfig`/`usePortfolio`/`useRbac` …）。

---

## 三、关键架构决策（改代码前必读）

1. **数据源多源冗余**：`DataSourceRegistry` 单例持有每市场有序源链 + Redis 配置（顺序/禁用/强制pin）。`DataService` 迭代源链逐个尝试，全失败降级 `DemoDataFeed`——**平台永不断供**。加新源：在 `feeds/` 加 `DataFeed` 子类 + `source_registry.SOURCE_CATALOG` 登记。
2. **OMS 网关路由**：`init_hybrid_order_manager` 启动时读 Redis `broker_config:alpaca`——配置则 US 用 AlpacaGateway(paper/live)，否则 PaperGateway；HK 有富途配置则 FutuGateway。手动/策略下单走同一 `submit_order`（dry-run 一致）。
3. **前端全走 Vite proxy**（同源，无 CORS）。**切勿设 `VITE_API_URL`**（会致跨域，Safari "Load failed"）。
4. **RBAC**：`require_role(Role.TRADER/ADMIN)` 依赖挂在写端点上；缺 role 的 JWT fail-safe 降级 viewer。测试需 `app.dependency_overrides[get_current_user]`。
5. **序列模型 lazy torch**：`quant/models/sequence.py` 惰性 import torch，未装时端点返 501。**torch 不在 requirements**（保持镜像轻量）；如需启用，装 torch 后端点自动可用。
6. **零新增重依赖原则**：ARM64 Docker 构建拉 gcc/pip 极慢。新功能优先复用已装 numpy/pandas/scipy/sklearn/httpx/akshare/yfinance。

---

## 四、多 agent 并行开发模式（本项目已验证）

v2.0 三波均以此模式交付，后续开发建议沿用：

```
契约先行 → N 个 agent 并行 implement→review(pipeline) → 主循环集成共享文件 → 验证 → 推送
```

**要点**：
- 禁止 agent 编辑共享文件（`router.py`/`App.tsx`/`Sidebar.tsx`/`main.py`/`requirements.txt`/`types/index.ts`）——让其返回集成片段，主循环统一 wire。
- review 把「router 未注册/免测试」标 `byDesign`，聚焦真实缺陷。
- 契约 agent 会提前捕获真相（依赖缺失、路径错误、去重），避免实现返工。
- 集成后必跑：`tsc --noEmit` + `pytest` + Docker 冒烟（端点 sweep）。

---

## 五、后续开发 backlog（按优先级）

### 高价值 · 已具备条件
- **B8 序列模型启用**：代码就绪（lazy torch）。评估 ARM64 装 torch 的镜像成本后，在 `requirements.txt` 加 torch 即激活训练。
- **F 平台化深化**：多用户注册/管理（当前仅内置 admin 单账户 + RBAC 骨架）、审计日志落库（当前 Redis stream）。
- **实盘对账**：定时拉取券商持仓/资金做差异对账（Alpaca/富途）。

### 数据/研究增强
- 更多数据源：BaoStock(A股,需装)、Tushare(需token)、Polygon/Finnhub(US,需key)。
- PIT 基本面（point-in-time，防前视）——qlib `data/pit.py` 参考。
- 实验记录器落库 + 因子/策略排行榜 UI 深化。

### 工程化
- 生产镜像烘焙（Docker Hub 网络恢复后 `docker compose build`；requirements 未变走缓存）。
- CI/CD（GitHub Actions：ruff+mypy+pytest / tsc+eslint）。
- Nginx+SSL / 数据库备份 / k6 负载测试（见 DEVPLAN Phase 7）。

---

## 六、已知限制与坑

| 项 | 说明 | 影响 |
|----|------|------|
| A股实时数据 | Docker 容器内访问中国金融 API 受限（AkShare RemoteDisconnected） | 容器内 A 股走演示兜底；本地 host 运行正常 |
| 港股实时 | 富途需本地 OpenD 运行；yfinance 常限流 | 无 OpenD 时港股走 AkShare 日线（非实时） |
| Stooq | 数据中心 IP 常被 403/返回 HTML | 优雅降级为空，链自动跳过 |
| Docker 构建 | ARM64 拉 Docker Hub 元数据偶超时 | dev 用卷挂载不受影响；重试或待网络恢复 |
| `docker compose restart` | 绕过 depends_on healthy，backend 可能抢跑崩 | 用 `stop && up -d` |
| pytest | 在 host `backend/.venv`（镜像无 pytest）；单跑子集加 `--no-cov` | — |
| 3 个 stale 测试 | 已在 162903c 修复；如再现检查 A股/RBAC override | — |

---

## 七、验收基线（回归自查清单）

```bash
# 1. 后端全测试（host venv）
cd backend && .venv/bin/pytest tests/ --no-cov -q          # 期望 593 passed

# 2. 前端类型检查
cd frontend && npx tsc --noEmit                             # 期望 0 error

# 3. 启动 + 端点 sweep（登录 admin/admin123）
docker compose -f infra/docker-compose.yml up -d
curl localhost:8000/health                                  # {"status":"ok"}
# 关键端点应全 200：/quant/processors/meta · /screener/presets · /quant/factor/library
#   /backtests/hyperopt/loss-functions · /quant/experiments · /orders/algo
#   /quant/ml/sequence-models · /audit · /data-sources/config · /data-sources/status
```

**访问**：http://localhost:3000 · 账号 `admin`/`admin123`（24h token，admin 全权限）。

---

## 八、参考仓库 `refs/`

qlib · vnpy · freqtrade · jesse · OpenBB · PyPortfolioOpt · backtrader · zipline-reloaded · alpaca-py · py-futu-api · **AlphaGPT**（因子挖掘）。
v2.0 各特性的算法来源见 [DEVPLAN_V2.md 第六节](DEVPLAN_V2.md) 参考映射表。
