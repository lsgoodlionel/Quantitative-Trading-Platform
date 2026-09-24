-- 审计日志持久层（V3 · J3）
--
-- 审计此前只写 Redis stream（`audit:log`，maxlen 10000 近似裁剪）。
-- Redis 是**快速查询层**：容量有上限、重启可能丢、超过 1 万条就被裁掉。
-- 审计的意义是「出事后能查」，所以持久层落这张表，**无容量上限**。
--
-- ⚠️ **审计记录不可变**：应用层只做 INSERT 与 SELECT。
-- 没有 UPDATE/DELETE 端点，也刻意不为「清理」提供接口 ——
-- 一个能被删除的审计日志，在真正需要它的那一刻就是空的。
-- 确需归档/清理请由 DBA 走带审批的运维流程。
--
-- 对已存在的部署，`PostgresAuditLogStore.ensure_schema()` 会幂等地补建同样的结构。

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL     PRIMARY KEY,
    ts          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    action      VARCHAR(64)   NOT NULL,
    actor       VARCHAR(255)  NOT NULL DEFAULT 'system',
    detail      JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_ts     ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor  ON audit_log(actor, ts DESC);
