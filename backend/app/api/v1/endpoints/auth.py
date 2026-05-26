from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

# ── 内置账户（种子数据同步） ──────────────────────────────────────
# 生产环境应改用数据库查询；此处保持 Phase 1 的内置账户以支持零配置启动
_BUILTIN_USERS: dict[str, dict[str, str]] = {
    "admin": {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "admin@quantbot.local",
        "role": "admin",
        # bcrypt hash 对应 "admin123" (bcrypt 5.x 生成)
        "hashed_pw": "$2b$12$0kMLEk./lr7l8hLBc4MIaeZTrJwp03XI3Zjw2LmiBjp5Of.KqvwWC",
    }
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
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": datetime.now(timezone.utc),
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
        role: str = payload.get("role", "trader")
        email: str = payload.get("email", "")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    return UserInfo(id=user_id, email=email, role=role)


@router.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Token:
    user = _BUILTIN_USERS.get(form_data.username)
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
