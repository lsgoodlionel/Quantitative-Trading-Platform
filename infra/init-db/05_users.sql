-- 用户表（V3 · J3）
--
-- 取代 `app/api/v1/endpoints/auth.py` 里硬编码的 `_BUILTIN_USERS`。
--
-- ⚠️ 内置账户是**首次启动种子**而非被删除的东西：表为空时由
-- `app/data/storage/users.py::seed_builtin_users()` 插入 admin / trader / viewer
-- 三个默认账户（沿用源码里那三个 bcrypt hash），并在日志里明确警告默认密码必须改。
-- 直接删掉内置账户会让零配置启动失效，那是现有部署依赖的行为。
--
-- `role` 取值必须与 `app/core/rbac.py` 的 `Role` 枚举一致（admin / trader / viewer）。
-- CHECK 约束是最后一道闸：一个 role 写错的用户会以「谁都不是」的身份存在。
--
-- 本期只做「管理员维护账户」，不做用户自助注册（无邮箱验证 / 限流 / 防滥用）。
--
-- 对已存在的部署，`PostgresUserStore.ensure_schema()` 会幂等地补建同样的结构。

CREATE TABLE IF NOT EXISTS users (
    id            UUID          PRIMARY KEY,
    username      VARCHAR(64)   NOT NULL UNIQUE,
    email         VARCHAR(255)  NOT NULL DEFAULT '',
    hashed_pw     VARCHAR(255)  NOT NULL,
    role          VARCHAR(20)   NOT NULL DEFAULT 'viewer'
                  CHECK (role IN ('admin', 'trader', 'viewer')),
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active, username);
