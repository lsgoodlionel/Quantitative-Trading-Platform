#!/usr/bin/env bash
#
# QuantBot 数据库恢复 — TimescaleDB 逻辑恢复（pg_restore 自定义格式）
#
# 用法:
#   scripts/restore-db.sh backups/daily/quantbot_20260814T020000Z.dump --target quantbot_restore_test
#   scripts/restore-db.sh <dump> --target <db> --yes        # 跳过交互确认（用于自动化演练）
#   scripts/restore-db.sh <dump> --target <db> --drop       # 目标库已存在时先删除重建
#
# TimescaleDB 专有流程（**不能省，也不能改顺序**）:
#   CREATE DATABASE  →  CREATE EXTENSION timescaledb
#   →  SELECT timescaledb_pre_restore()      -- 关闭 TimescaleDB 钩子与后台 worker
#   →  pg_restore -Fc（**禁止 -j**）
#   →  SELECT timescaledb_post_restore()     -- 重新启用
#
# 超表**不需要**手工 create_hypertable() 重建：超表元数据与 chunk 结构
# 都在 dump 里，恢复后自动还原。详见 scripts/README.md 与其中的官方来源。
#
set -euo pipefail

QB_BACKUP_MODE="${QB_BACKUP_MODE:-docker}"
QB_DB_CONTAINER="${QB_DB_CONTAINER:-qb_db}"
DB_USER="${DB_USER:-quantbot}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

DUMP_FILE=""
TARGET_DB=""
ASSUME_YES=0
DROP_EXISTING=0
FORCE_VERSION=0

log()  { printf '[restore-db][%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
fail() { printf '[restore-db][ERROR] %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

# ── 参数解析 ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)         TARGET_DB="${2:-}"; shift 2 ;;
    --yes|-y)         ASSUME_YES=1; shift ;;
    --drop)           DROP_EXISTING=1; shift ;;
    --force-version)  FORCE_VERSION=1; shift ;;
    -h|--help)        usage 0 ;;
    -*)               fail "未知参数：$1" ;;
    *)                DUMP_FILE="$1"; shift ;;
  esac
done

[[ -n "${DUMP_FILE}" ]] || usage 1
[[ -n "${TARGET_DB}" ]] || fail "必须用 --target 指定目标数据库名"
[[ -f "${DUMP_FILE}" ]] || fail "备份文件不存在：${DUMP_FILE}"

# 恢复演练请用独立库名。直接盖生产库风险极高，这里给出明确提示。
if [[ "${TARGET_DB}" == "${DB_NAME:-quantbot}" && "${ASSUME_YES}" -eq 0 ]]; then
  log "警告：目标库 '${TARGET_DB}' 与当前业务库同名，这是覆盖生产数据的操作。"
  log "      恢复演练应当恢复到独立库名（如 quantbot_restore_test）后再校验。"
fi

case "${QB_BACKUP_MODE}" in
  docker)
    command -v docker >/dev/null 2>&1 || fail "未找到 docker"
    docker inspect -f '{{.State.Running}}' "${QB_DB_CONTAINER}" 2>/dev/null | grep -q '^true$' \
      || fail "容器 ${QB_DB_CONTAINER} 未在运行"
    ;;
  local)
    command -v pg_restore >/dev/null 2>&1 || fail "未找到 pg_restore"
    command -v psql       >/dev/null 2>&1 || fail "未找到 psql"
    ;;
  *) fail "QB_BACKUP_MODE 只能是 docker 或 local" ;;
esac

# ── 执行器 ───────────────────────────────────────────────────────
run_pg() {
  local bin="$1"; shift
  if [[ "${QB_BACKUP_MODE}" == "docker" ]]; then
    docker exec -i "${QB_DB_CONTAINER}" "${bin}" -U "${DB_USER}" "$@"
  else
    "${bin}" -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "$@"
  fi
}

psql_maint() { run_pg psql -d postgres -v ON_ERROR_STOP=1 -tAc "$1"; }
psql_target(){ run_pg psql -d "${TARGET_DB}" -v ON_ERROR_STOP=1 -tAc "$1"; }

# ── 0. 归档可读性预检 ────────────────────────────────────────────
log "预检备份归档 ${DUMP_FILE}"
if ! run_pg pg_restore --list < "${DUMP_FILE}" > /dev/null 2>&1; then
  fail "备份归档无法解析，恢复中止（文件损坏或不是 pg_dump -Fc 自定义格式）"
fi

# ── 1. 版本比对 ──────────────────────────────────────────────────
# pg_dump 不会把扩展版本写进归档，所以备份时我们额外存了 .meta。
# 版本不一致时的报错通常不指向真实原因，宁可在这里先拦住。
META_FILE="${DUMP_FILE%.dump}.meta"
if [[ -f "${META_FILE}" ]]; then
  SRC_TS_VER="$(grep '^timescaledb_version=' "${META_FILE}" | cut -d= -f2- || true)"
  SRC_PG_VER="$(grep '^postgres_version='    "${META_FILE}" | cut -d= -f2- || true)"
  CUR_TS_VER="$(psql_maint "SELECT default_version FROM pg_available_extensions WHERE name='timescaledb';" | tr -d '\r' || true)"

  log "备份来源：PostgreSQL=${SRC_PG_VER:-?} TimescaleDB=${SRC_TS_VER:-?}"
  log "目标实例：TimescaleDB 可用版本=${CUR_TS_VER:-?}"

  if [[ -n "${SRC_TS_VER}" && -n "${CUR_TS_VER}" && "${SRC_TS_VER}" != "${CUR_TS_VER}" ]]; then
    if [[ "${FORCE_VERSION}" -eq 1 ]]; then
      log "警告：TimescaleDB 版本不一致（${SRC_TS_VER} → ${CUR_TS_VER}），已用 --force-version 跳过检查"
    else
      fail "TimescaleDB 版本不一致：备份为 ${SRC_TS_VER}，目标实例为 ${CUR_TS_VER}。
      官方建议先恢复到**版本相同**的实例，再执行 ALTER EXTENSION timescaledb UPDATE 升级。
      确认要继续请加 --force-version。"
    fi
  fi
else
  log "警告：未找到 ${META_FILE}，无法比对版本。若恢复失败，优先排查版本不一致。"
fi

# ── 2. 确认 ──────────────────────────────────────────────────────
if [[ "${ASSUME_YES}" -ne 1 ]]; then
  DROP_HINT=""
  [[ "${DROP_EXISTING}" -eq 1 ]] && DROP_HINT="（并会先 DROP 已存在的同名库）"
  printf '将把 %s 恢复到数据库 "%s"%s。确认请输入 yes: ' "${DUMP_FILE}" "${TARGET_DB}" "${DROP_HINT}" >&2
  read -r CONFIRM
  [[ "${CONFIRM}" == "yes" ]] || fail "已取消"
fi

# ── 3. 建库 ──────────────────────────────────────────────────────
EXISTS="$(psql_maint "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}';" | tr -d '[:space:]')"

if [[ "${EXISTS}" == "1" ]]; then
  if [[ "${DROP_EXISTING}" -eq 1 ]]; then
    log "目标库已存在，按 --drop 删除重建"
    psql_maint "DROP DATABASE \"${TARGET_DB}\";" >/dev/null
    EXISTS=""
  else
    fail "目标库 '${TARGET_DB}' 已存在。请换个库名，或加 --drop 明确表示要删除重建。"
  fi
fi

if [[ "${EXISTS}" != "1" ]]; then
  log "创建数据库 ${TARGET_DB}"
  psql_maint "CREATE DATABASE \"${TARGET_DB}\";" >/dev/null
fi

# ── 4. TimescaleDB 恢复前置 ──────────────────────────────────────
log "CREATE EXTENSION timescaledb + timescaledb_pre_restore()"
psql_target "CREATE EXTENSION IF NOT EXISTS timescaledb;" >/dev/null
psql_target "SELECT timescaledb_pre_restore();" >/dev/null

# pre_restore 之后无论成败都必须调 post_restore，否则该库会一直停在
# timescaledb.restoring = on 的状态：后台 worker 不工作、保留策略不执行，
# 而且**表面上一切正常**。这里用 trap 保证一定会执行。
post_restore() {
  log "timescaledb_post_restore()"
  psql_target "SELECT timescaledb_post_restore();" >/dev/null \
    || log "严重：post_restore 执行失败！请手动在 ${TARGET_DB} 上执行 SELECT timescaledb_post_restore();"
}
trap post_restore EXIT

# ── 5. 恢复 ──────────────────────────────────────────────────────
# ⚠️ 绝对不要加 -j：TimescaleDB 官方明确说明并行恢复无法正确还原其目录表。
log "pg_restore 开始（单线程，禁用 -j）"
RESTORE_RC=0
run_pg pg_restore -Fc --no-tablespaces -d "${TARGET_DB}" < "${DUMP_FILE}" || RESTORE_RC=$?

# pg_restore 对「扩展已存在」等情形会返回非零但实际无害，交由后续校验判定
if [[ "${RESTORE_RC}" -ne 0 ]]; then
  log "警告：pg_restore 退出码 ${RESTORE_RC}（自定义格式恢复常见于已存在对象的告警）"
  log "      不要就此认为成功 —— 下面的超表校验才是判据。"
fi

trap - EXIT
post_restore

# ── 6. 恢复后校验（这一步决定成败）──────────────────────────────
log "校验超表还原情况"
HT_LIST="$(psql_target "SELECT string_agg(hypertable_name, ',' ORDER BY hypertable_name) FROM timescaledb_information.hypertables;" | tr -d '\r' || true)"
HT_COUNT="$(psql_target "SELECT count(*) FROM timescaledb_information.hypertables;" | tr -d '[:space:]' || echo 0)"

log "目标库超表（${HT_COUNT} 张）：${HT_LIST:-<无>}"

if [[ -f "${META_FILE}" ]]; then
  SRC_HT="$(grep '^hypertables=' "${META_FILE}" | cut -d= -f2- || true)"
  if [[ -n "${SRC_HT}" && "${SRC_HT}" != "unknown" ]]; then
    if [[ "${SRC_HT}" == "${HT_LIST}" ]]; then
      log "超表清单与备份来源一致 ✓"
    else
      fail "超表清单不一致！备份为 [${SRC_HT}]，恢复后为 [${HT_LIST:-<无>}]。
      这通常意味着 dump 时用了 -t/--schema 之类的选择性导出，导致 TimescaleDB
      目录信息缺失、超表退化成普通表。请勿把这份备份当作可用备份。"
    fi
  fi
fi

if [[ "${HT_COUNT}" == "0" ]]; then
  fail "目标库一张超表都没有，恢复结果不可信"
fi

# 恢复后统计信息是空的，不做 ANALYZE 的话第一批查询会走很差的执行计划
log "ANALYZE（重建统计信息）"
run_pg psql -d "${TARGET_DB}" -q -c "ANALYZE;" >/dev/null 2>&1 || log "警告：ANALYZE 失败，可稍后手动执行"

log "恢复完成：${DUMP_FILE} → ${TARGET_DB}"
log "请按 scripts/README.md 的「恢复演练」继续做数据抽查，恢复完成 ≠ 数据正确。"
exit 0
