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

-- ── 与早期部署对齐（幂等）─────────────────────────────────────────────────
--
-- `02_init_business.sql` 曾建过一版**没有 username 列**的 users。上面的
-- `CREATE TABLE IF NOT EXISTS` 对那种库是空操作，于是下面的索引会直接报
-- 「column "username" does not exist」：全新部署 → 容器初始化失败退出；
-- 已有数据卷的部署 → `PostgresUserStore.ensure_schema()` 跑同一份 DDL，
-- 在用户仓储第一次被使用时炸。02 已改成同一份 schema，这里补上存量库的迁移。
--
-- 全部写成幂等：重复执行不报错，也不覆盖任何已有数据。

ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64);

-- 存量行没有 username：先取邮箱前缀，取不到就用 id 前 8 位兜底。
UPDATE users SET username = split_part(email, '@', 1)
 WHERE username IS NULL AND email <> '';
UPDATE users SET username = 'user_' || left(id::text, 8)
 WHERE username IS NULL OR username = '';

-- 邮箱前缀可能撞车（a@x.com 与 a@y.com）。接 id 前 8 位拆开，
-- 只改重复组里除最早创建的那一个之外的行 —— 保住老账户的用户名不变。
UPDATE users u
   SET username = u.username || '_' || left(u.id::text, 8)
  FROM (
        SELECT id, row_number() OVER (PARTITION BY username ORDER BY created_at, id) AS rn
          FROM users
       ) d
 WHERE d.id = u.id AND d.rn > 1;

ALTER TABLE users ALTER COLUMN username SET NOT NULL;

-- 02 早期版本给 email 加过 UNIQUE，而现在允许多个账户留空邮箱 ——
-- 不摘掉的话第二个空邮箱账户就建不出来。约束名是 Postgres 的确定性默认名。
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;
ALTER TABLE users ALTER COLUMN email SET DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active, username);
