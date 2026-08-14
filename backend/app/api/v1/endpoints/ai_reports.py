"""AI 报告 API（V3 Wave C-a / I4+I5）

    POST /api/v1/ai/reports/stock      个股研报（分节 + sources + 免责声明）
    POST /api/v1/ai/reports/backtest   回测 AI 诊断（解读规则评级，不取代它）

状态码分层，与 LLM 网关一致：

- **501** 一个 provider 都没配 → 功能未启用，`detail` 指向 `/settings/models`
- **503** 配了但连不上 / 鉴权失败 → 依赖挂了
- **502** 模型连上了但反复吐不出可解析结构 → 结构化错误，**不返回半截报告**
- **422** 行情数据不足以生成研报

⚠️ **`/backtest` 收的是完整验证的结果体本身，而不是 `run_id`**：
`full-validation` 的 `run_id` 是每次请求现生成的（见 `engine/backtest/full_validation.py`），
平台从未把它落库 —— 拿 id 反查是查不到东西的。前端手里就有那份结果，
直接回传是唯一不撒谎的做法。`run_id` 仍可选传，只用于结果溯源展示。
"""

from __future__ import annotations

from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_reports import (
    AIReportError,
    SnapshotError,
    build_stock_snapshot,
    generate_backtest_diagnosis,
    generate_stock_report,
)
from app.api.v1.endpoints.auth import UserInfo
from app.core.database import get_db
from app.core.llm import LLMNotConfiguredError, LLMUnavailableError, resolve_active
from app.core.logging import get_logger
from app.core.rbac import Role, require_role
from app.core.redis import get_redis
from app.data.models import Market

logger = get_logger(__name__)

router = APIRouter()

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
SessionDep = Annotated[AsyncSession, Depends(get_db)]
AuthedDep = Annotated[UserInfo, Depends(require_role(Role.VIEWER))]

#: 未配置模型时引导用户去的页面
SETUP_URL = "/settings/models"

DEFAULT_LOOKBACK_DAYS = 180
MIN_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 1095

#: 单次诊断接受的步骤原始结果条数上限（五步 + 余量）
MAX_STEPS = 10

#: starlette 已把 `HTTP_422_UNPROCESSABLE_ENTITY` 标记为弃用，这里直接用字面量，
#: 与本项目其余端点（`raise HTTPException(422, ...)`）的写法一致
HTTP_422_UNPROCESSABLE = 422


# ── Schemas ──────────────────────────────────────────────────────────────────

class StockReportRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market: str = Field(default="US", max_length=8)
    lookback_days: int = Field(
        default=DEFAULT_LOOKBACK_DAYS, ge=MIN_LOOKBACK_DAYS, le=MAX_LOOKBACK_DAYS
    )
    model: str | None = Field(default=None, max_length=200, description="单次覆盖模型")


class SectionTitle(BaseModel):
    key: str
    title: str


class NewsSourceOut(BaseModel):
    title: str
    published_at: str | None = None
    publisher: str | None = None
    url: str | None = None


class StockReportResponse(BaseModel):
    symbol: str
    market: str
    sections: dict[str, str]
    section_titles: list[SectionTitle]
    #: 实际喂给模型的新闻标题与时间 —— 用户据此核对模型有没有瞎编（契约 §1.1 第 1 点）
    sources: list[NewsSourceOut]
    #: 本次缺了哪些数据，前端与提示词看到的是同一句话
    data_notes: list[str]
    technicals: dict[str, Any]
    generated_at: str
    model: str
    provider_id: str
    disclaimer: str


class GradeFindingIn(BaseModel):
    """`grade.findings` 的一条。字段与 `engine/backtest/validation_grade.Finding` 对齐。"""

    step: str = ""
    rule: str = ""
    metric: str = ""
    value: float | None = None
    threshold: float | None = None
    penalty: float | None = None
    detail: str = ""


class GradeNotEvaluatedIn(BaseModel):
    step: str = ""
    rule: str = ""
    reason: str = ""


class ValidationGradeIn(BaseModel):
    """完整验证的规则化评级。整份原样回传，服务端不重算。"""

    score: float = 0.0
    level: str = ""
    level_label: str = ""
    completed_steps: list[str] = Field(default_factory=list)
    failed_steps: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)
    findings: list[GradeFindingIn] = Field(default_factory=list)
    not_evaluated: list[GradeNotEvaluatedIn] = Field(default_factory=list)
    based_on: str = ""
    is_complete: bool = False


class BacktestDiagnosisRequest(BaseModel):
    #: 完整验证返回体里的 run_id，仅用于溯源展示（平台不落库，无法反查）
    run_id: str | None = Field(default=None, max_length=64)
    grade: ValidationGradeIn
    steps: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=MAX_STEPS)
    model: str | None = Field(default=None, max_length=200, description="单次覆盖模型")


class DiagnosisFindingOut(BaseModel):
    title: str
    detail: str
    severity: str
    rule: str | None = None


class ContradictionOut(BaseModel):
    rule: str
    claim: str
    detail: str


class BacktestDiagnosisResponse(BaseModel):
    run_id: str | None
    grade_level: str
    grade_score: float
    #: `grade.based_on`，形如「基于 3/5 步」—— 结论里也会带上（契约 §2.1）
    coverage: str
    is_complete: bool
    summary: str
    findings: list[DiagnosisFindingOut]
    next_steps: list[str]
    #: AI 解读与规则判据冲突之处；非空即表示这份诊断不可照单全收
    contradictions: list[ContradictionOut]
    has_contradiction: bool
    generated_at: str
    model: str
    provider_id: str
    disclaimer: str


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.post("/stock", response_model=StockReportResponse)
async def stock_report(
    body: StockReportRequest,
    redis: RedisDep,
    session: SessionDep,
    _user: AuthedDep,
) -> StockReportResponse:
    """生成一份个股研报。不缓存 —— 依赖实时行情与新闻（契约 §1.1 第 4 点）。"""
    market = _parse_market(body.market)
    resolved = await _resolve_provider(redis, body.model)

    try:
        snapshot = await build_stock_snapshot(
            session,
            symbol=body.symbol.strip(),
            market=market,
            lookback_days=body.lookback_days,
        )
    except SnapshotError as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc

    try:
        report = await generate_stock_report(resolved.provider, snapshot)
    except LLMUnavailableError as exc:
        raise _unavailable(exc) from exc
    except AIReportError as exc:
        raise _bad_gateway(exc) from exc

    return StockReportResponse(
        symbol=report.symbol,
        market=report.market,
        sections=report.sections,
        section_titles=[SectionTitle(**item) for item in report.section_titles],
        sources=[NewsSourceOut(**item) for item in report.sources],
        data_notes=report.data_notes,
        technicals=report.technicals,
        generated_at=report.generated_at,
        model=report.model,
        provider_id=resolved.preset.id,
        disclaimer=report.disclaimer,
    )


@router.post("/backtest", response_model=BacktestDiagnosisResponse)
async def backtest_diagnosis(
    body: BacktestDiagnosisRequest,
    redis: RedisDep,
    _user: AuthedDep,
) -> BacktestDiagnosisResponse:
    """把完整验证的规则判据翻译成人能读的诊断。AI 解读评级，不取代评级。"""
    resolved = await _resolve_provider(redis, body.model)

    try:
        diagnosis = await generate_backtest_diagnosis(
            resolved.provider,
            body.grade.model_dump(),
            {name: dict(payload) for name, payload in body.steps.items()},
            run_id=body.run_id,
        )
    except LLMUnavailableError as exc:
        raise _unavailable(exc) from exc
    except AIReportError as exc:
        raise _bad_gateway(exc) from exc

    if diagnosis.has_contradiction:
        logger.warning(
            "ai diagnosis contradicts rule findings",
            run_id=body.run_id,
            rules=[c["rule"] for c in diagnosis.contradictions],
        )

    return BacktestDiagnosisResponse(
        run_id=diagnosis.run_id,
        grade_level=diagnosis.grade_level,
        grade_score=diagnosis.grade_score,
        coverage=diagnosis.coverage,
        is_complete=diagnosis.is_complete,
        summary=diagnosis.summary,
        findings=[DiagnosisFindingOut(**item) for item in diagnosis.findings],
        next_steps=diagnosis.next_steps,
        contradictions=[ContradictionOut(**item) for item in diagnosis.contradictions],
        has_contradiction=diagnosis.has_contradiction,
        generated_at=diagnosis.generated_at,
        model=diagnosis.model,
        provider_id=resolved.preset.id,
        disclaimer=diagnosis.disclaimer,
    )


# ── 内部 ─────────────────────────────────────────────────────────────────────

async def _resolve_provider(redis: Any, model: str | None) -> Any:
    """解析当前 provider。未配置 → 501 并指向配置页；连不上 → 503。"""
    try:
        return await resolve_active(redis, model_override=model)
    except LLMNotConfiguredError as exc:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"{exc} 请前往 {SETUP_URL} 完成配置后重试。",
        ) from exc
    except LLMUnavailableError as exc:
        raise _unavailable(exc) from exc


def _parse_market(value: str) -> Market:
    try:
        return Market(value.strip().upper())
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"无效市场 '{value}'，可选 {[m.value for m in Market]}",
        ) from None


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        f"模型服务当前不可用：{exc} 请到「模型管理」检查地址、密钥与模型名。",
    )


def _bad_gateway(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
