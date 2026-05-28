# QuantBot — 多市场量化交易平台

> 支持美股 / 港股 / 沪深A股的一站式量化交易系统，集行情、回测、策略、风控、实盘于一体。

![Platform](https://img.shields.io/badge/platform-Docker-2496ED?logo=docker)
![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20Python%203.11-009688?logo=fastapi)
![Frontend](https://img.shields.io/badge/frontend-React%2018%20%2B%20TypeScript-61DAFB?logo=react)
![DB](https://img.shields.io/badge/database-TimescaleDB-FFA500?logo=postgresql)

---

## 目录

- [功能概览](#功能概览)
- [技术架构](#技术架构)
- [快速开始](#快速开始)
- [环境配置](#环境配置)
- [功能详解](#功能详解)
- [API 文档](#api-文档)
- [数据通道](#数据通道)
- [已知限制](#已知限制)
- [开发指南](#开发指南)

---

## 功能概览

| 模块 | 功能 |
|------|------|
| 📊 **行情** | 三市实时/延迟报价、K线图、16种技术指标 |
| 🔬 **回测** | 策略回测、蒙特卡洛模拟、参数优化 |
| 🤖 **策略** | 8种预设策略、自定义代码编辑器、实盘运行 |
| 💼 **持仓** | 多市场持仓聚合、绩效归因分析 |
| ⚠️ **风控** | 多维风控规则、VaR/CVaR、组合优化、再平衡 |
| 📈 **量化实验室** | GBM/BSM/GARCH/HMM、因子分析、Kelly公式、ML策略 |
| 📋 **订单** | 模拟盘/实盘区分、实时下单、取消管理 |
| 🔔 **价格预警** | 价格阈值告警、条件触发通知 |
| ⚙️ **设置** | 券商配置、数据通道状态检测 |

---

## 技术架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend  :3000                          │
│  React 18 + TypeScript + Vite                               │
│  TanStack Query · Recharts · lightweight-charts · Zustand   │
│  Monaco Editor (策略代码编辑) · Tailwind CSS                 │
└──────────────────────┬───────────────────────────────────────┘
                       │ REST API / WebSocket
┌──────────────────────▼───────────────────────────────────────┐
│                     Backend  :8000                           │
│  FastAPI + Python 3.11 + Uvicorn                            │
│  SQLAlchemy 2.0 (async) · Pydantic v2 · Celery              │
└────┬─────────────┬────────────┬────────────────┬─────────────┘
     │             │            │                │
┌────▼────┐  ┌─────▼─────┐  ┌──▼──────┐  ┌─────▼──────────┐
│TimescaleDB│  │   Redis   │  │ Alpaca  │  │ Futu OpenD     │
│  :5432  │  │   :6379   │  │  API    │  │ host:11111     │
└─────────┘  └───────────┘  └─────────┘  └────────────────┘
```

### 核心技术栈

**后端**
- FastAPI 0.136 + Python 3.11
- TimescaleDB（PostgreSQL 时序扩展）
- SQLAlchemy 2.0 异步 ORM
- Redis 7（配置存储、状态管理）
- Celery 5（异步任务队列）
- Pydantic v2（数据校验）

**前端**
- React 18 + TypeScript
- TanStack Query v5（服务端状态，8s 轮询行情）
- Recharts + lightweight-charts（图表）
- Monaco Editor（策略代码编辑）
- Zustand（客户端状态）
- Tailwind CSS（深色主题 UI）

**数据通道**
- 美股：Alpaca Markets API（实时成交价）
- 港股：yfinance 批量下载（日线收盘价）
- A股：AkShare 东方财富（本地网络环境）
- 历史数据：Alpaca Bars / yfinance / AkShare → TimescaleDB

---

## 快速开始

### 前置要求

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 4.x+
- [Git](https://git-scm.com/)
- 约 4 GB 磁盘空间（镜像 + 数据）

### 1. 克隆仓库

```bash
git clone https://github.com/lsgoodlionel/Quantitative-Trading-Platform.git
cd Quantitative-Trading-Platform
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填写以下字段：

```env
DB_PASSWORD=your_strong_password
SECRET_KEY=your_32_char_secret   # openssl rand -hex 32
```

其余字段（Alpaca Key、Futu OpenD 等）可在启动后通过 **设置页面** 配置。

### 3. 启动服务

```bash
# 开发模式（代码热重载）
make dev

# 或直接使用 Docker Compose
docker compose -f infra/docker-compose.yml up -d
```

### 4. 访问平台

| 服务 | 地址 |
|------|------|
| **前端** | http://localhost:3000 |
| **API 文档** | http://localhost:8000/docs |
| **健康检查** | http://localhost:8000/health |

### 5. 默认账号

```
用户名: admin
密码:   admin123
```

> ⚠️ 生产环境请立即通过 `POST /api/v1/auth/change-password` 修改密码。

---

## 环境配置

### Makefile 常用命令

```bash
make dev            # 启动开发环境（热重载）
make dev-monitor    # 开发 + Prometheus/Grafana 监控
make down           # 停止所有服务
make reset          # 停止并清除所有数据（谨慎！）
make test           # 运行前后端测试
make test-backend   # 仅运行后端测试
make logs           # 查看所有服务日志
make ps             # 查看服务状态
make shell-backend  # 进入后端容器 shell
make shell-db       # 进入数据库 shell
make gen-secret     # 生成随机 SECRET_KEY
```

### Docker Compose Profiles

```bash
# 启动 Celery 异步任务
docker compose -f infra/docker-compose.yml --profile celery up -d

# 启动监控（Prometheus + Grafana）
docker compose -f infra/docker-compose.yml --profile monitoring up -d
```

### 完整 .env 说明

```env
# 应用
ENVIRONMENT=development       # development | production
LOG_LEVEL=INFO

# 数据库
DB_USER=quantbot
DB_PASSWORD=CHANGE_ME
DB_NAME=quantbot

# JWT 认证
SECRET_KEY=CHANGE_ME          # openssl rand -hex 32

# CORS
ALLOWED_ORIGINS=http://localhost:3000

# 美股 Alpaca（https://alpaca.markets/）
ALPACA_API_KEY=               # PK开头=模拟盘，其他=实盘
ALPACA_SECRET_KEY=
ALPACA_PAPER=true

# 港股/美股 富途 OpenD（https://openapi.futunn.com）
FUTU_HOST=127.0.0.1           # Docker内用 host.docker.internal
FUTU_PORT=11111
FUTU_TRADE_ENV=SIMULATE       # SIMULATE | REAL

# 告警通知（可选）
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 监控
GRAFANA_PASSWORD=CHANGE_ME
```

---

## 功能详解

### 📊 行情页（Market）

三市报价面板 + K线图 + 技术指标。

**股票面板**
- A股 / 港股 / 美股三个 Tab 切换
- 实时搜索（代码/中文名）+ 全库搜索回落
- 涨跌幅统计（上涨/下跌/平盘数量）
- 底部显示数据来源（实时/延迟/日线/演示）和更新时间

**K线图**
- 支持 1分钟 / 5分钟 / 15分钟 / 1小时 / 日线 / 周线
- 时间范围：1月 / 3月 / 6月 / 1年 / 2年
- 叠加 16 种技术指标：
  ```
  SMA · EMA · RSI · MACD · 布林带
  ATR · ADX · Stochastic · CCI · OBV
  VWAP · Williams %R · ROC · MFI · Donchian · Keltner
  ```

**实时行情轮询**（8秒间隔）
- 美股：Alpaca StockLatestTradeRequest（33支）
- 港股：yfinance 批量下载（30支，日线收盘价）
- A股：AkShare 东方财富（需本地网络环境）

---

### 🔬 回测页（Backtest）

**基础回测**
- 选择策略（预设或自定义）+ 标的 + 市场 + 时间范围 + 频率
- 初始资金、手续费率可配置
- 结果展示：净值曲线、回撤曲线、月度热力图
- 核心指标：总收益率、年化收益、Sharpe/Sortino/Calmar、最大回撤、胜率、盈亏比

**蒙特卡洛模拟**
- 基于历史收益率分布，模拟 N 条路径
- 展示置信区间（5th / 25th / 50th / 75th / 95th 百分位）

**参数优化**
- 网格搜索策略参数组合
- 按目标指标（Sharpe / 总收益 / 最大回撤等）排序

---

### 🤖 策略页（Strategies）

**8种预设策略**

| 策略 | 描述 |
|------|------|
| `double_ma` | 双均线金叉死叉 |
| `bollinger` | 布林带突破 / 均值回归 |
| `macd` | MACD 金叉死叉 |
| `rsi_mean_reversion` | RSI 超买超卖均值回归 |
| `momentum` | 动量策略（N日涨跌幅排序） |
| `grid_trading` | 网格交易 |
| `pairs_trading` | 配对交易（协整） |
| `multi_factor` | 多因子选股 |

**自定义策略**
- 继承 `StrategyBase`，实现 `on_bar()` / `on_start()` / `on_stop()`
- 在线代码编辑器（Monaco）实时编写
- 语法验证 + 一键保存
- 可直接启动实盘/模拟盘运行

---

### 📈 量化实验室（AlgoLab）

**数学模型工具**

| 工具 | 用途 |
|------|------|
| **GBM** | 几何布朗运动价格路径模拟 |
| **BSM** | Black-Scholes 期权定价（Call/Put/隐含波动率） |
| **GARCH** | 波动率建模与预测 |
| **Kelly 公式** | 最优仓位计算 |
| **协整检验** | 配对交易选股（ADF / Johansen） |
| **HMM** | 隐马尔可夫市场状态识别（牛熊震荡） |
| **PCA** | 主成分因子降维 |
| **Copula** | 多资产联合分布建模 |

**因子分析**
- 多维因子计算（动量、波动率、市值、价值等）
- IC / ICIR / 因子收益归因

**ML 策略训练**
- 特征工程 + 监督学习（分类/回归）
- 自动生成交易信号

---

### ⚠️ 风控页（Risk）

**风控规则配置**

| 规则 | 含义 |
|------|------|
| `max_position_pct` | 单标的最大持仓比例 |
| `max_order_value` | 单笔最大订单价值 |
| `max_daily_orders` | 每日最大订单数 |
| `daily_loss_limit` | 每日亏损限额 |
| `max_drawdown` | 最大回撤触发停止 |
| `max_leverage` | 最大杠杆倍数 |
| `allowed_markets` | 允许交易的市场 |
| `allowed_symbols` | 允许交易的标的白名单 |

**量化风险指标**
- VaR（历史模拟法 / 参数法 / 蒙特卡洛）
- CVaR（条件风险价值）
- Beta / 相关系数矩阵

**组合优化**
- 等权重 / 风险平价 / 最大化Sharpe / 最小化波动率
- 权重约束 + 再平衡建议生成

---

### 💼 持仓 / Dashboard

- 多市场持仓汇总（美股 / 港股 / A股）
- 账户资金概览（总资产、可用资金、市值）
- PnL 分布图
- 绩效归因：按市场 / 时间 / 持仓分解收益来源

---

### 📋 订单页（Orders）

- 模拟盘（蓝色）/ 实盘（红色）横幅区分
- 实时订单列表：市价单 / 限价单
- 订单状态跟踪：待成交 / 已成交 / 已取消
- 手动下单 / 取消订单

---

### 🔔 价格预警（Alerts）

- 设置标的价格触发条件（高于 / 低于）
- 告警触发后通知（支持 Telegram Webhook）
- 手动重置已触发的告警

---

### ⚙️ 设置页（Settings）

**券商配置**
- Alpaca Markets（美股，API Key / Secret）
- 连接测试（显示账户ID + 可用资金）
- 自动识别 PK前缀（模拟盘）/ 其他（实盘）

**数据通道状态**
- A股：AkShare 安装状态 + 连通性探测
- 港股：Futu OpenD 安装 + OpenD 连接状态
- 港股备用：yfinance 安装状态

---

## API 文档

启动服务后访问 **http://localhost:8000/docs**（Swagger UI）。

### 主要端点

```
# 行情
GET  /api/v1/bars                    # K线历史数据
GET  /api/v1/bars/spot               # 三市实时快照（8s 轮询）
GET  /api/v1/bars/market-overview    # 市场概览
GET  /api/v1/bars/indicators         # 技术指标计算
GET  /api/v1/bars/symbols/search     # 股票搜索

# 回测
POST /api/v1/backtests/run           # 运行回测
POST /api/v1/backtests/optimize      # 参数优化
POST /api/v1/backtests/montecarlo    # 蒙特卡洛模拟

# 策略
GET  /api/v1/strategies              # 策略列表
POST /api/v1/strategies              # 创建策略
GET  /api/v1/strategies/presets      # 预设策略
POST /api/v1/strategies/{id}/start   # 启动策略
POST /api/v1/live-strategies/start   # 实盘启动

# 订单与持仓
POST /api/v1/orders                  # 下单
GET  /api/v1/orders                  # 订单列表
POST /api/v1/orders/{id}/cancel      # 取消订单
GET  /api/v1/positions               # 持仓列表
GET  /api/v1/positions/account       # 账户信息

# 风控
GET  /api/v1/risk                    # 风控配置
PUT  /api/v1/risk                    # 更新风控配置
POST /api/v1/risk/check/pre-trade    # 下单前风控检查
POST /api/v1/risk/var                # VaR 计算
POST /api/v1/risk/portfolio/optimize # 组合优化

# 量化工具
POST /api/v1/quant/gbm               # GBM 模拟
POST /api/v1/quant/bsm               # BSM 期权定价
POST /api/v1/quant/garch             # GARCH 波动率
POST /api/v1/quant/kelly             # Kelly 公式
POST /api/v1/quant/cointegration     # 协整检验
POST /api/v1/quant/hmm               # HMM 状态识别
POST /api/v1/quant/factor/analyze    # 因子分析
POST /api/v1/quant/ml/train          # ML 策略训练

# 配置
GET  /api/v1/broker-config           # 券商配置查询
POST /api/v1/broker-config/alpaca    # 保存 Alpaca 配置
POST /api/v1/broker-config/alpaca/test  # 测试连接
GET  /api/v1/data-config/status      # 数据通道状态
```

---

## 数据通道

### 美股（US）

| 用途 | 数据源 | 延迟 |
|------|--------|------|
| 实时报价 | Alpaca StockLatestTrade | 模拟盘免费 |
| 历史K线 | Alpaca Bars API | T+0 |
| 配置 | Settings → Alpaca | API Key 需在官网申请 |

申请地址：https://alpaca.markets/

> **注意**：API Key 以 `PK` 开头为模拟盘，系统自动使用 `paper-api.alpaca.markets`。

### 港股（HK）

| 用途 | 数据源 | 延迟 |
|------|--------|------|
| 行情快照 | yfinance（Yahoo Finance） | T+0 收盘 |
| 实时报价 | Futu OpenD（需本地安装） | 15分钟 |
| 实盘下单 | Futu OpenD | 需本地安装 |

**Futu OpenD 安装**：
1. 下载 https://www.futunn.com/download/OpenAPI
2. 登录富途牛牛账号
3. 确认监听端口 `11111`
4. Docker 通过 `host.docker.internal:11111` 连接

### A股（沪深）

| 用途 | 数据源 | 延迟 |
|------|--------|------|
| 实时行情 | AkShare 东方财富 | 实时（需中国大陆网络） |
| 历史数据 | AkShare | T+1 |

> A股行情在 Docker 容器内访问中国金融 API 可能受网络限制，建议在本地网络环境部署。

---

## 已知限制

| 限制 | 说明 |
|------|------|
| A股实盘下单 | 暂不支持，A股仅支持行情和回测 |
| 期货交易 | 接口预留（`futures_base.py`），尚未实现 |
| A股Docker行情 | Docker容器无法直接访问中国金融API，需本地网络环境 |
| Futu OpenD | 需在本地主机单独运行，不包含在Docker Compose中 |
| 历史数据 | 免费Alpaca账号历史数据有限（IEX数据源） |

---

## 开发指南

### 目录结构

```
quantbot/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/    # FastAPI 路由
│   │   ├── core/                # 配置、数据库、JWT
│   │   ├── data/
│   │   │   ├── feeds/           # 数据源（Alpaca/yfinance/AkShare/Futu）
│   │   │   ├── models/          # SQLAlchemy ORM 模型
│   │   │   └── service.py       # 数据服务层
│   │   ├── engine/
│   │   │   ├── backtest/        # 回测引擎（SimulatedBroker + BacktestEngine）
│   │   │   └── live/            # 实盘事件循环
│   │   ├── gateway/             # 券商网关（Alpaca/Futu/IB/XTP）
│   │   ├── oms/                 # 订单管理系统
│   │   ├── quant/               # 量化算法库
│   │   ├── risk/                # 风控引擎
│   │   ├── strategy/
│   │   │   ├── base.py          # StrategyBase ABC
│   │   │   ├── context.py       # StrategyContext（buy/sell API）
│   │   │   ├── indicators.py    # 技术指标库
│   │   │   └── presets/         # 8种预设策略
│   │   └── tasks/               # Celery 异步任务
│   ├── tests/                   # 16个测试文件，174个测试用例
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/          # 可复用组件
│       ├── hooks/               # React Query hooks
│       ├── pages/               # 页面组件（12个页面）
│       └── types/               # TypeScript 类型定义
└── infra/
    ├── docker-compose.yml       # 服务编排
    └── monitoring/              # Prometheus + Grafana 配置
```

### 自定义策略示例

```python
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import sma

class MyStrategy(StrategyBase):
    params = {
        "fast_period": 10,
        "slow_period": 30,
    }

    def on_start(self, ctx: StrategyContext):
        self.fast_ma = []
        self.slow_ma = []

    def on_bar(self, ctx: StrategyContext):
        closes = ctx.closes  # 历史收盘价列表

        fast = sma(closes, self.params["fast_period"])
        slow = sma(closes, self.params["slow_period"])

        if fast[-2] < slow[-2] and fast[-1] > slow[-1]:
            # 金叉买入
            ctx.buy(value=ctx.account.cash * 0.95)
        elif fast[-2] > slow[-2] and fast[-1] < slow[-1]:
            # 死叉卖出
            ctx.sell_all()

    def on_stop(self, ctx: StrategyContext):
        ctx.sell_all()
```

### 运行测试

```bash
# 后端测试
make test-backend
# 或
docker exec qb_backend pytest tests/ -v

# 单个测试文件
docker exec qb_backend pytest tests/test_backtest_engine.py -v
```

---

## 服务端口

| 服务 | 端口 |
|------|------|
| 前端 | 3000 |
| 后端 API | 8000 |
| TimescaleDB | 5432 |
| Redis | 6379 |
| Grafana（监控） | 3001 |
| Prometheus（监控） | 9090 |

---

## License

MIT License — 仅供学习研究使用，实盘交易风险自担。
</content>
</invoke>