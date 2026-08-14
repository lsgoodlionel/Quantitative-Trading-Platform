# QuantBot 运维脚本

| 脚本 | 作用 |
|---|---|
| `backup-db.sh` | TimescaleDB 逻辑备份（`pg_dump -Fc`）+ 产物校验 + 保留策略 |
| `restore-db.sh` | 从备份恢复（含 TimescaleDB 专有前置/后置流程）|
| `loadtest/` | 冒烟级容量摸底（**只压只读端点**，见该目录 README）|

---

## 一、备份用的是哪种方式，为什么

**方式：整库 `pg_dump` 自定义格式（`-Fc`）逻辑备份。**

TimescaleDB 官方（Tiger Data 文档）给出的自托管逻辑备份流程就是
`pg_dump -Fc` + `pg_restore -Fc`，并配套 `timescaledb_pre_restore()` /
`timescaledb_post_restore()` 两个函数。我们完全按这套来，没有自创。

来源：

- [Logical backup with pg_dump and pg_restore — Tiger Data / TimescaleDB 官方文档](https://www.tigerdata.com/docs/self-hosted/latest/backup-and-restore/logical-backup)
- [timescale/docs.timescale.com-content · using-timescaledb/backup.md（历史版本，`-j` 与选择性导出的限制说明更详细）](https://github.com/timescale/docs.timescale.com-content/blob/master/using-timescaledb/backup.md)

### 与普通 PostgreSQL 的四点区别（都会咬人）

1. **恢复时超表不需要手工重建。**
   `create_hypertable()` **不用再跑一遍**。超表元数据、chunk 结构、索引、
   约束、触发器都随 dump 一起导出（TimescaleDB 通过扩展的 `extconfig`
   机制把自己的目录表纳入了 pg_dump 范围），恢复后自动还原。
   如果恢复完发现超表变成了普通表，那不是「需要重建」，而是**备份方式错了**
   —— 见第 2 点。

2. **绝对不能用 `-t` / `--schema` 做选择性导出。**
   官方明确说明：加了这些标志，dump 里就缺少 TimescaleDB 理解
   「超表 ↔ chunk」关系所需的信息。产物看起来正常、大小也不小，
   但恢复出来的是一堆散装普通表。`backup-db.sh` 因此固定整库导出，
   并在 `restore-db.sh` 里比对超表清单，把这种情况拦成失败而不是「成功」。

3. **`pg_restore` 禁用 `-j` 并行。**
   官方原文：*"Do not use pg_restore with the -j option. This option does not
   correctly restore the TimescaleDB catalogs."* 大库恢复会慢，这是必须付的代价。

4. **必须记录 PostgreSQL 与 TimescaleDB 的版本。**
   `pg_dump` 不会把扩展版本写进归档。官方建议先恢复到**扩展版本一致**的实例，
   之后再 `ALTER EXTENSION timescaledb UPDATE` 升级。
   `backup-db.sh` 为此给每份备份写了同名 `.meta` 文件（版本 + 超表清单），
   `restore-db.sh` 会自动比对，不一致时直接中止（可用 `--force-version` 强行继续）。

> 补充：官方说明本流程**对压缩超表同样适用，不需要先解压 chunk**。
> 当前 `infra/init-db/` 只用到 3 张超表（`bars` / `ticks` / `strategy_equity`）
> 和 1 条 `ticks` 的 30 天保留策略，没有启用压缩、也没有连续聚合，
> 所以恢复路径比官方描述的通用情形更简单。将来若引入连续聚合或压缩策略，
> 恢复后请额外确认 `timescaledb_information.jobs` 里的后台任务是否都在。

### 本方案的适用边界（诚实说明）

逻辑备份的恢复时间随数据量线性增长，且**没有 PITR（时间点恢复）**能力 ——
只能恢复到某次备份的时刻。当 `bars` / `ticks` 涨到几十 GB 以上时，
应当换成物理备份方案（pgBackRest / WAL 归档），那不在本 wave 范围内。

---

## 二、备份

```bash
# 默认：走 docker exec 到 qb_db 容器，产物落在 ./backups/
scripts/backup-db.sh

# 自定义位置与保留份数
QB_BACKUP_DIR=/mnt/backup QB_BACKUP_KEEP_DAILY=14 scripts/backup-db.sh

# 不用 docker，走本机 pg_dump（需要本机装 postgresql-client）
QB_BACKUP_MODE=local DB_HOST=db.internal scripts/backup-db.sh
```

### 产物结构

```
backups/
├── daily/
│   ├── quantbot_20260814T020000Z.dump   # pg_dump -Fc 自定义格式
│   └── quantbot_20260814T020000Z.meta   # 版本 + 超表清单 + 字节数
└── weekly/                              # 每周日额外留一份
```

### 口令怎么处理（脚本不碰口令）

- **docker 模式（默认，推荐）**：在容器内经 Unix socket 连接，
  官方 postgres 镜像对 local 连接是 `trust`，**根本不需要口令**。
  命令行、环境变量、进程列表里都不会出现口令。
  额外好处：`pg_dump` 版本与服务端**必然一致**，不会有客户端版本过旧的问题。
- **local 模式**：口令从 `PGPASSWORD` 环境变量或 `~/.pgpass` 读取，
  脚本自身不解析、不传递、不写日志。

### 一条可以忽略的告警

每次备份都会看到这段，**属正常现象，不是错误**：

```
pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: hypertable          （以及 chunk / continuous_agg）
pg_dump: hint: You might not be able to restore the dump without using --disable-triggers
pg_dump: hint: Consider using a full dump instead of a --data-only dump to avoid this problem.
```

TimescaleDB 自己的目录表（`hypertable` / `chunk` / `continuous_agg`）之间存在
循环外键，pg_dump 例行提示。注意 hint 的原话是 *"instead of a **--data-only** dump"* ——
而我们做的正是**整库全量 dump**，恰好就是它建议的做法，所以不受影响。
本 wave 已实测完整的备份→恢复往返（5000 行 bars + 2000 行 ticks，
超表与 chunk 结构、保留策略均正确还原），确认该告警无害。

### 成功判据（脚本已强制）

一份备份被判为成功，必须同时满足：

1. `pg_dump` 退出码为 0；
2. 产物字节数 ≥ `QB_BACKUP_MIN_BYTES`（默认 512）；
3. `pg_restore --list` 能解析出归档目录 —— 只查大小挡不住「写了一半的归档」。

任一不满足，脚本**删除半成品并以非零码退出**，且**不会执行任何清理**。

### 保留策略与删除顺序

默认保留 **7 份日备 + 4 份周备**（`QB_BACKUP_KEEP_DAILY` / `QB_BACKUP_KEEP_WEEKLY`）。

> ⚠️ 删除逻辑写在脚本最后，**只在新备份校验通过并原子落盘之后才执行**。
> 顺序反过来（先清理后备份）会在磁盘满、pg_dump 失败的那一天，
> 把仅存的历史备份一起删掉 —— 恰恰是最需要它们的时候。
> 改这段代码前请先想清楚这一点。

### 定时执行

本 wave 不预设调度器。crontab 示例（每天 02:00）：

```cron
0 2 * * * cd /opt/quantbot && QB_BACKUP_DIR=/mnt/backup ./scripts/backup-db.sh >> /var/log/quantbot-backup.log 2>&1
```

⚠️ cron 的环境变量极其精简，`docker` 往往不在 `PATH` 里。用绝对路径，
并且**第一次配好后手动确认日志里有 "备份完成"**，不要配完就走。

---

## 三、恢复

```bash
# 恢复到独立库（恢复演练用这个）
scripts/restore-db.sh backups/daily/quantbot_20260814T020000Z.dump \
  --target quantbot_restore_test

# 自动化场景跳过交互确认
scripts/restore-db.sh <dump> --target quantbot_restore_test --yes

# 目标库已存在时删除重建
scripts/restore-db.sh <dump> --target quantbot_restore_test --drop --yes
```

脚本执行的流程（顺序不可改）：

```
pg_restore --list 预检   →  版本比对（不一致则中止）
  →  CREATE DATABASE     →  CREATE EXTENSION timescaledb
  →  timescaledb_pre_restore()
  →  pg_restore -Fc（无 -j）
  →  timescaledb_post_restore()      ← 用 trap 保证异常路径也会执行
  →  超表清单比对 + ANALYZE
```

> `timescaledb_pre_restore()` 会把 `timescaledb.restoring` 置为 `on` 并停掉后台
> worker。**这个状态是持久化到数据库级别的**，如果中途失败而没有调
> `post_restore()`，该库会一直处于「后台任务不跑、保留策略不生效，但表面看起来
> 一切正常」的状态。脚本用 `trap ... EXIT` 保证任何退出路径都会调用它。

---

## 四、恢复演练（每季度至少一次，务必真的做）

> 一份从没恢复过的备份，与没有备份的区别只在于心理安慰。
> 演练的目的不是证明脚本能跑，而是提前发现「备份其实是坏的」。

```bash
# ── 1. 取最新一份日备 ──────────────────────────────────────────
LATEST="$(ls -1t backups/daily/*.dump | head -1)"
echo "演练对象：${LATEST}"

# ── 2. 恢复到独立库（不碰生产库）───────────────────────────────
./scripts/restore-db.sh "${LATEST}" --target quantbot_drill --drop --yes

# ── 3. 超表结构核对 —— 必须是 3 张且都是超表，不是普通表 ────────
docker exec -i qb_db psql -U quantbot -d quantbot_drill -c \
  "SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables ORDER BY 1;"
# 期望看到 bars / strategy_equity / ticks

# ── 4. 数据抽查 —— 行数与时间范围要和生产对得上 ────────────────
for db in quantbot quantbot_drill; do
  echo "--- $db ---"
  docker exec -i qb_db psql -U quantbot -d "$db" -tAc \
    "SELECT 'bars', count(*), min(time), max(time) FROM bars
     UNION ALL SELECT 'strategies', count(*), NULL, NULL FROM strategies;"
done
# 差异应当只来自备份时刻之后的新增数据。
# 若 drill 库某表是 0 行而生产不是 → 备份有问题，立刻排查，不要等下次。

# ── 5. 保留策略等后台任务是否还在 ──────────────────────────────
docker exec -i qb_db psql -U quantbot -d quantbot_drill -c \
  "SELECT job_id, proc_name, hypertable_name, scheduled FROM timescaledb_information.jobs;"
# ticks 的 30 天保留策略应当在列。

# ── 6. 清理演练库 ──────────────────────────────────────────────
docker exec -i qb_db psql -U quantbot -d postgres -c 'DROP DATABASE quantbot_drill;'
```

### 演练记录

每次演练在下表追加一行。**没有记录的演练等于没做过。**

| 日期 | 备份文件 | 恢复耗时 | 超表 3/3 | 数据抽查 | 执行人 | 备注 |
|---|---|---|---|---|---|---|
| _待填_ | | | | | | |

---

## 五、故障时的真实恢复（生产库已损坏）

1. **先停写**：`docker compose -f infra/docker-compose.prod.yml stop backend nginx`
   —— 恢复期间继续写入会让数据更乱。
2. **不要直接覆盖生产库**。先恢复到 `quantbot_recovered`，抽查确认数据正确。
3. 确认无误后再切换：重命名旧库留档 → 重命名新库为业务库名 → 起服务。

   ```sql
   ALTER DATABASE quantbot RENAME TO quantbot_broken_20260814;
   ALTER DATABASE quantbot_recovered RENAME TO quantbot;
   ```

   > 重命名需要目标库无活动连接，所以第 1 步的停写不能省。
   > 留着 `quantbot_broken_*` 别急着删 —— 事后定位原因要用。
4. 起服务并核对：`/health`、最近一根 K 线时间、持仓与券商对账。
