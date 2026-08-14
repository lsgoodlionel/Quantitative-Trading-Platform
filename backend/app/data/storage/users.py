"""
用户仓储（V3 · J3）

把 `app/api/v1/endpoints/auth.py` 的 `_BUILTIN_USERS`（三个账户、密码 hash 写在源码里）
搬进 Postgres。建表见 `infra/init-db/05_users.sql`；对已存在的部署，
仓储首次使用时执行一次幂等的 `ensure_schema()`（项目未引入 alembic 迁移流程）。

## 三条硬约束

1. **内置账户是「首次启动种子」，不是被删掉的东西。**
   `seed_builtin_users()` 只在**空表**时插入 admin / trader / viewer，
   沿用源码里那三个 bcrypt hash，并在日志里 WARNING 提醒默认密码必须改。
   直接删掉内置账户会让零配置启动失效 —— 那是现有部署依赖的行为。
   非空表一律不动：不覆盖、不「补齐缺失的账户」，否则管理员删掉的账号会自己长回来。

2. **密码哈希继续用 bcrypt，不换算法。**
   直接用 `bcrypt` 包而非 `passlib`：仓库里虽然装着 passlib 1.7.4，但它读
   `bcrypt.__about__.__version__`，而依赖锁定的 bcrypt 5.0.0 已移除该属性，
   `passlib.hash.bcrypt` 会直接报错。`auth.py` 现有的校验就是直接调 bcrypt 的，
   这里保持一致 —— **算法没变，变的只是调用哪个封装**。

3. **`role` 必须落在 `app/core/rbac.py` 的 `Role` 枚举内。**
   落库前用 `normalize_role_strict()` 校验并拒绝非法值（不是静默降级）：
   一个 role 写错的用户会以「谁都不是」的身份存在，比拒绝创建危险得多。
   数据库侧另有 CHECK 约束兜底。

⚠️ 本模块**不提供**自助注册相关能力（邮箱验证 / 找回密码 / 限流）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Role

logger = logging.getLogger(__name__)

# 列表接口单页上限
MAX_PAGE_SIZE = 200

# 用户名长度约束（与 DDL 的 VARCHAR(64) 对齐）
MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 64

# 密码最短长度。bcrypt 只吃前 72 字节，超长部分静默截断，因此也设上限。
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72

DEFAULT_PASSWORD_WARNING = (
    "已播种内置账户 admin / trader / viewer（默认密码 admin123 / trader123 / viewer123）。"
    "⚠️ 这三个密码是公开写在源码与文档里的，暴露在公网前必须立即修改，"
    "改密请走 PUT /api/v1/users/{id}。"
)


class UserError(Exception):
    """用户仓储的基类异常。"""


class InvalidRoleError(UserError):
    """role 不在 `Role` 枚举内。"""


class InvalidUserInputError(UserError):
    """用户名 / 密码 / 邮箱不合法。"""


class DuplicateUsernameError(UserError):
    """用户名已存在。"""


class UserNotFoundError(UserError):
    """用户不存在。"""


@dataclass(frozen=True)
class UserRecord:
    """一条用户记录（不可变）。`hashed_pw` 绝不出现在任何 API 响应里。"""

    id: str
    username: str
    email: str
    hashed_pw: str
    role: Role
    is_active: bool = True
    created_at: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        """对外投影：**不含** hashed_pw。响应模型只应消费这个。"""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role.value,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }


class UserStore(Protocol):
    """用户仓储接口（便于测试替换为内存实现）。"""

    async def count(self) -> int: ...

    async def get_by_username(self, username: str) -> UserRecord | None: ...

    async def get(self, user_id: str) -> UserRecord | None: ...

    async def list(self, limit: int, offset: int) -> tuple[list[UserRecord], int]: ...

    async def create(self, record: UserRecord) -> UserRecord: ...

    async def update(self, record: UserRecord) -> UserRecord: ...

    async def delete(self, user_id: str) -> bool: ...


# ── 校验与构造 ────────────────────────────────────────────────────

def normalize_role_strict(raw: str | Role | None) -> Role:
    """
    严格校验 role：非法值**抛错**而不是降级。

    与 `rbac.normalize_role()` 的 fail-safe 降级刻意不同 —— 那是「读一个已存在的
    身份」时宁可少给权限；这里是「写一个新身份」，静默把 `Role.ADMN` 存成 viewer
    会造出一个管理员以为自己建了 admin、实际谁都不是的账户。
    """
    if isinstance(raw, Role):
        return raw
    try:
        return Role(str(raw or "").strip().lower())
    except ValueError as exc:
        allowed = ", ".join(r.value for r in Role)
        raise InvalidRoleError(f"非法角色 {raw!r}，允许值：{allowed}") from exc


def hash_password(plain: str) -> str:
    """bcrypt 哈希。与 `auth.py::_verify_password` 使用同一算法与库。"""
    validate_password(plain)
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def validate_password(plain: str) -> None:
    if not plain or len(plain) < MIN_PASSWORD_LEN:
        raise InvalidUserInputError(f"密码至少 {MIN_PASSWORD_LEN} 位")
    if len(plain.encode()) > MAX_PASSWORD_BYTES:
        # bcrypt 静默截断到 72 字节：不拦的话「改了长密码却仍能用旧前缀登录」
        raise InvalidUserInputError(f"密码不得超过 {MAX_PASSWORD_BYTES} 字节（bcrypt 上限）")


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not (MIN_USERNAME_LEN <= len(name) <= MAX_USERNAME_LEN):
        raise InvalidUserInputError(
            f"用户名长度须在 {MIN_USERNAME_LEN}~{MAX_USERNAME_LEN} 之间"
        )
    if not all(c.isalnum() or c in "._-" for c in name):
        raise InvalidUserInputError("用户名只允许字母、数字与 . _ -")
    return name


def build_user(
    username: str,
    password: str,
    role: str | Role,
    *,
    email: str = "",
    is_active: bool = True,
    user_id: str | None = None,
) -> UserRecord:
    """构造一条待落库的用户记录（校验 + 哈希，尚未写库）。"""
    return UserRecord(
        id=user_id or str(uuid.uuid4()),
        username=validate_username(username),
        email=(email or "").strip(),
        hashed_pw=hash_password(password),
        role=normalize_role_strict(role),
        is_active=is_active,
        created_at=datetime.now(UTC).isoformat(),
    )


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。哈希损坏时返回 False 而非抛错（登录路径不该因脏数据 500）。"""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        logger.error("用户密码哈希无法解析，登录被拒")
        return False


# ── 内置账户种子 ──────────────────────────────────────────────────

# 沿用 auth.py `_BUILTIN_USERS` 的 id / email / role / hash，逐字不改。
# 保留 hash 而非明文，是为了让种子与现有部署的登录行为完全一致。
BUILTIN_SEED: tuple[UserRecord, ...] = (
    UserRecord(
        id="00000000-0000-0000-0000-000000000001",
        username="admin",
        email="admin@quantbot.local",
        hashed_pw="$2b$12$0kMLEk./lr7l8hLBc4MIaeZTrJwp03XI3Zjw2LmiBjp5Of.KqvwWC",
        role=Role.ADMIN,
    ),
    UserRecord(
        id="00000000-0000-0000-0000-000000000002",
        username="trader",
        email="trader@quantbot.local",
        hashed_pw="$2b$12$c1/sv7s00LizRZTO1rrO/eDRIYo3YReJ//Cdk8cNBNZfIPENRYvDS",
        role=Role.TRADER,
    ),
    UserRecord(
        id="00000000-0000-0000-0000-000000000003",
        username="viewer",
        email="viewer@quantbot.local",
        hashed_pw="$2b$12$Hr02dEzvB8GmnjWP2eR7yektufn5V3wmgrV6pjgMClFSMM1qMZgXK",
        role=Role.VIEWER,
    ),
)


@dataclass(frozen=True)
class SeedResult:
    """播种结果。`warning` 非空即代表这次真的写入了默认密码账户。"""

    seeded: tuple[str, ...]
    skipped: bool
    warning: str = ""


async def seed_builtin_users(store: UserStore) -> SeedResult:
    """
    空表时播种三个内置账户，并 WARNING 提醒默认密码。

    表非空一律跳过：不覆盖、不补齐 —— 管理员删掉的账号不该自己长回来。
    """
    existing = await store.count()
    if existing > 0:
        logger.info("users 表已有 %d 条记录，跳过内置账户播种", existing)
        return SeedResult(seeded=(), skipped=True)

    created: list[str] = []
    for record in BUILTIN_SEED:
        # created_at 在构造期是空串，落库时补当前时间
        await store.create(replace(record, created_at=datetime.now(UTC).isoformat()))
        created.append(record.username)

    logger.warning(DEFAULT_PASSWORD_WARNING)
    return SeedResult(
        seeded=tuple(created), skipped=False, warning=DEFAULT_PASSWORD_WARNING
    )


# ── Postgres 实现 ─────────────────────────────────────────────────

_DDL = """
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
ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64);
UPDATE users SET username = split_part(email, '@', 1)
 WHERE username IS NULL AND email <> '';
UPDATE users SET username = 'user_' || left(id::text, 8)
 WHERE username IS NULL OR username = '';
UPDATE users u
   SET username = u.username || '_' || left(u.id::text, 8)
  FROM (
        SELECT id, row_number() OVER (PARTITION BY username ORDER BY created_at, id) AS rn
          FROM users
       ) d
 WHERE d.id = u.id AND d.rn > 1;
ALTER TABLE users ALTER COLUMN username SET NOT NULL;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;
ALTER TABLE users ALTER COLUMN email SET DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active, username)
"""
"""与 `infra/init-db/05_users.sql` 逐句对应，两处必须同步改。

`CREATE TABLE IF NOT EXISTS` 之后那一串 ALTER/UPDATE 不是冗余：
`02_init_business.sql` 曾建过一版**没有 username 列**的 users，对那种库
建表语句是空操作，紧接着的索引会直接报「column does not exist」——
全新部署时容器初始化失败退出，已有数据卷的部署则在这里第一次跑 DDL 时炸。
全部写成幂等，重复执行不报错也不覆盖数据。
"""

_COLUMNS = "id, username, email, hashed_pw, role, is_active, created_at"

_INSERT_SQL = text(f"""
    INSERT INTO users ({_COLUMNS})
    VALUES (CAST(:id AS UUID), :username, :email, :hashed_pw, :role, :is_active, :created_at)
""")

_UPDATE_SQL = text("""
    UPDATE users
       SET username = :username, email = :email, hashed_pw = :hashed_pw,
           role = :role, is_active = :is_active
     WHERE id = CAST(:id AS UUID)
""")

_DELETE_SQL = text("DELETE FROM users WHERE id = CAST(:id AS UUID)")


def _row_to_record(row: Any) -> UserRecord:
    created = row.created_at
    return UserRecord(
        id=str(row.id),
        username=row.username,
        email=row.email or "",
        hashed_pw=row.hashed_pw,
        # 读路径用宽松的 Role()，非法值在这里已被 DDL CHECK 挡住；
        # 万一遇到脏数据宁可让它显式炸，也好过悄悄变成一个别的角色。
        role=Role(row.role),
        is_active=bool(row.is_active),
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
    )


class PostgresUserStore:
    """基于 PostgreSQL / TimescaleDB 的用户仓储。"""

    _schema_ready = False

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_schema(self) -> None:
        """幂等建表（项目无 alembic 迁移流程，首次使用时补齐）。"""
        if PostgresUserStore._schema_ready:
            return
        for statement in filter(None, (s.strip() for s in _DDL.split(";"))):
            await self._session.execute(text(statement))
        await self._session.commit()
        PostgresUserStore._schema_ready = True

    async def count(self) -> int:
        await self.ensure_schema()
        row = (await self._session.execute(text("SELECT COUNT(*) AS n FROM users"))).fetchone()
        return int(row.n) if row else 0

    async def get_by_username(self, username: str) -> UserRecord | None:
        await self.ensure_schema()
        row = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM users WHERE username = :username"),
                {"username": username},
            )
        ).fetchone()
        return _row_to_record(row) if row else None

    async def get(self, user_id: str) -> UserRecord | None:
        await self.ensure_schema()
        row = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM users WHERE id = CAST(:id AS UUID)"),
                {"id": user_id},
            )
        ).fetchone()
        return _row_to_record(row) if row else None

    async def list(self, limit: int, offset: int) -> tuple[list[UserRecord], int]:
        await self.ensure_schema()
        total_row = (
            await self._session.execute(text("SELECT COUNT(*) AS n FROM users"))
        ).fetchone()
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM users "
                    "ORDER BY created_at ASC LIMIT :limit OFFSET :offset"
                ),
                {"limit": min(limit, MAX_PAGE_SIZE), "offset": offset},
            )
        ).fetchall()
        return [_row_to_record(r) for r in rows], int(total_row.n) if total_row else 0

    async def create(self, record: UserRecord) -> UserRecord:
        await self.ensure_schema()
        if await self.get_by_username(record.username) is not None:
            raise DuplicateUsernameError(f"用户名已存在: {record.username}")
        await self._session.execute(_INSERT_SQL, _params(record))
        await self._session.commit()
        logger.info("创建用户 username=%s role=%s", record.username, record.role.value)
        return record

    async def update(self, record: UserRecord) -> UserRecord:
        await self.ensure_schema()
        result = await self._session.execute(_UPDATE_SQL, _params(record))
        await self._session.commit()
        if not result.rowcount:
            raise UserNotFoundError(f"用户不存在: {record.id}")
        logger.info("更新用户 id=%s role=%s", record.id, record.role.value)
        return record

    async def delete(self, user_id: str) -> bool:
        await self.ensure_schema()
        result = await self._session.execute(_DELETE_SQL, {"id": user_id})
        await self._session.commit()
        return bool(result.rowcount)


def _params(record: UserRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "username": record.username,
        "email": record.email,
        "hashed_pw": record.hashed_pw,
        # 再校一次：即使调用方绕过 build_user 直接构造 UserRecord，也不让非法 role 落库
        "role": normalize_role_strict(record.role).value,
        "is_active": record.is_active,
        "created_at": datetime.fromisoformat(record.created_at)
        if record.created_at
        else datetime.now(UTC),
    }
