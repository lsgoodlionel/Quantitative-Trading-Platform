"""
RBAC — 基于角色的访问控制（Admin / Trader / Viewer）

层级：Viewer < Trader < Admin（数值越大权限越高）。

用法（FastAPI 依赖）::

    from app.core.rbac import Role, require_role

    @router.post("")
    async def submit_order(
        body: SubmitOrderRequest,
        _user=Depends(require_role(Role.TRADER)),
    ):
        ...

复用 auth.py 的 ``get_current_user``：JWT payload 中已含 ``role`` 字段。
角色不足时抛 403 Forbidden。
"""

from __future__ import annotations

from enum import Enum

from fastapi import Depends, HTTPException, status

from app.api.v1.endpoints.auth import UserInfo, get_current_user


class Role(str, Enum):
    """系统角色。继承 str 以便直接与 JWT / JSON 字符串互操作。"""

    VIEWER = "viewer"
    TRADER = "trader"
    ADMIN = "admin"


# 角色层级：数值越大权限越高。用于比较「是否达到最低要求」。
_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.TRADER: 1,
    Role.ADMIN: 2,
}


def normalize_role(raw: str | None) -> Role:
    """
    将任意字符串安全转换为 Role。

    未知 / 缺失角色一律降级为最低权限 Viewer（fail-safe，绝不提权）。
    """
    if not raw:
        return Role.VIEWER
    try:
        return Role(raw.strip().lower())
    except ValueError:
        return Role.VIEWER


def has_permission(user_role: str | Role, min_role: Role) -> bool:
    """判断 user_role 是否达到 min_role 的层级要求。"""
    current = user_role if isinstance(user_role, Role) else normalize_role(user_role)
    return _ROLE_RANK[current] >= _ROLE_RANK[min_role]


def require_role(min_role: Role):
    """
    构造一个 FastAPI 依赖：要求当前用户角色 >= min_role，否则抛 403。

    返回校验通过的 UserInfo，端点可按需使用（如审计日志）。
    """

    async def _dependency(
        current_user: UserInfo = Depends(get_current_user),
    ) -> UserInfo:
        if not has_permission(current_user.role, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"权限不足：该操作需要 {min_role.value} 及以上角色，"
                    f"当前角色为 {normalize_role(current_user.role).value}。"
                ),
            )
        return current_user

    return _dependency
