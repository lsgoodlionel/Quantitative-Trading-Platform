#!/usr/bin/env bash
#
# QuantBot 数据库备份 — TimescaleDB 逻辑备份（pg_dump 自定义格式）
#
# 用法:
#   scripts/backup-db.sh                    # 按默认配置备份
#   QB_BACKUP_DIR=/mnt/backup scripts/backup-db.sh
#   QB_BACKUP_MODE=local scripts/backup-db.sh
#
# 设计要点（改动前请先读 scripts/README.md）:
#   1. 整库 dump，**绝不加 -t / --schema** —— 会丢失 TimescaleDB 目录信息，
#      恢复时超表变成普通表。
#   2. 产物必须通过「非空 + pg_restore --list 可解析」双重校验才算成功。
#   3. 保留策略的删除**只在新备份校验通过之后**执行。
#   4. 全程不在命令行出现数据库口令。
#
set -euo pipefail

# ── 配置（全部可用环境变量覆盖）──────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

QB_BACKUP_MODE="${QB_BACKUP_MODE:-docker}"          # docker | local
QB_DB_CONTAINER="${QB_DB_CONTAINER:-qb_db}"
QB_BACKUP_DIR="${QB_BACKUP_DIR:-${REPO_ROOT}/backups}"
QB_BACKUP_KEEP_DAILY="${QB_BACKUP_KEEP_DAILY:-7}"
QB_BACKUP_KEEP_WEEKLY="${QB_BACKUP_KEEP_WEEKLY:-4}"

# 自定义格式的空库 dump 也有 1KB 上下；低于这个值一定是出错了。
QB_BACKUP_MIN_BYTES="${QB_BACKUP_MIN_BYTES:-512}"

DB_USER="${DB_USER:-quantbot}"
DB_NAME="${DB_NAME:-quantbot}"
DB_HOST="${DB_HOST:-localhost}"                     # 仅 local 模式使用
DB_PORT="${DB_PORT:-5432}"                          # 仅 local 模式使用

DAILY_DIR="${QB_BACKUP_DIR}/daily"
WEEKLY_DIR="${QB_BACKUP_DIR}/weekly"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASENAME="quantbot_${TS}"

log()  { printf '[backup-db][%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
fail() { printf '[backup-db][ERROR] %s\n' "$*" >&2; exit 1; }

# ── 前置检查 ─────────────────────────────────────────────────────
case "${QB_BACKUP_MODE}" in
  docker)
    command -v docker >/dev/null 2>&1 || fail "未找到 docker（可用 QB_BACKUP_MODE=local 走本机 pg_dump）"
    docker inspect -f '{{.State.Running}}' "${QB_DB_CONTAINER}" 2>/dev/null | grep -q '^true$' \
      || fail "容器 ${QB_DB_CONTAINER} 未在运行"
    ;;
  local)
    command -v pg_dump    >/dev/null 2>&1 || fail "未找到 pg_dump"
    command -v pg_restore >/dev/null 2>&1 || fail "未找到 pg_restore（校验产物需要）"
    command -v psql       >/dev/null 2>&1 || fail "未找到 psql（记录版本信息需要）"
    ;;
  *)
    fail "QB_BACKUP_MODE 只能是 docker 或 local，当前为 '${QB_BACKUP_MODE}'"
    ;;
esac

mkdir -p "${DAILY_DIR}" "${WEEKLY_DIR}" || fail "无法创建备份目录 ${QB_BACKUP_DIR}"

# ── 执行器：屏蔽 docker / local 两种模式的差异 ───────────────────
# 口令处理：
#   docker 模式 —— 经容器内 Unix socket 连接，官方镜像的 pg_hba 对 local
#                  连接是 trust，**根本不需要口令**，因此命令行、环境变量
#                  里都不会出现口令。这是首选模式。
#   local  模式 —— 从 PGPASSWORD 环境变量或 ~/.pgpass 读取，脚本自身不碰。
run_pg() {
  local bin="$1"; shift
  if [[ "${QB_BACKUP_MODE}" == "docker" ]]; then
    docker exec -i "${QB_DB_CONTAINER}" "${bin}" -U "${DB_USER}" "$@"
  else
    "${bin}" -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "$@"
  fi
}

# ── 1. dump ──────────────────────────────────────────────────────
TMP_FILE="${DAILY_DIR}/.${BASENAME}.dump.partial"
DUMP_FILE="${DAILY_DIR}/${BASENAME}.dump"
META_FILE="${DAILY_DIR}/${BASENAME}.meta"

# 任何中途退出都要清掉半成品，避免下次误当成有效备份。
# .meta 也要清：留下一个没有对应 .dump 的 .meta 会让人误以为备份存在过。
cleanup_partial() {
  rm -f "${TMP_FILE}" "${META_FILE}"
  return 0
}
trap cleanup_partial EXIT

log "开始备份 ${DB_NAME}（模式=${QB_BACKUP_MODE}）→ ${DUMP_FILE}"

# -Fc  自定义格式：TimescaleDB 官方文档指定的格式，恢复时配合 pg_restore -Fc
# --no-tablespaces  恢复到不同机器时避免因表空间路径缺失而失败
# 刻意不加 -t / --schema：见文件头说明
if ! run_pg pg_dump -d "${DB_NAME}" -Fc --no-tablespaces > "${TMP_FILE}"; then
  fail "pg_dump 失败，未生成有效备份"
fi

# ── 2. 校验产物（非空 + 可解析）──────────────────────────────────
[[ -f "${TMP_FILE}" ]] || fail "备份文件未生成：${TMP_FILE}"

if command -v stat >/dev/null 2>&1; then
  SIZE="$(stat -f%z "${TMP_FILE}" 2>/dev/null || stat -c%s "${TMP_FILE}" 2>/dev/null || echo 0)"
else
  SIZE="$(wc -c < "${TMP_FILE}" | tr -d ' ')"
fi

if [[ "${SIZE}" -lt "${QB_BACKUP_MIN_BYTES}" ]]; then
  fail "备份文件过小（${SIZE} 字节 < ${QB_BACKUP_MIN_BYTES}），判定为失败"
fi

# 只查大小挡不住「写了一半的归档」。用 pg_restore --list 解析目录，
# 能读出条目才说明归档结构完整。这一步不会写任何数据库。
if ! run_pg pg_restore --list < "${TMP_FILE}" > /dev/null 2>&1; then
  fail "备份文件无法被 pg_restore 解析，归档已损坏（${SIZE} 字节）"
fi

log "校验通过：${SIZE} 字节，归档结构完整"

# ── 3. 记录版本与超表清单（恢复时要用）──────────────────────────
# TimescaleDB 官方明确要求「pg_dump 定期备份时务必记录 PostgreSQL 与
# TimescaleDB 的版本」—— pg_dump 不会把扩展版本写进归档，版本不一致时
# 恢复会失败，且报错信息通常不指向真正的原因。
{
  echo "backup_time_utc=${TS}"
  echo "db_name=${DB_NAME}"
  echo "db_user=${DB_USER}"
  echo "backup_mode=${QB_BACKUP_MODE}"
  echo "dump_bytes=${SIZE}"
  echo "pg_dump_format=custom"
} > "${META_FILE}"

meta_query() {
  run_pg psql -d "${DB_NAME}" -tAc "$1" 2>/dev/null | tr -d '\r' || true
}

PG_VERSION="$(meta_query "SHOW server_version;")"
TS_VERSION="$(meta_query "SELECT extversion FROM pg_extension WHERE extname='timescaledb';")"
HYPERTABLES="$(meta_query "SELECT string_agg(hypertable_name, ',' ORDER BY hypertable_name) FROM timescaledb_information.hypertables;")"

{
  echo "postgres_version=${PG_VERSION:-unknown}"
  echo "timescaledb_version=${TS_VERSION:-unknown}"
  echo "hypertables=${HYPERTABLES:-unknown}"
} >> "${META_FILE}"

log "版本信息：PostgreSQL=${PG_VERSION:-unknown} TimescaleDB=${TS_VERSION:-unknown}"
log "超表：${HYPERTABLES:-unknown}"

if [[ -z "${TS_VERSION}" ]]; then
  log "警告：未读到 timescaledb 扩展版本。若目标库本就没装 TimescaleDB 可忽略，"
  log "      否则说明连接的库不对，请在恢复前查清楚。"
fi

# ── 4. 落盘（原子）──────────────────────────────────────────────
mv "${TMP_FILE}" "${DUMP_FILE}" || fail "备份文件落盘失败"
trap - EXIT
log "备份完成：${DUMP_FILE}"

# ── 5. 周备（每周日额外留一份）─────────────────────────────────
if [[ "$(date -u +%u)" == "7" ]]; then
  cp "${DUMP_FILE}" "${WEEKLY_DIR}/${BASENAME}.dump" \
    && cp "${META_FILE}" "${WEEKLY_DIR}/${BASENAME}.meta" \
    && log "已归入周备：${WEEKLY_DIR}/${BASENAME}.dump"
fi

# ── 6. 保留策略 ─────────────────────────────────────────────────
# ⚠️ 顺序是刻意的：清理**只在新备份已校验通过并落盘之后**才执行。
#    反过来（先清后备）会在磁盘满、pg_dump 失败的那一天，
#    把仅存的历史备份一起删掉 —— 恰恰是最需要它们的时候。
# 注意：这里用 while-read 而非 mapfile，以兼容 macOS 自带的 bash 3.2。
prune_dir() {
  local dir="$1" keep="$2" label="$3"
  local idx=0 count=0 victim

  # 按文件名倒序（时间戳是 ISO 紧凑格式，字典序 == 时间序，最新在前）
  count="$(find "${dir}" -maxdepth 1 -type f -name 'quantbot_*.dump' | wc -l | tr -d ' ')"
  if [[ "${count}" -le "${keep}" ]]; then
    log "${label}：现有 ${count} 份，保留上限 ${keep}，无需清理"
    return 0
  fi

  while IFS= read -r victim; do
    idx=$(( idx + 1 ))
    [[ "${idx}" -le "${keep}" ]] && continue
    rm -f "${victim}" "${victim%.dump}.meta"
    log "${label}：已删除过期备份 $(basename "${victim}")"
  done < <(find "${dir}" -maxdepth 1 -type f -name 'quantbot_*.dump' | sort -r)
}

prune_dir "${DAILY_DIR}"  "${QB_BACKUP_KEEP_DAILY}"  "日备"
prune_dir "${WEEKLY_DIR}" "${QB_BACKUP_KEEP_WEEKLY}" "周备"

log "全部完成。恢复方式见 scripts/README.md（并请定期做恢复演练）"
exit 0
