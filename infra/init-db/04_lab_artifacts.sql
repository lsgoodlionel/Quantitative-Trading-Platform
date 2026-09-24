-- 投研产物库元数据表（V4 · M4）
--
-- 元数据落库、内容落文件系统（默认 backend/data/lab，可用 QUANTBOT_LAB_ROOT 覆盖）。
-- 模型二进制**不进数据库**。
--
-- 容量策略：无上限、仅手动删除 —— 与 backtest_history 一致，
-- 刻意不做实验记录器那种滚动淘汰。
--
-- 对已存在的部署，`PostgresArtifactMetaStore.ensure_schema()` 会幂等地补建同样的结构。

CREATE TABLE IF NOT EXISTS lab_artifacts (
    artifact_id UUID          PRIMARY KEY,
    kind        VARCHAR(20)   NOT NULL,   -- dataset | model | signal
    name        VARCHAR(300)  NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    size_bytes  BIGINT        NOT NULL DEFAULT 0,
    tags        JSONB         NOT NULL DEFAULT '{}',
    checksum    VARCHAR(64)   NOT NULL DEFAULT ''   -- 内容 sha256，加载前必校验
);

CREATE INDEX IF NOT EXISTS idx_lab_artifacts_created ON lab_artifacts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lab_artifacts_kind    ON lab_artifacts(kind, created_at DESC);
