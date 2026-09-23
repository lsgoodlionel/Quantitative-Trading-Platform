# 部署

## 一条命令

```bash
export QB_VERSION=4.0.0                                   # 见 Releases 页
docker compose -f infra/docker-compose.prod.yml pull
docker compose -f infra/docker-compose.prod.yml up -d
```

前后端镜像由 CI 预先构建好推到 GHCR（`linux/amd64` + `linux/arm64`），
部署机只需 `pull`。

## 为什么不在部署机上构建

早先 `docker-compose.prod.yml` 里写的是 `build:`，每次部署都现场编译。两个问题：

1. **慢。** 后端要装 numpy / scipy / pandas / cvxpy，ARM64 上尤其难熬。
2. **不稳。** 构建失败要到部署那一刻才暴露 —— 那是最不该出意外的时刻。
   现在构建失败会在 CI 里红，根本走不到发布。

## 为什么钉版本号而不是 latest

`QB_VERSION` 是**必填**的，漏设会直接报错并告诉你怎么改，不会静默回落到 `latest`。

`latest` 是浮动标签：两台机器在不同时间 `pull`，拿到的可能不是同一个镜像，
而这种不一致排查起来极其费时。版本标签是不可变的 ——
**同一个 `QB_VERSION` 在任何机器、任何时间拉到的都是同一个镜像**。

回滚就是把版本号改回上一个再 `up -d`。

## 发布新版本

```bash
# 1. 改版本号（两处必须一致，有测试看守）
#    backend/app/core/version.py 的 APP_VERSION
#    backend/pyproject.toml 的 version
# 2. 打标签推送，CI 自动构建镜像并创建 Release
git tag v4.1.0 && git push origin v4.1.0
```

发布只由**打标签**触发，不挂在 push main 上 ——
发布该是一个明确的动作，而不是每次合并的副作用。

## 上线前必改

| 项 | 说明 |
|---|---|
| `SECRET_KEY` | 仍是 `.env.example` 的占位值时，后端**会拒绝启动**。用 `make gen-secret` 生成 |
| 三个内置账户密码 | `admin123` / `trader123` / `viewer123` 公开写在源码与文档里，暴露到公网前必须改（`PUT /api/v1/users/{id}`，需 ADMIN） |
| `ALLOWED_ORIGINS` | 含 `*` 时后端拒绝启动 |
| `server_name` | `infra/nginx/conf.d/quantbot.conf` 里是占位符，改成真实域名 |
| TLS 证书 | 放到 `TLS_CERT_DIR`，见 `infra/nginx/README.md`。证书不进仓库 |

## 健康检查

```bash
curl localhost:8000/health        # liveness：进程活着就 200，不查依赖
curl localhost:8000/health/ready  # readiness：逐项探测 Postgres / Redis
```

两者刻意分开：把依赖检查塞进 liveness 会让「Redis 抖了一下」变成
「容器被 kill 重启」，而重启并不能修好 Redis，只会让服务在滚动重启里反复横跳。

`ready` 的语义：Postgres 挂 = `down`（503，核心数据没了）；
仅 Redis 挂 = `degraded`（200，缓存与通知降级但主要功能仍在）。
全判成 down 会让一次缓存抖动摘掉整个集群。

## 已知坑

- **`docker compose restart` 会绕过 `depends_on: service_healthy`**，backend 可能抢在
  数据库就绪前启动而崩。用 `stop` + `up -d`。
- 富途 OpenD 未运行时日志会刷 `ECONNREFUSED`，不是故障 —— 港股走 AkShare 日线。
