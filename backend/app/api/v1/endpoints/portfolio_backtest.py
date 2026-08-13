"""组合回测端点（Wave K-c 遗留项）

把 `PortfolioBacktestEngine` 暴露成 HTTP 接口。与既有 `/backtests/run` 的区别：

| | `/backtests/run` | 本端点 |
|---|---|---|
| 标的 | 单个 | **多个，共享同一份资金** |
| 策略 | 直接跑 `StrategyBase` | preset 经 `LegacyStrategyAlphaAdapter` 包成组合策略 |
| 返回 | 单标的指标 | 组合指标 + **逐标的归因** + **日度盈亏拆解** |

**为什么不接受 `bars_by_symbol`**：契约初稿设想请求体直接传 bar 序列，
但 50 标的 × 3 年日线是几十万个数字，走 HTTP 既慢又没必要 ——
服务端本来就能取数。这里沿用 `/backtests/run` 的 `symbols + 区间` 形态。

**组合语义**：N 个标的**共用一份资金、互相竞争仓位**，这才是「组合回测」。
如果只是想看同一策略在 N 个标的上各自的表现，那是 N 次单标的回测，
走 `/backtests/batch`（Wave A-c）而不是这里。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Frequency, Market
from app.data.service import DataService
from app.engine.backtest.capacity import build_capacity_section
from app.engine.backtest.crisis import build_crisis_section
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.reject_reasons import build_rejected_signal_section
from app.engine.backtest.report import metrics_to_dict
from app.engine.framework import (
    EqualWeightingPCM,
    FrameworkStrategy,
    ImmediateExecutionModel,
    InsightWeightingPCM,
    LegacyStrategyAlphaAdapter,
)
from app.strategy.resolver import available_strategies

logger = logging.getLogger(__name__)

router = APIRouter()


def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)

#: 单次组合回测的标的数上限。50 标的 × 750 时点实测 ~1.1s，
#: 但取数是逐标的串行的，标的越多等待越久 —— 与 /backtests/batch 取同一上限。
MAX_PORTFOLIO_SYMBOLS = 50

#: 净值曲线与日度盈亏的返回上限（尾部截断），避免分钟级回测把响应撑爆
MAX_CURVE_POINTS = 2000
MAX_DAILY_ROWS = 2000
MAX_FILLS = 1000

#: A股仅支持日线/周线（与 /backtests/run 同一约束）
_A_ALLOWED_FREQS = (Frequency.DAY_1, Frequency.WEEK_1)

#: 组合构建方式 → PCM。刻意只暴露两种不需要协方差估计的：
#: 优化器类方法有「≥60 根 bar」门槛，且已由 /factors/strategy/backtest 覆盖。
_PCM_BUILDERS = {
    "equal_weight": EqualWeightingPCM,
    "insight_weight": InsightWeightingPCM,
}


class PortfolioBacktestRequest(BaseModel):
    strategy_name: str = Field(..., description="策略名称，见 /strategies/presets")
    symbols: list[str] = Field(..., min_length=2, description="标的列表（至少 2 个）")
    market: str = Field("US", description="市场：US / HK / A")
    frequency: str = Field("1d", description="K 线周期")
    start_date: date
    end_date: date
    initial_cash: float = Field(1_000_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数覆盖")
    portfolio_method: str = Field("equal_weight", description="equal_weight / insight_weight")
    max_open_positions: int | None = Field(None, ge=1, description="同时持仓上限")

    @field_validator("symbols")
    @classmethod
    def _dedupe_and_cap(cls, v: list[str]) -> list[str]:
        # 去重后仍要保证 ≥2：传 ["AAPL","AAPL"] 本质是单标的，不该走组合路径
        seen = sorted({s.strip().upper() for s in v if s.strip()})
        if len(seen) < 2:
            raise ValueError("去重后至少需要 2 个不同标的；单标的请用 /backtests/run")
        if len(seen) > MAX_PORTFOLIO_SYMBOLS:
            raise ValueError(
                f"标的数 {len(seen)} 超过上限 {MAX_PORTFOLIO_SYMBOLS}"
            )
        return seen


def _resolve_market_and_frequency(body: PortfolioBacktestRequest) -> tuple[Market, Frequency]:
    if body.strategy_name not in available_strategies():
        raise HTTPException(
            status_code=400,
            detail=f"未知策略 '{body.strategy_name}'，可选：{list(available_strategies())}",
        )
    if body.portfolio_method not in _PCM_BUILDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"未知组合构建方式 '{body.portfolio_method}'，"
                f"可选：{list(_PCM_BUILDERS)}；优化器类方法请用 /factors/strategy/backtest"
            ),
        )
    try:
        market = Market(body.market.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效市场 '{body.market}'") from None
    try:
        frequency = Frequency(body.frequency)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效周期 '{body.frequency}'") from None

    if market == Market.A and frequency not in _A_ALLOWED_FREQS:
        raise HTTPException(
            status_code=400, detail=f"A股仅支持日线(1d)和周线(1w)，不支持: {body.frequency}"
        )
    if body.start_date >= body.end_date:
        raise HTTPException(status_code=400, detail="start_date 必须早于 end_date")
    return market, frequency


async def _fetch_universe(
    body: PortfolioBacktestRequest, market: Market, frequency: Frequency, svc: DataService
) -> tuple[dict[str, list], list[str]]:
    """逐标的取数。取不到的标的**跳过并告警**，而不是让整个回测失败。"""
    bars_by_symbol: dict[str, list] = {}
    warnings: list[str] = []

    for symbol in body.symbols:
        try:
            bars = await svc.get_bars(
                symbol=symbol, market=market, frequency=frequency,
                start=body.start_date, end=body.end_date,
            )
        except Exception as e:  # noqa: BLE001 — 单个标的取数失败不该毁掉整个组合
            logger.warning("组合回测取数失败 %s: %s", symbol, e)
            warnings.append(f"{symbol}: 取数失败（{e}）")
            continue
        if len(bars) < 2:
            warnings.append(f"{symbol}: 区间内 bar 不足（{len(bars)} 根），已跳过")
            continue
        bars_by_symbol[symbol] = bars

    if len(bars_by_symbol) < 2:
        raise HTTPException(
            status_code=422,
            detail=f"可用标的不足 2 个，无法做组合回测。详情：{warnings or '无数据'}",
        )
    return bars_by_symbol, warnings


def _build_strategy(body: PortfolioBacktestRequest) -> FrameworkStrategy:
    """preset → 组合策略。

    传**类**而非实例：preset 普遍在 `self` 上存状态（网格、配对的滚动统计），
    共用一个实例会让不同标的的状态互相污染（见 alpha.py 的说明）。
    """
    return FrameworkStrategy(
        alpha=LegacyStrategyAlphaAdapter(
            available_strategies()[body.strategy_name], body.params or None
        ),
        portfolio_construction=_PCM_BUILDERS[body.portfolio_method](),
        execution=ImmediateExecutionModel(),
    )


def _run(body: PortfolioBacktestRequest, market: Market, bars_by_symbol: dict, run_id: str):
    config = PortfolioBacktestConfig(
        initial_cash=body.initial_cash,
        market=market,
        max_open_positions=body.max_open_positions,
    )
    return PortfolioBacktestEngine(config).run(
        _build_strategy(body), bars_by_symbol, strategy_id=run_id
    )


def _daily_to_dict(d) -> dict:
    """日度盈亏 → JSON。

    刻意不展开 `contracts[*].trades`：逐笔成交已经在响应的 `fills` 字段里了，
    在这里再嵌一份只会把响应体撑大好几倍。逐标的当日盈亏仍然保留。
    """
    return {
        "date": d.date.isoformat(),
        "trade_count": d.trade_count,
        "turnover": round(d.turnover, 4),
        "commission": round(d.commission, 4),
        "slippage": round(d.slippage, 4),
        "trading_pnl": round(d.trading_pnl, 4),
        "holding_pnl": round(d.holding_pnl, 4),
        "total_pnl": round(d.total_pnl, 4),
        "net_pnl": round(d.net_pnl, 4),
        "other_cash_flow": round(d.other_cash_flow, 4),
        "contracts": {
            symbol: {
                "close_price": round(c.close_price, 4),
                "end_pos": c.end_pos,
                "trade_count": c.trade_count,
                "net_pnl": round(c.net_pnl, 4),
            }
            for symbol, c in d.contracts.items()
        },
    }


def _wave_na_sections(result, bars_by_symbol: dict, market: Market) -> dict:
    """N-a 三个可空 section。

    刻意直接调三个 builder 而不是 `build_extended_sections` —— 后者会连带算完
    整套 C6/C7 tearsheet，而这个端点有意不做那件事（组合回测的 tearsheet 走
    `/backtests/report`）。这里只付 N-a 的钱。
    """
    return {
        "capacity_analysis": build_capacity_section(
            result.daily_results,
            result.equity_curve,
            bars_by_symbol,
            reference_equity=result.initial_cash,
        ),
        "crisis_windows": build_crisis_section(result.equity_curve, market.value),
        "rejected_signals": build_rejected_signal_section(
            result.rejections, overflow=result.rejection_overflow
        ),
    }


def _serialize(
    result, run_id: str, warnings: list[str], bars_by_symbol: dict, market: Market
) -> dict:
    curve = result.equity_curve.tail(MAX_CURVE_POINTS)
    return {
        "backtest_id": run_id,
        "strategy_name": result.strategy_name,
        "symbols": result.symbols,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "initial_cash": result.initial_cash,
        "final_value": round(result.final_value, 4),
        "metrics": metrics_to_dict(result.metrics),
        "equity_curve": [
            {"date": ts.isoformat(), "value": round(float(v), 4)} for ts, v in curve.items()
        ],
        # ── 组合回测独有的两块，K-c 专为归因（N2/N4）而建 ──
        "per_symbol_metrics": {
            symbol: metrics_to_dict(m) for symbol, m in result.per_symbol_metrics.items()
        },
        "daily_results": [_daily_to_dict(d) for d in result.daily_results[-MAX_DAILY_ROWS:]],
        "n_fills": len(result.fills),
        "fills": result.fills[-MAX_FILLS:],
        "warnings": warnings,
        # ── Wave N-a：容量·换手·杠杆 / 危机区间 / 拒绝信号（均可为 None）──
        **_wave_na_sections(result, bars_by_symbol, market),
    }


@router.post("/portfolio")
async def run_portfolio_backtest(
    body: PortfolioBacktestRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> dict:
    """多标的组合回测：N 个标的共享一份资金，返回组合指标 + 逐标的归因 + 日度盈亏。"""
    market, frequency = _resolve_market_and_frequency(body)
    bars_by_symbol, warnings = await _fetch_universe(body, market, frequency, svc)
    run_id = str(uuid.uuid4())

    loop = asyncio.get_event_loop()
    try:
        # 引擎是纯 CPU 的同步代码，丢线程池避免阻塞事件循环
        result = await loop.run_in_executor(None, _run, body, market, bars_by_symbol, run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"组合回测引擎错误: {e}") from e

    return _serialize(result, run_id, warnings, bars_by_symbol, market)
