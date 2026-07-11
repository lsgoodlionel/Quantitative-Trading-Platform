"""
Topk-Dropout 轮动组合 API 端点（Wave-3 / D6）

  POST /portfolio/topk  — 打分（内置横截面因子）→ 轮动组合回测

风格对齐 endpoints/factor_library.py：Pydantic v2 请求模型 + try/except →
HTTPException(400/422/503)，数据层复用 AsyncSessionLocal + DataService，
重型模块在 handler 内惰性 import。评分与再平衡逻辑在
app.engine.portfolio.topk_dropout 中实现，本端点只负责数据编排与序列化。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Portfolio Optimizer"])

# 单标的最少 bar 数
_MIN_BARS = 60
# 单个再平衡日最少可评分标的数
_MIN_NAMES = 3
# 结果最多返回的期数（避免 payload 过大）
_MAX_PERIODS = 200

ScoreMethod = Literal["momentum", "reversal", "vol_scaled_momentum"]


# ── 请求模型 ──────────────────────────────────────────────────────

class TopkRequest(BaseModel):
    symbols:       list[str] = Field(min_length=3, max_length=60)
    market:        Literal["US", "HK", "A"] = "US"
    frequency:     str = "1d"
    start:         str | None = None
    end:           str | None = None

    # 打分
    score_method:  ScoreMethod = "momentum"
    lookback:      int = Field(default=20, ge=2, le=252, description="打分回看窗口（bar）")

    # 轮动
    rebalance_days: int = Field(default=5, ge=1, le=60, description="每隔多少 bar 再平衡")
    topk:          int = Field(default=5, ge=1, le=50)
    n_drop:        int = Field(default=1, ge=0, le=50)
    hold_thresh:   int = Field(default=1, ge=1, le=60, description="最短持仓期（再平衡次数）")
    risk_degree:   float = Field(default=0.95, gt=0.0, le=1.0)
    method_sell:   Literal["bottom", "random"] = "bottom"
    method_buy:    Literal["top", "random"] = "top"


# ── 数据拉取（自包含，避免跨端点耦合）─────────────────────────────

async def _fetch_universe(req: TopkRequest) -> dict[str, list]:
    from datetime import date, timedelta

    from app.core.database import AsyncSessionLocal
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService

    end_date = date.fromisoformat(req.end) if req.end else date.today()
    start_date = date.fromisoformat(req.start) if req.start else end_date - timedelta(days=365 * 2)

    try:
        market_enum = MarketEnum(req.market)
        freq_enum = FreqEnum(req.frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    bars_by_symbol: dict[str, list] = {}
    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        for raw in req.symbols:
            sym = raw.strip().upper()
            if not sym:
                continue
            try:
                bars = await svc.get_bars(sym, market_enum, freq_enum, start_date, end_date)
            except Exception:
                continue                       # 单标的失败不作硬错误，剔除即可
            if len(bars) >= _MIN_BARS:
                bars_by_symbol[sym] = bars

    if len(bars_by_symbol) < _MIN_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"有效标的不足（需 ≥ {_MIN_NAMES}，实得 {len(bars_by_symbol)}；"
                   f"每标的需 ≥ {_MIN_BARS} 根 bar）",
        )
    return bars_by_symbol


# ── 面板与打分（惰性 import 重型库）───────────────────────────────

def _build_close_panel(bars_by_symbol: dict[str, list]):
    import pandas as pd

    frames = {sym: pd.Series({b.time: b.close for b in bars})
              for sym, bars in bars_by_symbol.items()}
    close = pd.DataFrame(frames).sort_index()
    return close.dropna(how="all")


def _score_panel(close, method: ScoreMethod, lookback: int):
    import numpy as np

    if method == "momentum":
        return close.pct_change(lookback)
    if method == "reversal":
        return -close.pct_change(lookback)
    if method == "vol_scaled_momentum":
        mom = close.pct_change(lookback)
        vol = close.pct_change().rolling(lookback).std().replace(0, np.nan)
        return mom / vol
    raise HTTPException(status_code=400, detail=f"未知打分方法: {method}")


def _select_rebalance(close, method: ScoreMethod, lookback: int, rebalance_days: int):
    """构造再平衡日对齐的 scores / prices 面板。"""
    scores = _score_panel(close, method, lookback)
    enough = scores.notna().sum(axis=1) >= _MIN_NAMES
    scores = scores[enough]
    if scores.shape[0] < 2:
        raise HTTPException(
            status_code=400,
            detail="有效再平衡期不足（增大数据区间或减小回看窗口 lookback）",
        )
    rb_index = scores.index[::rebalance_days]
    if len(rb_index) < 2:
        raise HTTPException(status_code=400, detail="再平衡期数不足（减小 rebalance_days）")
    return scores.loc[rb_index], close.loc[rb_index]


# ── 端点 ──────────────────────────────────────────────────────────

@router.post("/topk")
async def build_topk_portfolio(req: TopkRequest) -> dict:
    """
    横截面打分 → Topk-Dropout 轮动组合回测。

    内置 3 种打分因子（动量 / 反转 / 波动缩放动量），逐再平衡日选出 topK 持仓，
    每期最多剔除 n_drop 只、控制换手，返回持仓时间线、换手率、净值曲线与绩效。
    """
    from app.engine.portfolio.topk_dropout import (
        TopkConfig, run_topk_dropout,
    )

    bars_by_symbol = await _fetch_universe(req)
    close = _build_close_panel(bars_by_symbol)
    scores, prices = _select_rebalance(close, req.score_method, req.lookback, req.rebalance_days)

    try:
        cfg = TopkConfig(
            topk=req.topk,
            n_drop=req.n_drop,
            hold_thresh=req.hold_thresh,
            risk_degree=req.risk_degree,
            method_sell=req.method_sell,
            method_buy=req.method_buy,
        )
        result = run_topk_dropout(scores, prices, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Topk 组合构建失败: {e}") from e

    periods = result.periods[-_MAX_PERIODS:]
    return {
        "symbols": list(bars_by_symbol.keys()),
        "market": req.market,
        "score_method": req.score_method,
        "lookback": req.lookback,
        "rebalance_days": req.rebalance_days,
        "topk": result.topk,
        "n_drop": result.n_drop,
        "hold_thresh": result.hold_thresh,
        "risk_degree": result.risk_degree,
        "method_sell": result.method_sell,
        "method_buy": result.method_buy,
        "n_periods": result.n_periods,
        "metrics": result.metrics,
        "equity_curve": result.equity_curve[-_MAX_PERIODS:],
        "periods": [
            {
                "date": p.date,
                "holdings": p.holdings,
                "weights": p.weights,
                "buys": p.buys,
                "sells": p.sells,
                "turnover": p.turnover,
                "n_holdings": p.n_holdings,
                "period_return": p.period_return,
                "equity": p.equity,
            }
            for p in periods
        ],
    }
