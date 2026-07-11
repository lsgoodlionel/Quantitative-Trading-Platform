"""
策略验证与稳健性 API 端点（Wave 2 · C1/C2/C3）

- POST /backtests/hyperopt     参数优化（grid/random/bayesian + 多目标损失）
- POST /backtests/walkforward  Walk-Forward 滚动样本内外验证
- POST /backtests/bias-check   前视 / 递归偏差检测
- GET  /backtests/hyperopt/loss-functions  可用损失函数列表

三者均复用现有 BacktestEngine，CPU 密集计算通过 run_in_threadpool 卸载，避免阻塞事件循环。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.data.models import Bar, Market, Frequency
from app.data.service import DataService
from app.engine.backtest.bias_detection import run_bias_check
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.hyperopt import (
    ParamSpace,
    list_loss_functions,
    run_hyperopt,
)
from app.engine.backtest.walkforward import run_walk_forward
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()

_A_ALLOWED_FREQS = {Frequency.DAY_1, Frequency.WEEK_1}


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
    if len(bars) < 10:
        raise HTTPException(422, f"数据不足：仅获取到 {len(bars)} 根 K 线，验证类分析建议 ≥ 60 根。")
    return market, bars


def _backtest_metrics(strategy_cls, params: dict, bars: list[Bar], market: Market, initial_cash: float) -> dict:
    """在给定 bar 序列上跑一次回测，返回 metrics 字典。"""
    strategy = strategy_cls(params=params)
    engine = BacktestEngine(BacktestConfig(initial_cash=initial_cash, market=market))
    result = engine.run(strategy, bars)
    return result.report["metrics"]


def _backtest_fills(strategy_cls, params: dict, bars: list[Bar], market: Market, initial_cash: float) -> list[dict]:
    strategy = strategy_cls(params=params)
    engine = BacktestEngine(BacktestConfig(initial_cash=initial_cash, market=market))
    result = engine.run(strategy, bars)
    return result.fills


# ══════════════════════════════════════════════════════════════════
# C1 — Hyperopt 参数优化
# ══════════════════════════════════════════════════════════════════

class HyperoptRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    param_space: dict = Field(
        ...,
        description="参数空间: 列表={'fast':[5,10,20]} 或区间={'fast':{'low':5,'high':50,'step':1,'type':'int'}}",
    )
    algorithm: str = Field("bayesian", description="grid / random / bayesian")
    loss_function: str = Field("sharpe", description="见 /hyperopt/loss-functions")
    n_trials: int = Field(40, ge=1, le=300)
    min_trades: int = Field(1, ge=0, le=1000, description="低于此成交笔数的组合将被重罚")
    seed: int = Field(42)


class HyperoptTrial(BaseModel):
    params: dict
    score: float
    total_return_pct: float
    annual_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int


class HyperoptResponse(BaseModel):
    algorithm: str
    loss_function: str
    best_params: dict
    best_score: float
    best_metrics: dict
    total_space: int
    evaluated: int
    used_fallback: bool
    trials: list[HyperoptTrial]


@router.get("/hyperopt/loss-functions")
async def get_loss_functions() -> list[dict]:
    """返回可用的多目标损失函数（name + 中文 label）。"""
    return list_loss_functions()


@router.post("/hyperopt", response_model=HyperoptResponse)
async def hyperopt_optimize(
    body: HyperoptRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> HyperoptResponse:
    """参数优化：在参数空间内以指定算法与损失函数搜索最优策略参数。"""
    market, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, body.symbol, svc,
    )
    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]

    try:
        space = ParamSpace.from_spec(body.param_space)
    except ValueError as e:
        raise HTTPException(400, f"参数空间无效: {e}")

    def evaluate(params: dict) -> dict:
        return _backtest_metrics(strategy_cls, params, bars, market, body.initial_cash)

    try:
        outcome = await run_in_threadpool(
            run_hyperopt,
            evaluate, space, body.loss_function, body.algorithm,
            body.n_trials, body.min_trades, body.seed,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"优化引擎错误: {e}")

    trials = [
        HyperoptTrial(
            params=t.params,
            score=round(t.score, 4),
            total_return_pct=t.metrics.get("total_return_pct", 0.0),
            annual_return_pct=t.metrics.get("annual_return_pct", 0.0),
            sharpe_ratio=t.metrics.get("sharpe_ratio", 0.0),
            max_drawdown_pct=t.metrics.get("max_drawdown_pct", 0.0),
            total_trades=t.metrics.get("total_trades", 0),
        )
        for t in outcome.trials[:50]
    ]
    return HyperoptResponse(
        algorithm=outcome.algorithm,
        loss_function=outcome.loss_name,
        best_params=outcome.best_params,
        best_score=outcome.best_score,
        best_metrics=outcome.best_metrics,
        total_space=outcome.total_space,
        evaluated=outcome.evaluated,
        used_fallback=outcome.used_fallback,
        trials=trials,
    )


# ══════════════════════════════════════════════════════════════════
# C2 — Walk-Forward 分析
# ══════════════════════════════════════════════════════════════════

class WalkForwardRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    param_space: dict = Field(..., description="训练窗口内寻优的参数空间，同 hyperopt")
    train_size: int = Field(120, ge=5, le=2000, description="训练窗口 bar 数")
    test_size: int = Field(40, ge=5, le=1000, description="测试窗口 bar 数")
    mode: str = Field("rolling", description="rolling 滚动 / anchored 锚定扩张")
    algorithm: str = Field("grid", description="窗口内寻优算法 grid/random/bayesian")
    loss_function: str = Field("sharpe")
    inner_trials: int = Field(24, ge=1, le=100, description="每个训练窗口的寻优评估次数")
    seed: int = Field(42)


class WalkForwardWindowOut(BaseModel):
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    best_params: dict
    is_sharpe: float
    oos_sharpe: float
    is_return_pct: float
    oos_return_pct: float
    oos_max_drawdown_pct: float
    oos_total_trades: int


class WalkForwardResponse(BaseModel):
    mode: str
    train_size: int
    test_size: int
    total_windows: int
    avg_is_sharpe: float
    avg_oos_sharpe: float
    avg_is_return_pct: float
    avg_oos_return_pct: float
    oos_is_efficiency: float
    oos_consistency: float
    oos_win_windows: int
    windows: list[WalkForwardWindowOut]


@router.post("/walkforward", response_model=WalkForwardResponse)
async def walk_forward_analysis(
    body: WalkForwardRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> WalkForwardResponse:
    """Walk-Forward：滚动训练寻优 + 样本外测试，衡量抗曲线拟合能力。"""
    market, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, body.symbol, svc,
    )
    if len(bars) < body.train_size + body.test_size:
        raise HTTPException(
            422,
            f"数据不足：共 {len(bars)} 根 bar，至少需 训练{body.train_size}+测试{body.test_size} 根",
        )
    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]

    try:
        space = ParamSpace.from_spec(body.param_space)
    except ValueError as e:
        raise HTTPException(400, f"参数空间无效: {e}")

    def optimize_fn(train_bars: list[Bar]) -> dict:
        def evaluate(params: dict) -> dict:
            return _backtest_metrics(strategy_cls, params, train_bars, market, body.initial_cash)
        outcome = run_hyperopt(
            evaluate, space, body.loss_function, body.algorithm,
            body.inner_trials, 1, body.seed,
        )
        return outcome.best_params

    def backtest_fn(params: dict, window_bars: list[Bar]) -> dict:
        return _backtest_metrics(strategy_cls, params, window_bars, market, body.initial_cash)

    try:
        outcome = await run_in_threadpool(
            run_walk_forward,
            bars, optimize_fn, backtest_fn,
            body.train_size, body.test_size, body.mode,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Walk-Forward 引擎错误: {e}")

    windows = [
        WalkForwardWindowOut(
            index=w.index,
            train_start=w.train_start, train_end=w.train_end,
            test_start=w.test_start, test_end=w.test_end,
            train_bars=w.train_bars, test_bars=w.test_bars,
            best_params=w.best_params,
            is_sharpe=w.is_metrics.get("sharpe_ratio", 0.0),
            oos_sharpe=w.oos_metrics.get("sharpe_ratio", 0.0),
            is_return_pct=w.is_metrics.get("total_return_pct", 0.0),
            oos_return_pct=w.oos_metrics.get("total_return_pct", 0.0),
            oos_max_drawdown_pct=w.oos_metrics.get("max_drawdown_pct", 0.0),
            oos_total_trades=w.oos_metrics.get("total_trades", 0),
        )
        for w in outcome.windows
    ]
    return WalkForwardResponse(
        mode=outcome.mode,
        train_size=outcome.train_size,
        test_size=outcome.test_size,
        total_windows=outcome.total_windows,
        avg_is_sharpe=outcome.avg_is_sharpe,
        avg_oos_sharpe=outcome.avg_oos_sharpe,
        avg_is_return_pct=outcome.avg_is_return_pct,
        avg_oos_return_pct=outcome.avg_oos_return_pct,
        oos_is_efficiency=outcome.oos_is_efficiency,
        oos_consistency=outcome.oos_consistency,
        oos_win_windows=outcome.oos_win_windows,
        windows=windows,
    )


# ══════════════════════════════════════════════════════════════════
# C3 — 前视 / 递归偏差检测
# ══════════════════════════════════════════════════════════════════

class BiasCheckRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数（固定，不寻优）")
    startup_candles: list[int] = Field(
        default_factory=lambda: [50, 100, 200],
        description="递归检测使用的前端裁剪 bar 数",
    )
    lookahead_cut_ratio: float = Field(0.7, ge=0.3, le=0.95, description="前视检测保留前 x 比例数据")


class SignalDiffOut(BaseModel):
    checked_signals: int
    changed_signals: int
    detail: str


class RecursiveDiffOut(BaseModel):
    startup_candle: int
    checked_signals: int
    changed_signals: int


class BiasCheckResponse(BaseModel):
    has_lookahead_bias: bool
    has_recursive_bias: bool
    total_signals: int
    lookahead: SignalDiffOut
    recursive: list[RecursiveDiffOut]
    notes: list[str]


@router.post("/bias-check", response_model=BiasCheckResponse)
async def bias_check(
    body: BiasCheckRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> BiasCheckResponse:
    """前视/递归偏差检测：通过截断重跑对比成交序列，识别策略是否偷看未来数据。"""
    market, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, body.symbol, svc,
    )
    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]

    def run_fills(window_bars: list[Bar]) -> list[dict]:
        if len(window_bars) < 2:
            return []
        return _backtest_fills(strategy_cls, body.params, window_bars, market, body.initial_cash)

    try:
        outcome = await run_in_threadpool(
            run_bias_check,
            run_fills, bars, body.startup_candles, body.lookahead_cut_ratio,
        )
    except Exception as e:
        raise HTTPException(500, f"偏差检测引擎错误: {e}")

    return BiasCheckResponse(
        has_lookahead_bias=outcome.has_lookahead_bias,
        has_recursive_bias=outcome.has_recursive_bias,
        total_signals=outcome.total_signals,
        lookahead=SignalDiffOut(
            checked_signals=outcome.lookahead.checked_signals,
            changed_signals=outcome.lookahead.changed_signals,
            detail=outcome.lookahead.detail,
        ),
        recursive=[
            RecursiveDiffOut(
                startup_candle=r.startup_candle,
                checked_signals=r.checked_signals,
                changed_signals=r.changed_signals,
            )
            for r in outcome.recursive
        ],
        notes=outcome.notes,
    )
