# V3 Wave D-c 契约：Nginx/SSL + 备份 + 负载测试（J5 中）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **J5**（Nginx+SSL / 备份 / 负载测试）· Agent-Dc
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿；**不改后端业务代码**
> （只新增 infra/ 与 scripts/ 下的文件，以及必要的 compose 接线）。

---

## 零、摸底结论

- `infra/nginx/` 目录**存在但是空的** —— compose 里没有反代，前端与后端各自裸奔
- `scripts/` 目录**存在但是空的** —— 没有任何备份/恢复脚本
- `infra/docker-compose.prod.yml` 有 timescaledb / redis / backend / frontend /
  prometheus / grafana，但没有反代、没有备份任务

---

## 一、Nginx 反向代理 + TLS

```
infra/nginx/
├── nginx.conf              主配置
├── conf.d/quantbot.conf    站点：/ → frontend，/api → backend，/ws → backend（websocket 升级）
└── README.md               证书怎么放、怎么续期
```

### 1.1 四条必须做对的

1. **WebSocket 必须显式配 `Upgrade` / `Connection` 头。** 平台有实时行情推送
   （`/api/v1/stream`），漏了这两行的表现是「页面能开、行情不动」，
   而且不报错 —— 是最难查的那类故障。
2. **`proxy_read_timeout` 要够长。** 完整验证、因子挖掘都是分钟级请求，
   默认 60s 会在跑到一半时切断，前端看到的是网络错误而非超时提示。
   给 `/api/` 设 300s，并在配置里注释说明为什么。
3. **TLS 证书不进仓库。** 用挂载卷 + `README.md` 说明；
   给出 self-signed 的本地生成命令供开发用，生产走 Let's Encrypt。
   `.gitignore` 要覆盖证书路径。
4. **不要在 Nginx 再配一遍安全响应头** —— D-a 已在应用层中间件里加了。
   两处都配会出现重复头，某些浏览器行为不确定。Nginx 只做代理与 TLS。

⚠️ 默认配置里 `server_name` 用占位符并在 README 写明必须改。
留一个 `localhost` 的默认值上生产，是很常见的翻车方式。

---

## 二、备份与恢复

```
scripts/
├── backup-db.sh       pg_dump（自定义格式）+ 保留策略
├── restore-db.sh      从备份恢复
└── README.md
```

### 2.1 三条必须做对的

1. **必须同时给恢复脚本，而且 README 要写「恢复演练」步骤。**
   一份从没恢复过的备份，与没有备份的区别只在于心理安慰。
2. **备份脚本要校验产物非空并在失败时返回非零退出码。**
   静默产出 0 字节的备份文件，比没有备份更危险 —— 它会让人以为有。
3. **保留策略要明确**（如保留 7 日 + 4 周），并且删除逻辑要**先确认新备份成功**
   再删旧的。顺序反了会在磁盘满的那天把所有备份一起清掉。

⚠️ 脚本里不要硬编码数据库口令，从环境变量或 `.pgpass` 读。

⚠️ **TimescaleDB 不是普通 Postgres**：`pg_dump` 对 hypertable 有额外注意事项，
README 要写清用的是哪种方式、以及恢复时超表是否需要重建。
这一点务必实际查证 TimescaleDB 文档，**不要想当然**。

---

## 三、负载测试

```
scripts/loadtest/
├── README.md
└── smoke.js | locustfile.py     二选一，说明为什么选它
```

### 3.1 定位要写清楚

这不是性能基准，是**冒烟级容量摸底**：几个只读端点在并发 N 下的
p50/p95 与错误率。

⚠️ **不要压写端点。** 压 `/orders` 会真的下单；压 AI 端点会真的花钱。
只压只读端点，并在 README 顶部用醒目文字写明这条禁令。

⚠️ **不进 CI。** 负载测试的结果依赖机器负载，在共享 runner 上跑出来的数字
没有可比性，只会制造噪音。定位为「发版前手动跑一次」。

---

## 四、验收

```
1. tests/regression 146 用例全绿；后端业务代码 git diff 为空
2. Nginx 配置：
   - 用 `nginx -t`（容器内）验证语法通过
   - 站点配置含 Upgrade/Connection 头（grep 断言）
   - /api/ 的 proxy_read_timeout ≥ 300s
   - server_name 是占位符而非 localhost
3. 备份脚本：
   - bash -n 语法检查通过
   - 干跑（或对空库跑）能产出非空文件；人为制造失败时退出码非零
   - restore 脚本存在且 README 含恢复演练步骤
4. .gitignore 覆盖证书与备份产物路径
5. 负载测试 README 顶部含「禁止压写端点/AI 端点」的醒目说明
6. compose：反代服务接线正确，`docker compose -f ... config` 校验通过
```

## 五、不做

- 自动续期证书的 cron（README 给出 certbot 命令即可，具体调度依部署环境）
- 异地备份 / 对象存储上传（先把本地备份跑通）
- 负载测试进 CI（见 §3.1）
- 多实例负载均衡与会话粘性（当前是单实例部署）
