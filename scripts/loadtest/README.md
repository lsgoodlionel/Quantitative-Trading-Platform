# 负载测试 — 冒烟级容量摸底

> # 🚫 禁止压测写端点与 AI 端点 🚫
>
> | 禁止 | 原因 |
> |---|---|
> | `/api/v1/orders/**` | **会真的下单。** 实盘模式下这是真钱。 |
> | `/api/v1/live-strategies/**` | 会影响真实持仓 |
> | `/api/v1/rebalance/**` | 会产生真实交易 |
> | `/api/v1/broker-config/**`、`/api/v1/data-config/**` | 会改动运行配置 |
> | `/api/v1/llm/**`、`/api/v1/ai-reports/**`、`/api/v1/copilot/**` | **会真的花钱**，按 token 计费 |
> | `/api/v1/notify/**`、`/api/v1/notifications/**` | 会真的把消息发出去 |
> | 因子挖掘 / 完整验证 / 稳健性检验 | 分钟级重计算，不属于冒烟范围 |
>
> 这条禁令**已在 `smoke.js` 里用代码强制**：目标 URL 命中上述模式时，
> 脚本在初始化阶段直接抛错退出，跑都跑不起来。
> 请不要绕过那段检查 —— 它挡的是「赶时间时顺手加一个端点」这种情况。

---

## 这是什么，不是什么

**是**：几个只读端点在并发 N 下的 p50 / p95 / 错误率，用来回答
「当前部署大概扛得住多少并发」和「这次发版有没有明显劣化」。

**不是**：性能基准测试。没有预热、没有控制变量、没有隔离环境。
数字受本机负载影响极大，**只有在同一台机器上纵向对比才有意义**。

---

## 为什么选 k6 而不是 Locust

| | k6 | Locust |
|---|---|---|
| 依赖 | 单个静态二进制，或直接 `docker run grafana/k6` | 需要 Python 环境 + gevent |
| 与后端环境的耦合 | 无 | 装在同一个 venv 里有依赖冲突风险 |
| p50/p95 | 原生输出，带 threshold 断言 | 需自行处理 |
| 与现有栈的契合 | 项目已有 Grafana，k6 同属 Grafana 生态，后续可直接出图 | — |

决定性理由是第二条：后端是 Python，Locust 装进同一环境会引入
依赖冲突风险；单独建 venv 又多一套要维护的东西。k6 零依赖、
`docker run` 就能跑，对一个「发版前手动跑一次」的工具来说更合适。

---

## 不进 CI

负载测试的结果依赖机器负载。共享 runner 上跑出来的数字彼此不可比，
只会制造噪音和随机失败的红色构建，最终导致大家忽略它。

**定位：发版前在目标机器上手动跑一次，把结果贴进发版记录。**

---

## 运行

### Docker（推荐，无需安装）

```bash
# 压本机后端
docker run --rm -i --network host \
  -e QB_BASE_URL=http://localhost:8000 \
  -v "$(pwd)/scripts/loadtest:/scripts" \
  grafana/k6 run /scripts/smoke.js

# 压经过反代的生产入口（更接近真实链路）
docker run --rm -i \
  -e QB_BASE_URL=https://your-domain.example.com \
  -e QB_VUS=20 -e QB_DURATION=60s \
  -v "$(pwd)/scripts/loadtest:/scripts" \
  grafana/k6 run /scripts/smoke.js
```

> macOS / Windows 的 Docker Desktop 不支持 `--network host`，
> 改用 `QB_BASE_URL=http://host.docker.internal:8000`。

### 本机安装 k6

```bash
brew install k6          # macOS
k6 run scripts/loadtest/smoke.js
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QB_BASE_URL` | `http://localhost:8000` | 目标地址 |
| `QB_VUS` | `10` | 并发虚拟用户数 |
| `QB_DURATION` | `30s` | 持续时长 |
| `QB_TOKEN` | 空 | Bearer token；端点返回 401/403 时需要设置 |

---

## 怎么读结果

```
════ QuantBot 容量摸底结果 ════
  目标      : http://localhost:8000
  并发/时长 : 10 VU / 30s
  p50       : 12.4 ms
  p95       : 48.9 ms
  p99       : 103.2 ms
  错误率    : 0.00 %
```

- **错误率 > 0** —— 先查后端日志，不要先怀疑容量。冒烟阶段任何错误都值得查。
- **p95 比上次发版明显变高** —— 大概率是新增了同步阻塞调用或缺索引的查询。
- **401 / 403** —— 是鉴权配置问题，不是容量问题。设置 `QB_TOKEN` 后重跑。
- 单调高并发（`QB_VUS=100`）跑出来的绝对值不要当承诺值对外说。

---

## 加端点的规矩

改 `smoke.js` 的 `TARGETS` 前，逐条确认：

1. 是 **GET**；
2. **不改变任何状态**（不下单、不改配置、不写库）；
3. **不调用 LLM 或其他按量计费的外部服务**；
4. 不是分钟级的重计算任务。

四条全满足才能加。有一条不确定，就不要加。
