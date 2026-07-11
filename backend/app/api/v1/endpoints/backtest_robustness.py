"""
蒙特卡洛稳健性与统计显著性 API 端点（Wave 3 · C4/C5）

- POST /backtests/mc-robustness  逐笔重采样稳健性（bootstrap / shuffle → 置信区间）
- POST /backtests/significance   Bootstrap 假设检验（策略 edge p 值 + 规则贡献度）

两者均复用现有 BacktestEngine 跑一次真实回测，再从回合交易 (round trips) 抽取逐笔盈亏，
CPU 密集的重采样通过 run_in_threadpool 卸载，避免阻塞事件循环。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.data.models import Bar, Frequency, Market
from app.data.service import DataService
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.mc_robustness import McRobustnessResult, run_mc_robustness
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.significance import SignificanceResult, analyze_significance
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()

_A_ALLOWED_FREQS = {Frequency.DAY_1, Frequency.WEEK_1}
_MIN_BARS = 10


# ── 依赖注入 ─────────────────────────────────────────────────────

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# ── 通用验证 & 数据加载 ──────────────────────────────────────────

async def _validate_and_fetch(
    strategy_name: str, market_str: str, frequency_str: str,
    start_date: date, end_date: date, symbol: str, svc: DataService,
) -> tuple[Market, list[Bar]]:
    if strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"未知策略 '{strategy_name}'，可用: {list(STRATEGY_REGISTRY.keys())}")
    try:
        market = Market(market_str.upper())
    except ValueError:
        raise HTTPException(400, f"无效市场 '{market_str}'")
    try:
        frequency = Frequency(frequency_str)
    except ValueError:
        raise HTTPException(400, f"无效频率 '{frequency_str}'")
    if market == Market.A and frequency not in _A_ALLOWED_FREQS:
        raise HTTPException(400, f"A股仅支持日线(1d)和周线(1w)，不支持: {frequency_str}")
    try:
        bars = await svc.get_bars(
            symbol=symbol, market=market, frequency=frequency,
            start=start_date, end=end_date,
        )
    except Exception as e:
        raise HTTPException(503, f"获取行情失败: {e}")
    if len(bars) < _MIN_BARS:
        raise HTTPException(422, f"数据不足：仅获取到 {len(bars)} 根 K 线，稳健性分析建议 ≥ 60 根。")
    return market, bars


def _run_backtest_trips(
    strategy_name: str, params: dict, bars: list[Bar], market: Market, initial_cash: float,
) -> tuple[list[float], list[str], dict]:
    """跑一次回测，返回 (逐笔净盈亏, 逐笔开仓标签, 回测 metrics)。"""
    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    strategy = strategy_cls(params=params)
    engine = BacktestEngine(BacktestConfig(initial_cash=initial_cash, market=market))
    result = engine.run(strategy, bars)
    trips = build_round_trips(result.fills)
    pnls = [t.pnl for t in trips]
    tags = [t.entry_tag for t in trips]
    return pnls, tags, result.report["metrics"]


# ══════════════════════════════════════════════════════════════════
# C4 — 蒙特卡洛稳健性（逐笔重采样）
# ══════════════════════════════════════════════════════════════════

class McRobustnessRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数（固定）")
    method: str = Field("bootstrap", description="bootstrap 有放回重采样 / shuffle 无放回打乱")
    n_scenarios: int = Field(1000, ge=50, le=5000, description="模拟场景数")
    seed: int = Field(42)


class McMetricStatOut(BaseModel):
    name: str
    original: float
    mean: float
    std: float
    min: float
    max: float
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    ci90_lower: float
    ci90_upper: float
    ci95_lower: float
    ci95_upper: float
    p_value: float
    is_significant_5pct: bool
    is_significant_1pct: bool


class McRobustnessResponse(BaseModel):
    method: str
    n_scenarios: int
    n_trades: int
    prob_profit: float
    prob_beat_original: float
    metrics: list[McMetricStatOut]
    envelope: list[dict]
    original_curve: list[float]


def _mc_to_response(r: McRobustnessResult) -> McRobustnessResponse:
    return McRobustnessResponse(
        method=r.method,
        n_scenarios=r.n_scenarios,
        n_trades=r.n_trades,
        prob_profit=r.prob_profit,
        prob_beat_original=r.prob_beat_original,
        metrics=[McMetricStatOut(**m.__dict__) for m in r.metrics],
        envelope=r.envelope,
        original_curve=r.original_curve,
    )


@router.post("/mc-robustness", response_model=McRobustnessResponse)
async def mc_robustness(
    body: McRobustnessRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> McRobustnessResponse:
    """蒙特卡洛稳健性：对逐笔盈亏做重采样，产出收益/最大回撤的置信区间。"""
    market, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, body.symbol, svc,
    )
    try:
        pnls, _tags, _metrics = await run_in_threadpool(
            _run_backtest_trips,
            body.strategy_name, body.params, bars, market, body.initial_cash,
        )
    except Exception as e:
        raise HTTPException(500, f"回测执行失败: {e}")

    try:
        outcome = await run_in_threadpool(
            run_mc_robustness,
            pnls, body.initial_cash, body.n_scenarios, body.method, body.seed,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"蒙特卡洛引擎错误: {e}")

    return _mc_to_response(outcome)


# ══════════════════════════════════════════════════════════════════
# C5 — 统计显著性检验（Bootstrap 假设检验 + 规则贡献度）
# ══════════════════════════════════════════════════════════════════

class SignificanceRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数（固定）")
    n_simulations: int = Field(2000, ge=100, le=20000, description="Bootstrap 重采样次数")
    seed: int = Field(42)


class RuleContributionOut(BaseModel):
    entry_tag: str
    n_trades: int
    total_pnl: float
    pnl_share_pct: float
    mean_pnl: float
    win_rate: float
    p_value: float
    is_significant_5pct: bool
    tested: bool


class SignificanceResponse(BaseModel):
    n_trades: int
    n_simulations: int
    observed_mean_pnl: float
    observed_total_pnl: float
    win_rate: float
    t_stat: float
    effect_size: float
    p_value: float
    is_significant_5pct: bool
    is_significant_1pct: bool
    ci95_mean_lower: float
    ci95_mean_upper: float
    null_hist: list[dict]
    observed_marker: float
    rule_contributions: list[RuleContributionOut]


def _sig_to_response(r: SignificanceResult) -> SignificanceResponse:
    return SignificanceResponse(
        n_trades=r.n_trades,
        n_simulations=r.n_simulations,
        observed_mean_pnl=r.observed_mean_pnl,
        observed_total_pnl=r.observed_total_pnl,
        win_rate=r.win_rate,
        t_stat=r.t_stat,
        effect_size=r.effect_size,
        p_value=r.p_value,
        is_significant_5pct=r.is_significant_5pct,
        is_significant_1pct=r.is_significant_1pct,
        ci95_mean_lower=r.ci95_mean_lower,
        ci95_mean_upper=r.ci95_mean_upper,
        null_hist=r.null_hist,
        observed_marker=r.observed_marker,
        rule_contributions=[RuleContributionOut(**c.__dict__) for c in r.rule_contributions],
    )


@router.post("/significance", response_model=SignificanceResponse)
async def significance_test(
    body: SignificanceRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> SignificanceResponse:
    """统计显著性：Bootstrap 假设检验判断策略 edge 是否显著非随机，并分解规则贡献度。"""
    market, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, body.symbol, svc,
    )
    try:
        pnls, tags, _metrics = await run_in_threadpool(
            _run_backtest_trips,
            body.strategy_name, body.params, bars, market, body.initial_cash,
        )
    except Exception as e:
        raise HTTPException(500, f"回测执行失败: {e}")

    try:
        outcome = await run_in_threadpool(
            analyze_significance,
            pnls, tags, body.n_simulations, body.seed,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"显著性检验引擎错误: {e}")

    return _sig_to_response(outcome)
