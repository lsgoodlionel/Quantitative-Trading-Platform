"""
回测 API 端点

支持:
1. 同步回测 POST /backtests/run  — 立即返回完整结果
2. 参数优化 POST /backtests/optimize — 网格搜索最优参数
3. 蒙特卡洛 POST /backtests/montecarlo — 交易随机排列验证
4. 策略列表 GET  /backtests/strategies — (兼容旧路由)
"""

from __future__ import annotations

import random
import uuid
from datetime import date
from itertools import product
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Market, Frequency
from app.data.service import DataService
from app.engine.backtest.engine import BacktestEngine, BacktestConfig
from app.engine.backtest.report import _metrics_to_dict
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()


# ── A股支持的频率 ───────────────────────────────────────────────
_A_ALLOWED_FREQS = {Frequency.DAY_1, Frequency.WEEK_1}


# ── Schemas ──────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy_name: str = Field(..., description="策略名称，见 /strategies/presets")
    symbol: str = Field(..., description="标的代码，如 AAPL / 00700 / 000001")
    market: str = Field("US", description="市场：US / HK / A")
    frequency: str = Field("1d", description="K 线周期")
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数覆盖")


class BacktestMetricsResponse(BaseModel):
    # 收益
    total_return_pct: float
    annual_return_pct: float
    volatility_pct: float
    trading_days: int
    # 风险调整
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    omega_ratio: float
    # 回撤
    max_drawdown_pct: float
    max_drawdown_duration: int
    # 交易统计
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float
    avg_trade_return: float
    sqn: float
    # 连胜连败
    max_consecutive_wins: int
    max_consecutive_losses: int
    # 基准
    buy_hold_return_pct: float


class BacktestResponse(BaseModel):
    backtest_id: str
    strategy_name: str
    symbol: str
    market: str
    start_date: str
    end_date: str
    initial_cash: float
    final_value: float
    metrics: BacktestMetricsResponse
    equity_curve: list[dict]
    drawdown_series: list[dict]
    monthly_returns: dict
    pnl_distribution: list[dict]
    fills: list[dict]
    generated_at: str


class OptimizeRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    param_grid: dict = Field(
        ...,
        description="参数网格: {'short_window': [5,10,20], 'long_window': [50,100,200]}"
    )
    optimize_target: str = Field("sharpe_ratio", description="优化目标指标")
    max_combinations: int = Field(50, ge=1, le=200, description="最多计算组合数")


class OptimizeResult(BaseModel):
    best_params: dict
    best_score: float
    optimize_target: str
    total_combinations: int
    evaluated_combinations: int
    results: list[dict]   # [{params, score, total_return_pct, sharpe, max_drawdown_pct}]


class MonteCarloRequest(BaseModel):
    strategy_name: str
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict)
    n_simulations: int = Field(200, ge=50, le=1000, description="模拟次数")
    seed: int = Field(42)


class MonteCarloResponse(BaseModel):
    n_simulations: int
    original_return_pct: float
    original_sharpe: float
    original_max_drawdown_pct: float
    # 分位数统计
    p5_return_pct: float
    p25_return_pct: float
    p50_return_pct: float
    p75_return_pct: float
    p95_return_pct: float
    p5_sharpe: float
    p95_sharpe: float
    p5_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    # 概率
    prob_positive: float     # 正收益概率
    prob_beat_market: float  # 超越买入持有概率（若基准 > 0）
    # 净值曲线包络
    envelope: list[dict]     # [{time, p5, p25, p50, p75, p95}]


# ── 依赖注入 ─────────────────────────────────────────────────

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# ── 通用验证 & 数据加载 ──────────────────────────────────────

async def _validate_and_fetch(
    strategy_name: str,
    market_str: str,
    frequency_str: str,
    start_date: date,
    end_date: date,
    svc: DataService,
    symbol: str,
):
    if strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{strategy_name}'. Available: {list(STRATEGY_REGISTRY.keys())}",
        )
    try:
        market = Market(market_str.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid market '{market_str}'.")

    try:
        frequency = Frequency(frequency_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid frequency '{frequency_str}'.")

    # A股仅支持日线/周线
    if market == Market.A and frequency not in _A_ALLOWED_FREQS:
        raise HTTPException(
            status_code=400,
            detail=f"A股仅支持日线(1d)和周线(1w)，不支持: {frequency_str}",
        )

    try:
        bars = await svc.get_bars(
            symbol=symbol, market=market, frequency=frequency,
            start=start_date, end=end_date,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch bars: {e}")

    if len(bars) < 5:
        raise HTTPException(
            status_code=422,
            detail=f"数据不足: 仅获取到 {len(bars)} 根 K 线。请检查标的代码、市场和日期范围。",
        )

    return market, frequency, bars


# ── 端点 ─────────────────────────────────────────────────────

@router.post("/run", response_model=BacktestResponse)
async def run_backtest(
    body: BacktestRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> BacktestResponse:
    """同步执行回测，立即返回完整结果（含回撤序列、月度收益、交易分布）。"""
    market, frequency, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, svc, body.symbol,
    )

    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]
    strategy = strategy_cls(params=body.params)
    config = BacktestConfig(initial_cash=body.initial_cash, market=market)
    engine = BacktestEngine(config)
    backtest_id = str(uuid.uuid4())

    try:
        result = engine.run(strategy, bars, strategy_id=backtest_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest engine error: {e}")

    report = result.report
    return BacktestResponse(
        backtest_id=backtest_id,
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        market=body.market,
        start_date=body.start_date.isoformat(),
        end_date=body.end_date.isoformat(),
        initial_cash=body.initial_cash,
        final_value=result.final_value,
        metrics=BacktestMetricsResponse(**report["metrics"]),
        equity_curve=report["equity_curve"],
        drawdown_series=report.get("drawdown_series", []),
        monthly_returns=report.get("monthly_returns", {}),
        pnl_distribution=report.get("pnl_distribution", []),
        fills=report["fills"],
        generated_at=report["generated_at"],
    )


@router.post("/optimize", response_model=OptimizeResult)
async def optimize_strategy(
    body: OptimizeRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> OptimizeResult:
    """
    参数网格搜索优化。

    param_grid 格式: {"short_window": [5, 10, 20], "long_window": [50, 100, 200]}
    optimize_target: sharpe_ratio / total_return_pct / calmar_ratio / sqn
    """
    market, frequency, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, svc, body.symbol,
    )

    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]

    # 展开参数网格
    keys = list(body.param_grid.keys())
    values = list(body.param_grid.values())
    all_combinations = list(product(*values))
    total = len(all_combinations)

    # 随机采样（超出限制时）
    if total > body.max_combinations:
        random.shuffle(all_combinations)
        all_combinations = all_combinations[:body.max_combinations]

    config = BacktestConfig(initial_cash=body.initial_cash, market=market)
    engine = BacktestEngine(config)

    results: list[dict] = []
    best_score = float("-inf")
    best_params: dict = {}

    for combo in all_combinations:
        params = dict(zip(keys, combo))
        try:
            strategy = strategy_cls(params=params)
            result = engine.run(strategy, bars)
            metrics = result.report["metrics"]
            score = float(metrics.get(body.optimize_target, 0.0))
            results.append({
                "params": params,
                "score": round(score, 4),
                "total_return_pct": metrics["total_return_pct"],
                "annual_return_pct": metrics["annual_return_pct"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "total_trades": metrics["total_trades"],
            })
            if score > best_score:
                best_score = score
                best_params = params
        except Exception:
            continue

    results.sort(key=lambda r: r["score"], reverse=True)

    return OptimizeResult(
        best_params=best_params,
        best_score=round(best_score, 4),
        optimize_target=body.optimize_target,
        total_combinations=total,
        evaluated_combinations=len(results),
        results=results[:50],  # 返回前 50 个
    )


@router.post("/montecarlo", response_model=MonteCarloResponse)
async def montecarlo_backtest(
    body: MonteCarloRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> MonteCarloResponse:
    """
    蒙特卡洛验证：随机打乱成交顺序，评估策略统计显著性。

    参考: refs/jesse/jesse/services/monte_carlo.py
    方法: 保持每笔交易盈亏不变，随机排列成交序列，重建净值曲线。
    """
    market, frequency, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, svc, body.symbol,
    )

    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]
    strategy = strategy_cls(params=body.params)
    config = BacktestConfig(initial_cash=body.initial_cash, market=market)
    engine = BacktestEngine(config)

    # 原始回测
    try:
        original = engine.run(strategy, bars)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Original backtest failed: {e}")

    orig_metrics = original.report["metrics"]
    orig_fills = original.fills
    orig_equity = original.equity_curve

    if len(orig_fills) < 2:
        raise HTTPException(status_code=422, detail="交易次数不足，无法进行蒙特卡洛模拟（最少需要 2 笔）")

    # 提取成交盈亏序列
    sell_pnls = [f.get("realized_pnl", 0.0) for f in orig_fills if f.get("side") in ("SELL", "sell")]
    if not sell_pnls:
        raise HTTPException(status_code=422, detail="没有卖出成交记录，无法进行蒙特卡洛模拟")

    rng = np.random.default_rng(body.seed)
    n_sim = body.n_simulations

    # 重建净值曲线：初始资金 + 累加打乱后的 PnL
    sim_returns: list[float] = []
    sim_sharpes: list[float] = []
    sim_drawdowns: list[float] = []
    # 保存各模拟的净值序列用于包络图
    equity_matrix: list[np.ndarray] = []

    n_trades = len(sell_pnls)
    initial_cash = body.initial_cash

    for _ in range(n_sim):
        shuffled = rng.permutation(sell_pnls)
        # 用等间隔插值模拟净值变化
        equity = np.full(len(orig_equity), initial_cash, dtype=float)
        step = max(1, len(orig_equity) // max(n_trades, 1))
        cash = initial_cash
        for j, pnl in enumerate(shuffled):
            idx = min(j * step, len(equity) - 1)
            cash += pnl
            equity[idx:] += pnl
        equity_matrix.append(equity)

        total_ret = (equity[-1] - initial_cash) / initial_cash
        sim_returns.append(total_ret)

        # 简易 Sharpe (年化)
        daily_ret = np.diff(equity) / equity[:-1]
        std = float(np.std(daily_ret))
        mean = float(np.mean(daily_ret))
        ann_ret = (1 + total_ret) ** (252 / max(len(equity), 1)) - 1
        sharpe = ann_ret / (std * (252 ** 0.5)) if std > 1e-10 else 0.0
        sim_sharpes.append(sharpe)

        # 最大回撤
        running_max = np.maximum.accumulate(equity)
        dd = np.min((equity - running_max) / running_max)
        sim_drawdowns.append(float(dd))

    arr_ret = np.array(sim_returns)
    arr_sharpe = np.array(sim_sharpes)
    arr_dd = np.array(sim_drawdowns)

    # 包络图（取 5/25/50/75/95 分位）
    equity_matrix_np = np.array(equity_matrix)
    time_labels = [pt["time"] for pt in original.report["equity_curve"]]
    n_pts = len(time_labels)
    # 采样矩阵列数对齐
    if equity_matrix_np.shape[1] != n_pts:
        # 重采样
        from scipy.interpolate import interp1d
        new_x = np.linspace(0, 1, n_pts)
        old_x = np.linspace(0, 1, equity_matrix_np.shape[1])
        resampled = np.zeros((n_sim, n_pts))
        for i, row in enumerate(equity_matrix_np):
            f = interp1d(old_x, row, kind="linear", fill_value="extrapolate")
            resampled[i] = f(new_x)
        equity_matrix_np = resampled

    envelope = []
    for j in range(min(n_pts, 300)):   # 最多 300 点
        col = equity_matrix_np[:, j * (n_pts // min(n_pts, 300))]
        envelope.append({
            "time": time_labels[j * (n_pts // min(n_pts, 300))]["time"] if isinstance(time_labels[0], dict) else time_labels[j * (n_pts // min(n_pts, 300))],
            "p5":  round(float(np.percentile(col, 5)), 2),
            "p25": round(float(np.percentile(col, 25)), 2),
            "p50": round(float(np.percentile(col, 50)), 2),
            "p75": round(float(np.percentile(col, 75)), 2),
            "p95": round(float(np.percentile(col, 95)), 2),
        })

    orig_bh = orig_metrics.get("buy_hold_return_pct", 0.0) / 100.0
    prob_beat = float(np.mean(arr_ret > orig_bh)) if orig_bh != 0 else 0.0

    return MonteCarloResponse(
        n_simulations=n_sim,
        original_return_pct=orig_metrics["total_return_pct"],
        original_sharpe=orig_metrics["sharpe_ratio"],
        original_max_drawdown_pct=orig_metrics["max_drawdown_pct"],
        p5_return_pct=round(float(np.percentile(arr_ret, 5)) * 100, 2),
        p25_return_pct=round(float(np.percentile(arr_ret, 25)) * 100, 2),
        p50_return_pct=round(float(np.percentile(arr_ret, 50)) * 100, 2),
        p75_return_pct=round(float(np.percentile(arr_ret, 75)) * 100, 2),
        p95_return_pct=round(float(np.percentile(arr_ret, 95)) * 100, 2),
        p5_sharpe=round(float(np.percentile(arr_sharpe, 5)), 4),
        p95_sharpe=round(float(np.percentile(arr_sharpe, 95)), 4),
        p5_max_drawdown_pct=round(float(np.percentile(arr_dd, 5)) * 100, 2),
        p95_max_drawdown_pct=round(float(np.percentile(arr_dd, 95)) * 100, 2),
        prob_positive=round(float(np.mean(arr_ret > 0)), 4),
        prob_beat_market=round(prob_beat, 4),
        envelope=envelope,
    )


@router.get("/strategies")
async def list_strategies() -> list[dict]:
    """列出所有可用策略及其默认参数说明（兼容旧路由）。"""
    return [
        {
            "name": name,
            "description": cls.description if hasattr(cls, "description") else "",
        }
        for name, cls in STRATEGY_REGISTRY.items()
    ]
