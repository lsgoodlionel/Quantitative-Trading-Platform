"""
一键完整验证 API（V3 · H2）

- POST /backtests/full-validation  串行跑 回测 → 寻优 → Walk-Forward → 偏差 → 稳健性

共享一次数据加载与一份配置，避免用户在 5 个表单里重复填写 symbol/策略/日期。
**任一步失败不中断整体**：该步返回 `{error}`，其余照常，综合评级标注「基于 N/5 步」。

执行模式
--------
同步执行，整段编排通过 `run_in_threadpool` 卸载到线程池（各引擎均为 CPU 密集的
同步代码），不阻塞事件循环。默认参数下（n_trials=24 / n_scenarios=500）在日线
2~4 年区间内可在数十秒内返回。若需要更大规模，请用 `steps` 分批跑 —— 本期不引入
Celery：Celery worker 侧没有异步 DataService/DB 会话的现成管线，为一个端点搭建
会引入远超收益的复杂度，宁可显式暴露「分步跑」这个用户可控的旋钮。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.data.models import Bar, Frequency, Market
from app.data.service import DataService
from app.engine.backtest.bias_detection import run_bias_check
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.full_validation import run_full_validation
from app.engine.backtest.hyperopt import ParamSpace, run_hyperopt
from app.engine.backtest.mc_robustness import run_mc_robustness
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.validation_grade import (
    ALL_STEPS,
    STEP_BACKTEST,
    STEP_BIAS,
    STEP_OPTIMIZE,
    STEP_ROBUSTNESS,
    STEP_WALKFORWARD,
)
from app.engine.backtest.validation_steps import (
    auto_window_sizes,
    derive_param_space,
    normalize_backtest,
    normalize_bias,
    normalize_optimize,
    normalize_robustness,
    normalize_walkforward,
)
from app.engine.backtest.walkforward import run_walk_forward
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()

_A_ALLOWED_FREQS = {Frequency.DAY_1, Frequency.WEEK_1}
_MIN_BARS = 30


# ── Schemas ──────────────────────────────────────────────────────

class FullValidationRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数（固定值，作为基准配置）")
    steps: list[str] | None = Field(
        None,
        description=f"只跑指定步骤，缺省全跑。可选: {list(ALL_STEPS)}",
    )
    param_space: dict | None = Field(
        None,
        description="寻优/Walk-Forward 的参数空间；缺省由 params 邻域推导",
    )
    loss_function: str = Field("sharpe", description="寻优损失函数")
    n_trials: int = Field(24, ge=1, le=200, description="寻优评估次数")
    train_size: int | None = Field(None, ge=5, le=2000, description="WF 训练窗口 bar 数，缺省自适应")
    test_size: int | None = Field(None, ge=5, le=1000, description="WF 测试窗口 bar 数，缺省自适应")
    n_scenarios: int = Field(500, ge=50, le=5000, description="稳健性重采样场景数")
    seed: int = Field(42)


class FullValidationResponse(BaseModel):
    run_id: str
    requested_steps: list[str]
    steps: dict[str, dict]
    grade: dict


# ── 依赖注入 ─────────────────────────────────────────────────────

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


async def _validate_and_fetch(
    body: FullValidationRequest, svc: DataService,
) -> tuple[Market, list[Bar]]:
    if body.strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"未知策略 '{body.strategy_name}'，可用: {list(STRATEGY_REGISTRY.keys())}")
    try:
        market = Market(body.market.upper())
    except ValueError:
        raise HTTPException(400, f"无效市场 '{body.market}'") from None
    try:
        frequency = Frequency(body.frequency)
    except ValueError:
        raise HTTPException(400, f"无效频率 '{body.frequency}'") from None
    if market == Market.A and frequency not in _A_ALLOWED_FREQS:
        raise HTTPException(400, f"A股仅支持日线(1d)和周线(1w)，不支持: {body.frequency}")
    try:
        bars = await svc.get_bars(
            symbol=body.symbol, market=market, frequency=frequency,
            start=body.start_date, end=body.end_date,
        )
    except Exception as e:
        raise HTTPException(503, f"获取行情失败: {e}") from e
    if len(bars) < _MIN_BARS:
        raise HTTPException(422, f"数据不足：仅 {len(bars)} 根 K 线，完整验证至少需 {_MIN_BARS} 根。")
    return market, bars


# ── 步骤执行器构造 ────────────────────────────────────────────────

def _make_runners(
    body: FullValidationRequest, market: Market, bars: list[Bar],
) -> dict[str, Any]:
    """把请求参数与行情闭包成五个无参 runner，交由编排器逐个执行。"""
    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]
    cash = body.initial_cash

    def _metrics_on(params: dict, window: list[Bar]) -> dict:
        engine = BacktestEngine(BacktestConfig(initial_cash=cash, market=market))
        return engine.run(strategy_cls(params=params), window).report["metrics"]

    def _space() -> ParamSpace:
        spec = body.param_space or derive_param_space(body.params)
        return ParamSpace.from_spec(spec)

    def run_backtest_step() -> dict:
        engine = BacktestEngine(BacktestConfig(initial_cash=cash, market=market))
        result = engine.run(strategy_cls(params=body.params), bars)
        return normalize_backtest(result.report, result.final_value)

    def run_optimize_step() -> dict:
        outcome = run_hyperopt(
            lambda p: _metrics_on(p, bars), _space(),
            body.loss_function, "grid", body.n_trials, 1, body.seed,
        )
        return normalize_optimize(outcome)

    def run_walkforward_step() -> dict:
        train, test = auto_window_sizes(len(bars), body.train_size, body.test_size)
        space = _space()

        def optimize_fn(train_bars: list[Bar]) -> dict:
            return run_hyperopt(
                lambda p: _metrics_on(p, train_bars), space,
                body.loss_function, "grid", body.n_trials, 1, body.seed,
            ).best_params

        outcome = run_walk_forward(
            bars, optimize_fn, lambda p, w: _metrics_on(p, w), train, test, "rolling",
        )
        return normalize_walkforward(outcome)

    def run_bias_step() -> dict:
        def run_fills(window: list[Bar]) -> list[dict]:
            if len(window) < 2:
                return []
            engine = BacktestEngine(BacktestConfig(initial_cash=cash, market=market))
            return engine.run(strategy_cls(params=body.params), window).fills

        return normalize_bias(run_bias_check(run_fills, bars, [50, 100, 200], 0.7))

    def run_robustness_step() -> dict:
        engine = BacktestEngine(BacktestConfig(initial_cash=cash, market=market))
        fills = engine.run(strategy_cls(params=body.params), bars).fills
        pnls = [t.pnl for t in build_round_trips(fills)]
        return normalize_robustness(
            run_mc_robustness(pnls, cash, body.n_scenarios, "bootstrap", body.seed)
        )

    return {
        STEP_BACKTEST: run_backtest_step,
        STEP_OPTIMIZE: run_optimize_step,
        STEP_WALKFORWARD: run_walkforward_step,
        STEP_BIAS: run_bias_step,
        STEP_ROBUSTNESS: run_robustness_step,
    }


# ── 端点 ─────────────────────────────────────────────────────────

@router.post("/full-validation", response_model=FullValidationResponse)
async def full_validation(
    body: FullValidationRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> FullValidationResponse:
    """
    一键完整验证：共享一份配置串行跑五步验证，产出规则化综合评级。

    评级为启发式汇总而非判决：`grade.findings` 逐条写明触发依据，
    `grade.not_evaluated` 列出因步骤缺失而未能检查的规则。
    """
    market, bars = await _validate_and_fetch(body, svc)
    runners = _make_runners(body, market, bars)
    try:
        outcome = await run_in_threadpool(run_full_validation, runners, body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    data = outcome.to_dict()
    return FullValidationResponse(
        run_id=data["run_id"],
        requested_steps=data["requested_steps"],
        steps=data["steps"],
        grade=data["grade"],
    )
