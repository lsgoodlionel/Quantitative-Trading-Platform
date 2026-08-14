# V3 Wave D-a 契约：生产加固（J5 上）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **J5**（配置校验 · 健康检查 · 安全响应头）· Agent-Da
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 零、摸底结论：三个真实缺口

不是照蓝图空写，是实际查出来的：

| 缺口 | 现状 | 后果 |
|---|---|---|
| **默认密钥可用于生产** | `config.py:47` `secret_key = "CHANGE_ME_IN_PRODUCTION_USE_RANDOM_64_CHARS"`，无任何校验拦截 | 用公开已知的串签 JWT，**任何人都能伪造 admin token** |
| **健康检查是假的** | `main.py:108` 与 `router.py:60` 都直接返回 `{"status":"ok"}` | Postgres/Redis 全挂它照样 ok，负载均衡器会一直往死实例打流量 |
| **无安全响应头** | 全仓搜不到 HSTS / X-Frame-Options / X-Content-Type-Options | 点击劫持、MIME 嗅探无防护 |

---

## 一、生产配置校验（最重要）

在 `Settings` 上加**跨字段**校验（`model_validator(mode="after")`），
`environment == "production"` 时逐项检查并**在启动时就失败**：

```
secret_key      == 默认值 / 长度 < 32        → 拒绝启动
allowed_origins 含 "*" 或为空                 → 拒绝启动
debug           为 True                       → 拒绝启动
```

### 1.1 四条必须做对的

1. **必须在启动时失败，不能是运行时警告。** 一条 WARNING 在容器日志里滚过去
   等于没有；而一个用默认密钥跑着的生产实例，是在持续签发可伪造的令牌。
2. **错误信息要说清怎么修**，附上生成命令（`Makefile` 里已有 `gen-secret`）。
   「Invalid configuration」这种报错只会让人去翻源码。
3. **只在 production 下强制。** development / staging 保持零配置可启动 ——
   把开发也卡住，人只会去改 `environment` 绕过，控制反而失效。
4. **默认值的判定要基于常量而非字面量重复。** 把
   `INSECURE_SECRET_KEY_DEFAULT` 提成模块级常量，字段默认值与校验都引用它，
   否则改了一处忘了另一处，校验就静默失效了。

   > ⚠️ **但只比对这一个常量会漏掉最常见的上生产路径。** `.env.example` 里的占位串
   > 是 `CHANGE_ME_RUN_make_gen-secret`，**与代码默认值根本不是同一个串** ——
   > 照抄 `.env.example` 的部署，常量比对完全拦不住。
   > 目前是长度检查（29 < 32）恰好兜住了它，但那是巧合：占位串一变长就静默失效。
   > 必须再加一条 `CHANGE_ME` 标记检测，并有一条专门断言「长占位串也被拒」的用例。

⚠️ **不要把 `secret_key` 的值写进任何日志或错误信息**，哪怕是「当前值是 xxx」。

---

## 二、真实的健康检查

```
GET /health           轻量存活探针（liveness）：进程活着就 200，不查依赖
GET /health/ready     就绪探针（readiness）：逐项探测依赖
```

```json
{
  "status": "ok | degraded | down",
  "version": "...",
  "environment": "...",
  "checks": {
    "postgres": {"ok": true,  "latency_ms": 3.1},
    "redis":    {"ok": false, "error": "Connection refused"}
  }
}
```

### 2.1 三条必须做对的

1. **liveness 与 readiness 必须分开。** 把依赖检查塞进 `/health` 会让
   「Redis 抖了一下」变成「容器被 kill 重启」—— 重启并不能修好 Redis，
   只会让服务在滚动重启里反复横跳。
2. **readiness 探测要有超时**（建议 2s）。一个探测卡住会让编排系统
   等到自己的超时，那期间实例状态是未知的。
3. **`down` 与 `degraded` 要分开**：Postgres 挂 = `down`（503，核心数据没了）；
   Redis 挂 = `degraded`（200，缓存/通知降级但主要功能仍在）。
   全判成 down 会让一次缓存抖动摘掉整个集群。

⚠️ **`/health/ready` 是免鉴权端点，绝不能把驱动层原始异常直接吐出去。**
（原稿的响应示例就这么写的。）SQLAlchemy/asyncpg 的连接类异常会带上
`postgresql+asyncpg://user:password@host/db` —— 那这个端点就成了一个
免鉴权的数据库口令泄露口。出口必须过一层脱敏：正则抹掉连接串凭证 + 截断。

⚠️ **版本号实际散在 4 处**（`pyproject.toml` 是源头、`main.py` 两处、`router.py` 一处，
原稿只说了两处），提成 `app/core/version.py` 的单一常量。

> **不要用 `importlib.metadata.version()` 读 pyproject**：镜像是以源码目录方式运行的，
> 没有 `pip install .`，容器里会抛 `PackageNotFoundError`。
> 用常量，另加一条测试以 `tomllib` 断言它与 `pyproject.toml` 一致 ——
> 漂移照样会红，但不引入运行时依赖。

⚠️ **探测必须并行**（`asyncio.gather`）。串行时总耗时是各超时之和，
两个依赖同时挂就翻倍到 4s，很容易越过编排系统自己的超时，
退化成「探测无响应」—— 那比一个明确的失败更糟。

---

## 三、安全响应头中间件

```
Strict-Transport-Security: max-age=31536000; includeSubDomains   （仅 production）
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

⚠️ **HSTS 只在 production 且走 HTTPS 时下发。** 在 `http://localhost` 上下发 HSTS
会把开发者的浏览器锁死在 https 上，之后本地开发全部打不开，
而且清起来很麻烦（要进 `chrome://net-internals`）。

> **「走 HTTPS」不能只看 `scope["scheme"]` —— 照字面实现等于永不生效。**
> 生产里 TLS 由 Nginx 终止（就是 §五点名的 D-c），到达应用时 scheme 恒为 `http`，
> 于是 HSTS 在唯一需要它的环境里永远不下发。必须同时读 `X-Forwarded-Proto`
> （多级代理会串成 `"https, http"`，取第一段）。
>
> **这给 D-c 留了个前置条件**：Nginx 必须设 `proxy_set_header X-Forwarded-Proto $scheme`。
> 伪造该头不构成攻击 —— 至多让攻击者自己的浏览器收到 HSTS。

⚠️ **中间件必须注册在 CORS 之后。** `add_middleware` 是往**外层**加，后加的在更外层。
加在 CORS 之前的话安全头就成了内层，CORS 自己短路返回的预检（OPTIONS）响应
不会带安全头，与 §四「四个通用头恒在」不符。

⚠️ **必须写成纯 ASGI 中间件，不要用 `BaseHTTPMiddleware`。** 本项目有 SSE 端点
（`/api/v1/stream/*`），后者会把响应包进中转的 `StreamingResponse`，
影响事件缓冲与断连传播。纯 ASGI 只改 `http.response.start` 的头部。

⚠️ **不在本期加 CSP。** 前端用了 Monaco 编辑器，它需要 worker 与 blob URL，
一个没测过的 CSP 会静默打碎编辑器。要做得先在真浏览器里逐条验证 —— 单列一项。

---

## 四、验收

```
1. tests/regression 146 用例全绿
2. tests/test_production_config.py:
   - production + 默认 secret_key → 启动失败，且报错含修复命令
   - production + secret_key 长度不足 → 启动失败
   - production + allowed_origins 含 "*" → 启动失败
   - production + debug=True → 启动失败
   - development 下以上全部**允许**（零配置可启动）
   - 报错信息**不含** secret_key 的实际值
3. tests/test_health.py（**该文件已存在**且含 bars/presets/risk 三个无关的既有测试，
   按「新建」照做会静默删掉它们 —— 追加，不要覆盖）:
   - /health 不触碰任何依赖（mock 断言零调用），依赖全挂仍 200
   - /health/ready：Postgres 挂 → status=down 且 HTTP 503
   - /health/ready：仅 Redis 挂 → status=degraded 且 HTTP 200
   - 探测超时被兜住，不会挂起
   - 版本号只有一个来源（断言两个端点返回同一个值且来自常量，
     并以 tomllib 断言与 pyproject.toml 一致）
   - error 字段经过脱敏：含连接串凭证的异常不会把口令吐出去
4. tests/test_security_headers.py:
   - 四个通用头恒在
   - HSTS 仅 production 下发；development 下**不**下发
5. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 五、不做

- CSP（见 §三，需真浏览器逐条验证 Monaco）
- 限流中间件（AI 端点已有日配额；全局限流要先定清楚按 IP 还是按用户）
- 密钥轮转机制
- Nginx 配置（D-c）
