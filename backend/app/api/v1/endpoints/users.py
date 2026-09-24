"""
用户管理 API（V3 · J3）

- GET    /api/v1/users        列出用户（分页）
- POST   /api/v1/users        创建用户
- PUT    /api/v1/users/{id}   修改角色 / 邮箱 / 启用状态 / 密码
- DELETE /api/v1/users/{id}   删除用户

**全部要求 `Role.ADMIN`**：本期只做「管理员维护账户」。

⚠️ **不做用户自助注册。** 开放注册要连带做邮箱验证、限流、防滥用、找回密码，
那是另一个量级的工作；半套的注册流程比没有更危险。

安全约束：
* 响应模型只吐 `UserRecord.to_public_dict()`，**任何情况下都不返回 `hashed_pw`**。
* 非法 `role` 一律 400 拒绝，不静默降级（见 `users.normalize_role_strict`）。
* 管理员不能删除或停用自己 —— 把最后一条进管理后台的路自己锁上是不可逆事故。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import UserInfo
from app.core.audit import AuditAction, audit_log
from app.core.database import get_db
from app.core.rbac import Role, require_role
from app.data.storage.users import (
    MAX_PAGE_SIZE,
    DuplicateUsernameError,
    InvalidRoleError,
    InvalidUserInputError,
    PostgresUserStore,
    UserError,
    UserNotFoundError,
    UserRecord,
    UserStore,
    build_user,
    hash_password,
    normalize_role_strict,
    validate_username,
)

router = APIRouter()

T = TypeVar("T")

AdminDep = Annotated[UserInfo, Depends(require_role(Role.ADMIN))]


def get_store(session: AsyncSession = Depends(get_db)) -> UserStore:
    return PostgresUserStore(session)


StoreDep = Annotated[UserStore, Depends(get_store)]


# ── Schemas ──────────────────────────────────────────────────────

class UserOut(BaseModel):
    """对外用户视图。**刻意没有 hashed_pw 字段** —— 加回来就是泄露。"""

    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: str


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


class CreateUserRequest(BaseModel):
    username: str = Field(description="字母/数字/. _ -，3~64 位")
    password: str = Field(description="至少 8 位")
    role: str = Field(description="admin / trader / viewer")
    email: str = ""
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    """全部可选；只改传了的字段。"""

    password: str | None = None
    role: str | None = None
    email: str | None = None
    is_active: bool | None = None


# ── 端点 ─────────────────────────────────────────────────────────

@router.get("", response_model=UserListResponse)
async def list_users(
    store: StoreDep,
    _admin: AdminDep,
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> UserListResponse:
    """列出用户（按创建时间正序）。"""
    records, total = await _guard(store.list(limit=limit, offset=offset))
    return UserListResponse(
        items=[UserOut(**r.to_public_dict()) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: CreateUserRequest,
    store: StoreDep,
    admin: AdminDep,
) -> UserOut:
    """创建用户。用户名重复 409，role / 密码非法 400。"""
    record = _guard_sync(
        lambda: build_user(
            username=body.username,
            password=body.password,
            role=body.role,
            email=body.email,
            is_active=body.is_active,
        )
    )
    saved = await _guard(store.create(record))
    await _audit(AuditAction.USER_CREATE, admin, saved, extra={"role": saved.role.value})
    return UserOut(**saved.to_public_dict())


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    store: StoreDep,
    admin: AdminDep,
) -> UserOut:
    """修改用户。停用自己会被拒绝（避免把自己锁在门外）。"""
    existing = await _guard(store.get(user_id))
    if existing is None:
        raise HTTPException(404, f"用户不存在: {user_id}")

    if body.is_active is False and _is_self(admin, existing):
        raise HTTPException(400, "不能停用当前登录的管理员账户")
    if body.role is not None and _is_self(admin, existing):
        new_role = _guard_sync(lambda: normalize_role_strict(body.role))
        if new_role is not Role.ADMIN:
            raise HTTPException(400, "不能降低当前登录管理员自身的角色")

    updated = _guard_sync(lambda: _apply_update(existing, body))
    saved = await _guard(store.update(updated))
    await _audit(
        AuditAction.USER_UPDATE,
        admin,
        saved,
        extra={
            "role": saved.role.value,
            "is_active": saved.is_active,
            # 只记「改没改密码」，绝不记密码本身或其哈希
            "password_changed": body.password is not None,
        },
    )
    return UserOut(**saved.to_public_dict())


@router.delete("/{user_id}")
async def delete_user(user_id: str, store: StoreDep, admin: AdminDep) -> dict[str, Any]:
    """删除用户。不能删除自己。"""
    existing = await _guard(store.get(user_id))
    if existing is None:
        raise HTTPException(404, f"用户不存在: {user_id}")
    if _is_self(admin, existing):
        raise HTTPException(400, "不能删除当前登录的管理员账户")

    deleted = await _guard(store.delete(user_id))
    await _audit(AuditAction.USER_DELETE, admin, existing)
    return {"deleted": bool(deleted), "id": user_id}


# ── 内部工具 ──────────────────────────────────────────────────────

def _apply_update(existing: UserRecord, body: UpdateUserRequest) -> UserRecord:
    """把补丁套到现有记录上，返回**新**记录（不就地修改）。"""
    changes: dict[str, Any] = {}
    if body.password is not None:
        changes["hashed_pw"] = hash_password(body.password)
    if body.role is not None:
        changes["role"] = normalize_role_strict(body.role)
    if body.email is not None:
        changes["email"] = body.email.strip()
    if body.is_active is not None:
        changes["is_active"] = body.is_active
    if not changes:
        raise InvalidUserInputError("没有可更新的字段")
    # username 不可改：它是登录标识，改名等于换账户；真要换请新建 + 删除
    validate_username(existing.username)
    return replace(existing, **changes)


def _is_self(admin: UserInfo, target: UserRecord) -> bool:
    """当前登录管理员是否就是被操作的用户（JWT sub 存的是 users.id）。"""
    return bool(admin.id) and admin.id == target.id


async def _audit(
    action: str, admin: UserInfo, target: UserRecord, *, extra: dict[str, Any] | None = None
) -> None:
    await audit_log(
        action,
        actor=admin.email or admin.id or "system",
        detail={"user_id": target.id, "username": target.username, **(extra or {})},
    )


async def _guard(awaitable: Awaitable[T]) -> T:
    """把仓储异常翻译成 HTTP 状态码。"""
    try:
        return await awaitable
    except UserError as exc:
        raise _to_http_error(exc) from exc


def _guard_sync(fn: Callable[[], T]) -> T:
    """同上，用于同步的构造/校验调用。"""
    try:
        return fn()
    except UserError as exc:
        raise _to_http_error(exc) from exc


def _to_http_error(exc: UserError) -> HTTPException:
    if isinstance(exc, DuplicateUsernameError):
        return HTTPException(409, str(exc))
    if isinstance(exc, UserNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, InvalidRoleError | InvalidUserInputError):
        return HTTPException(400, str(exc))
    return HTTPException(500, f"用户仓储错误: {exc}")
