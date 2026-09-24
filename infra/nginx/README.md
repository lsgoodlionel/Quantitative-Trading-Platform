# QuantBot 边缘反向代理（Nginx + TLS）

单实例生产部署的统一入口：TLS 终止 → 反向代理到 `frontend` / `backend`。

```
外网 ──443/tls──▶ nginx ──┬── /                  ──▶ frontend:80   (SPA 静态站)
                          ├── /api/v1/stream/*   ──▶ backend:8000  (WebSocket)
                          └── /api/*             ──▶ backend:8000  (REST)
```

| 文件 | 作用 |
|---|---|
| `nginx.conf` | 主配置：worker / 日志 / **WebSocket 升级 map** / upstream |
| `conf.d/quantbot.conf` | 站点：80 跳转 + ACME，443 正式入口与全部 location |
| `certs/` | 证书挂载点，**内容不进仓库**（见 `.gitignore`） |

---

## 一、上线前必改（三项，缺一不可）

1. **域名**。`conf.d/quantbot.conf` 里两处 `server_name quantbot.example.com`
   改成真实域名。这里刻意没用 `localhost` / `_` 作默认值 —— 带着 localhost
   上生产是常见翻车方式，用 `example.com` 能让问题立刻暴露而不是静默生效。
2. **证书**。把 `fullchain.pem` / `privkey.pem` 放进挂载目录（见下）。
   **证书不存在时 nginx 会启动失败**，这是有意为之，不要改成可选。
3. **前端 WebSocket 地址**。前端 `src/lib/ws.ts` 的默认值是
   `ws://localhost:8000`，构建时必须注入：

   ```bash
   VITE_WS_URL=wss://your-domain.example.com/api/v1/stream
   ```

   ⚠️ 不注入的表现同样是「页面能开、行情不动」：浏览器会去连
   `ws://localhost:8000`，而且 HTTPS 页面里的 `ws://` 明文连接会被
   浏览器直接拦截。**改完域名务必连这一条一起改。**

---

## 二、证书怎么放

挂载点由 compose 的 `TLS_CERT_DIR` 决定（默认 `./nginx/certs`）：

```yaml
- ${TLS_CERT_DIR:-./nginx/certs}:/etc/nginx/certs:ro
```

容器内固定读取：

```
/etc/nginx/certs/fullchain.pem
/etc/nginx/certs/privkey.pem
```

### 开发 / 自签（仅本地）

```bash
mkdir -p infra/nginx/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout infra/nginx/certs/privkey.pem \
  -out    infra/nginx/certs/fullchain.pem \
  -subj   "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

自签证书只用于本地联调（浏览器会告警）。**不要拿去生产。**

### 生产（Let's Encrypt）

首签走 webroot 模式。compose 已把 `certbot_webroot` 卷同时挂到
nginx 的 `/var/www/certbot`，站点配置里已有
`location /.well-known/acme-challenge/`。

```bash
# 1) 先让 nginx 起来（80 端口可达，443 可以先用自签占位）
docker compose -f infra/docker-compose.prod.yml up -d nginx

# 2) 申请证书
docker run --rm \
  -v quantbot_certbot_webroot:/var/www/certbot \
  -v "$(pwd)/infra/nginx/certs:/out" \
  -v certbot_etc:/etc/letsencrypt \
  certbot/certbot certonly --webroot -w /var/www/certbot \
    -d your-domain.example.com \
    --email ops@example.com --agree-tos --no-eff-email

# 3) 把签发结果放到挂载目录
#    （certbot 产物在 /etc/letsencrypt/live/<domain>/）

# 4) 重载
docker compose -f infra/docker-compose.prod.yml exec nginx nginx -s reload
```

> 卷名前缀取决于 compose 项目名（默认是 `infra`，即 `infra_certbot_webroot`）。
> 用 `docker volume ls` 确认实际名称。

### 续期

Let's Encrypt 证书 90 天有效期。续期本身是一条命令：

```bash
docker run --rm \
  -v <webroot-volume>:/var/www/certbot \
  -v certbot_etc:/etc/letsencrypt \
  certbot/certbot renew --webroot -w /var/www/certbot --quiet
# 续期后必须 reload，nginx 不会自动感知证书文件变化
docker compose -f infra/docker-compose.prod.yml exec nginx nginx -s reload
```

**自动调度（cron / systemd timer / k8s CronJob）由部署环境决定，本 wave 不预设。**
无论用哪种，记住两点：① 每天跑一次即可，certbot 会自己判断是否临近到期；
② **续期后一定要 reload**，忘了 reload 是「证书明明续了却还是过期」的经典原因。

---

## 三、为什么这里没有安全响应头

CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy /
Permissions-Policy / **HSTS** 全部由应用层中间件统一注入（Wave D-a）。

Nginx 再配一遍会出现重复头。重复头的行为在各浏览器间不完全一致
（CSP 重复时按最严格的交集执行，其他头多数取第一个），
调起来很痛苦。**单一来源优于两处兜底。**

本层只保留 `server_tokens off`（隐藏 nginx 版本号）—— 那是代理自身的
信息暴露，不在应用层的职责范围内，不构成重复。

### 一个已知的边界情况（部署时需自行判断）

`location /` 代理到前端容器的**静态文件**，这条链路不经过后端应用中间件，
因此 SPA 的 `index.html` 与静态资源**拿不到应用层的安全响应头**。

如果安全评审要求静态站也带头，有两个选择，**任选其一，不要两个都做**：

- 在 `frontend/nginx.conf`（前端容器内）加，作用域刚好只覆盖静态资源；
- 或在本文件的 `location /` 块里加，并同步确认应用层不会对 `/api/` 重复注入。

当前默认不做，属于有意识的取舍，记录在此以免被当成遗漏。

---

## 四、验证

```bash
# 语法检查（容器内）
docker compose -f infra/docker-compose.prod.yml exec nginx nginx -t

# WebSocket 升级头是否还在（改配置后跑一次，防止被误删）
grep -c 'proxy_set_header   Upgrade' infra/nginx/conf.d/quantbot.conf   # 期望 2

# /api/ 读超时是否 ≥ 300s
grep -n 'proxy_read_timeout' infra/nginx/conf.d/quantbot.conf

# server_name 不是 localhost
grep -n 'server_name' infra/nginx/conf.d/quantbot.conf
```

端到端确认 WebSocket 真的通了（最有价值的一条）：

```bash
curl -isk --http1.1 -o /dev/null -w '%{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://your-domain.example.com/api/v1/stream/bars
# 期望 101（Switching Protocols）。
# 拿到 200 → 升级头没生效，退化成了普通 HTTP 请求，就是「行情不动」的成因。
```

> ### ⚠️ `--http1.1` 不能省，否则会得到假阴性
>
> 本站点开了 HTTP/2。而 HTTP/2 协议**禁止** `Connection` / `Upgrade`
> 这类逐跳（hop-by-hop）头，curl 走 h2 时会直接把它们丢掉，
> 于是 nginx 侧 `$http_upgrade` 为空 —— 你会看到 200 而不是 101，
> **误以为配置坏了**。（本 wave 实测踩过这一脚：不加 `--http1.1` 时
> 后端收到的是 `Upgrade=<MISSING>` / `Connection=close`；
> 加上之后立刻变成 `Upgrade=websocket` / `Connection=upgrade`。）
>
> 真实浏览器不受影响：nginx 不支持 RFC 8441（HTTP/2 上的扩展 CONNECT），
> 不会通告 `SETTINGS_ENABLE_CONNECT_PROTOCOL`，因此浏览器执行
> `new WebSocket(...)` 时会自动另开一条 HTTP/1.1 连接来完成握手。
> 页面本身仍然走 HTTP/2。两者并存，符合预期。

顺带验证普通 REST 请求**没有**被误加升级头（对照组）：

```bash
curl -sk --http1.1 -D- -o /dev/null https://your-domain.example.com/api/v1/health
# 后端侧应看到 Connection: close，不应出现 Upgrade 头。
# 这正是 nginx.conf 里那个 map 的作用：只在真的握手时才升级。
```

---

## 五、本 wave 不做

- 证书自动续期的调度（命令已给，调度依部署环境）
- 多实例负载均衡与会话粘性（当前单实例）
- 边缘限流 / WAF（应用层已有速率限制）
