"""
认证端点（登录 / 当前用户）

## 登录的取数顺序（V3 Wave C-b · J3）

1. **Postgres `users` 表**（`app/data/storage/users.py`）—— 正常路径。
   表为空时先播种三个内置账户（见 `seed_builtin_users`），再查一次。
2. **数据库不可达时回落 `_BUILTIN_USERS`**，并记 WARNING。
   这是为了保住「零配置启动」—— 现有部署依赖它，直接删掉内置账户会让
   没起 Postgres 的环境彻底登不进去。

⚠️ **数据库可达但查无此人时不回落。** 那种情况说明库是好的，
只是这个账号被管理员删了或改了密码 —— 此时再拿源码里的内置口令放行，
等于「删掉的管理员还能登录」。回落只在**数据库本身不可达**时发生。

被停用（`is_active=False`）的账户一律拒绝登录。
"""

import logging
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        logger.error("密码哈希无法解析，登录被拒")
        return False

# ── 内置账户（首次启动种子 + 数据库不可达时的回落） ────────────────
# 由 app/data/storage/users.py::BUILTIN_SEED 在空表时播种到 users 表。
# 这里保留同一份数据，作为「Postgres 不可达」时的零配置登录回落路径。
# role 字段对应 RBAC 角色（app/core/rbac.py）：admin / trader / viewer
_BUILTIN_USERS: dict[str, dict[str, str]] = {
    "admin": {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "admin@quantbot.local",
        "role": "admin",
        # bcrypt hash 对应 "admin123" (bcrypt 5.x 生成)
        "hashed_pw": "$2b$12$0kMLEk./lr7l8hLBc4MIaeZTrJwp03XI3Zjw2LmiBjp5Of.KqvwWC",
    },
    # 示范账户：Trader 可下单但不可改系统配置
    "trader": {
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "trader@quantbot.local",
        "role": "trader",
        # bcrypt hash 对应 "trader123"
        "hashed_pw": "$2b$12$c1/sv7s00LizRZTO1rrO/eDRIYo3YReJ//Cdk8cNBNZfIPENRYvDS",
    },
    # 示范账户：Viewer 只读，写操作会被 RBAC 拦截为 403
    "viewer": {
        "id": "00000000-0000-0000-0000-000000000003",
        "email": "viewer@quantbot.local",
        "role": "viewer",
        # bcrypt hash 对应 "viewer123"
        "hashed_pw": "$2b$12$Hr02dEzvB8GmnjWP2eR7yektufn5V3wmgrV6pjgMClFSMM1qMZgXK",
    },
}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserInfo(BaseModel):
    id: str
    email: str
    role: str


def create_access_token(data: dict) -> str:
    payload = {
        **data,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserInfo:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id: str | None = payload.get("sub")
        # fail-safe：缺失 role 时降级为最低权限 viewer（绝不默认提权）
        role: str = payload.get("role", "viewer")
        email: str = payload.get("email", "")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc from None

    return UserInfo(id=user_id, email=email, role=role)


async def _lookup_user(username: str) -> dict[str, str] | None:
    """
    查用户：Postgres 优先，**仅在数据库不可达时**回落内置账户。

    返回 `{"id", "email", "role", "hashed_pw"}`；查无此人或账户已停用返回 None。
    """
    try:
        from app.core.database import AsyncSessionLocal
        from app.data.storage.users import PostgresUserStore, seed_builtin_users

        async with AsyncSessionLocal() as session:
            store = PostgresUserStore(session)
            if await store.count() == 0:
                await seed_builtin_users(store)
            record = await store.get_by_username(username)
    except Exception as exc:
        logger.warning(
            "用户库不可达，本次登录回落内置账户（零配置启动路径）：%s", exc
        )
        return _BUILTIN_USERS.get(username)

    if record is None:
        # 库是好的、就是没这个人 —— 绝不回落内置口令，否则删掉的账号还能登录
        return None
    if not record.is_active:
        logger.warning("已停用账户尝试登录：%s", username)
        return None
    return {
        "id": record.id,
        "email": record.email,
        "role": record.role.value,
        "hashed_pw": record.hashed_pw,
    }


@router.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Token:
    user = await _lookup_user(form_data.username)
    if user is None or not _verify_password(form_data.password, user["hashed_pw"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({
        "sub": user["id"],
        "role": user["role"],
        "email": user["email"],
    })
    return Token(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def get_me(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    return current_user
