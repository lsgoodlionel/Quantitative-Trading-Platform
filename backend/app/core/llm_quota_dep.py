"""LLM 配额在 HTTP 层的接线。

单独一个模块而不是塞进 `llm_quota.py`：后者是纯逻辑，被 Celery 任务
（自动因子循环的 LLM 复盘）直接调用，不该把 fastapi 拖进那条依赖链。

⚠️ **刻意不做成路由级 `Depends`。** 依赖在进入函数体之前就执行，而两个
AI 端点都是「先解析 provider，解析不出来就走引导/501」——挂成依赖的话，
一个还没配模型的用户靠反复收到引导语就能把当天额度烧光，而那些请求
一次 LLM 都没调过。所以配额必须**显式扣在真正要发起调用的那一行之前**。
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.v1.endpoints.auth import UserInfo
from app.core.llm_quota import QuotaState, consume

#: 429 的 detail 里带上上限，用户才知道该找谁调
_EXCEEDED_DETAIL = (
    "今日 AI 调用次数已达上限（{limit} 次/日）。"
    "配额按 UTC 自然日重置；需要更高额度请联系管理员。"
)


async def charge_llm_quota(user: UserInfo) -> QuotaState:
    """记一次 LLM 调用；超限抛 429。

    调用点必须紧贴真正的模型调用 —— 见模块文档。
    """
    state = await consume(user.id, user.role)
    if not state.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_EXCEEDED_DETAIL.format(limit=state.limit),
            headers={"Retry-After": "3600"},
        )
    return state
