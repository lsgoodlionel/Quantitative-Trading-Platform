"""
组合优化引擎

实现三种优化方法:
1. MVO (均值-方差优化)  — 最大化 Sharpe 比率
2. Risk Parity (风险平价) — 均等化各资产风险贡献
3. Min CVaR — 最小化条件风险价值 (CVaR / Expected Shortfall)

参考:
- PyPortfolioOpt 实现思路
- refs/PyPortfolioOpt/pypfopt/efficient_frontier.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize, OptimizeResult

from app.engine.portfolio.expected_returns import ReturnsModel, expected_returns
from app.engine.portfolio.risk_models import RiskModel, risk_matrix
from app.engine.portfolio.black_litterman import (
    BLResult,
    InvestorView,
    black_litterman,
)
from app.engine.portfolio.hrp import hrp_weights
from app.engine.portfolio.cvar_opt import (
    DEFAULT_BETA,
    min_cdar_weights,
    min_cvar_weights,
)

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.05  # 年化无风险利率
MIN_WEIGHT = 0.0       # 最低仓位
MAX_WEIGHT = 1.0       # 最高仓位
TRADING_DAYS = 252


class OptimizeMethod(str, Enum):
    MAX_SHARPE = "max_sharpe"
    MIN_VOLATILITY = "min_volatility"
    RISK_PARITY = "risk_parity"
    MIN_CVAR = "min_cvar"
    EQUAL_WEIGHT = "equal_weight"
    # ── v2.0 Wave 2 (D3/D4/D5) ──
    HRP = "hrp"                       # 层次风险平价
    BLACK_LITTERMAN = "black_litterman"  # 市场均衡 + 投资者观点
    MIN_CDAR = "min_cdar"             # 最小条件回撤风险


@dataclass
class PortfolioOptResult:
    method: str
    weights: dict[str, float]                # symbol → weight
    expected_return: float                    # 年化期望收益率 %
    expected_volatility: float                # 年化波动率 %
    sharpe_ratio: float
    cvar_95: float                            # 95% CVaR %
    # 有效前沿点（仅 max_sharpe / min_vol 提供）
    frontier: list[dict] = field(default_factory=list)   # [{vol, ret, sharpe}]
    # 各资产风险贡献（风险平价时计算）
    risk_contributions: dict[str, float] = field(default_factory=dict)
    # 回显所用估计器（供 UI 展示）
    risk_model: str = RiskModel.SAMPLE_COV.value
    expected_returns_method: str = ReturnsModel.MEAN_HISTORICAL.value
    # ── Black-Litterman 专属回显（其它方法为空/None）──
    bl_prior_returns: dict[str, float] = field(default_factory=dict)      # 市场隐含先验（%）
    bl_posterior_returns: dict[str, float] = field(default_factory=dict)  # 融合后验（%）
    bl_risk_aversion: Optional[float] = None                              # δ
    bl_views: list[str] = field(default_factory=list)                    # 观点文本
    # ── HRP / CVaR 元信息 ──
    linkage_method: Optional[str] = None       # HRP 聚类连接方式
    cvar_beta: Optional[float] = None          # CVaR/CDaR 置信水平


# ── 核心数学函数 ───────────────────────────────────────────────

def _annual_stats(
    weights: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
) -> tuple[float, float, float]:
    """返回 (年化收益, 年化波动, Sharpe)。"""
    ret = float(weights @ mu)
    vol = float(np.sqrt(weights @ cov @ weights))
    sharpe = (ret - RISK_FREE_RATE) / vol if vol > 1e-10 else 0.0
    return ret, vol, sharpe


def _cvar_95(weights: np.ndarray, returns_matrix: np.ndarray) -> float:
    """计算投资组合日收益的 95% CVaR（历史模拟）。"""
    portfolio_returns = returns_matrix @ weights
    var_95 = np.percentile(portfolio_returns, 5)
    cvar = float(portfolio_returns[portfolio_returns <= var_95].mean())
    return cvar * np.sqrt(TRADING_DAYS) * 100  # 年化百分比


# ── 优化方法 ──────────────────────────────────────────────────

def _max_sharpe(
    mu: np.ndarray,
    cov: np.ndarray,
    n: int,
) -> np.ndarray:
    """最大化 Sharpe 比率（负 Sharpe 最小化）。"""
    def neg_sharpe(w: np.ndarray) -> float:
        _, _, s = _annual_stats(w, mu, cov)
        return -s

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(MIN_WEIGHT, MAX_WEIGHT)] * n
    w0 = np.ones(n) / n

    result: OptimizeResult = minimize(
        neg_sharpe, w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    return result.x


def _min_volatility(
    cov: np.ndarray,
    n: int,
) -> np.ndarray:
    """最小化组合方差。"""
    def portfolio_vol(w: np.ndarray) -> float:
        return float(np.sqrt(w @ cov @ w))

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(MIN_WEIGHT, MAX_WEIGHT)] * n
    w0 = np.ones(n) / n

    result: OptimizeResult = minimize(
        portfolio_vol, w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    return result.x


def _risk_parity(
    cov: np.ndarray,
    n: int,
) -> np.ndarray:
    """
    风险平价：最小化各资产风险贡献方差。
    目标: min Σ (RC_i - RC_avg)²
    """
    def risk_parity_obj(w: np.ndarray) -> float:
        sigma = float(np.sqrt(w @ cov @ w))
        if sigma < 1e-10:
            return 1e10
        mrc = (cov @ w) / sigma          # 边际风险贡献
        rc = w * mrc                      # 风险贡献
        rc_avg = rc.mean()
        return float(((rc - rc_avg) ** 2).sum())

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(1e-4, MAX_WEIGHT)] * n    # 风险平价不允许零权重
    w0 = np.ones(n) / n

    result: OptimizeResult = minimize(
        risk_parity_obj, w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    return result.x


# ── 有效前沿 ──────────────────────────────────────────────────

def _compute_frontier(
    mu: np.ndarray,
    cov: np.ndarray,
    n: int,
    n_points: int = 40,
) -> list[dict]:
    """
    沿目标收益扫描有效前沿。
    返回 [{vol, ret, sharpe}, …] 按 vol 升序。
    """
    min_ret = float(mu.min())
    max_ret = float(mu.max())
    target_returns = np.linspace(min_ret, max_ret, n_points)
    frontier = []

    for target in target_returns:
        def vol_obj(w: np.ndarray) -> float:
            return float(np.sqrt(w @ cov @ w))

        constraints = [
            {"type": "eq", "fun": lambda w: w.sum() - 1.0},
            {"type": "eq", "fun": lambda w, t=target: w @ mu - t},
        ]
        bounds = [(MIN_WEIGHT, MAX_WEIGHT)] * n
        w0 = np.ones(n) / n

        try:
            res: OptimizeResult = minimize(
                vol_obj, w0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-10, "maxiter": 500},
            )
            if res.success:
                vol = float(np.sqrt(res.x @ cov @ res.x))
                ret = float(res.x @ mu)
                sharpe = (ret - RISK_FREE_RATE) / vol if vol > 1e-10 else 0.0
                frontier.append({
                    "vol": round(vol * 100, 2),
                    "ret": round(ret * 100, 2),
                    "sharpe": round(sharpe, 3),
                })
        except Exception:
            continue

    frontier.sort(key=lambda x: x["vol"])
    return frontier


# ── Black-Litterman 分支 ─────────────────────────────────────

def _run_black_litterman(
    prices: pd.DataFrame,
    cov_df: pd.DataFrame,
    views: list[InvestorView],
    *,
    market_caps: dict[str, float] | None,
    risk_aversion: float | None,
    tau: float,
) -> tuple[np.ndarray, np.ndarray, BLResult]:
    """执行 BL 融合，返回 (后验收益向量, 后验协方差矩阵, BLResult)。"""
    symbols = list(prices.columns)
    bl = black_litterman(
        cov_df.reindex(index=symbols, columns=symbols),
        views,
        market_caps=market_caps,
        risk_aversion=risk_aversion,
        tau=tau,
    )
    post_mu = bl.posterior_returns.reindex(symbols).to_numpy()
    post_cov = bl.posterior_cov.reindex(index=symbols, columns=symbols).to_numpy()
    return post_mu, post_cov, bl


# ── 公共入口 ──────────────────────────────────────────────────

def optimize_portfolio(
    prices: pd.DataFrame,
    method: OptimizeMethod = OptimizeMethod.MAX_SHARPE,
    include_frontier: bool = True,
    *,
    risk_model: RiskModel = RiskModel.SAMPLE_COV,
    expected_returns_method: ReturnsModel = ReturnsModel.MEAN_HISTORICAL,
    views: list[InvestorView] | None = None,
    market_caps: dict[str, float] | None = None,
    bl_risk_aversion: float | None = None,
    bl_tau: float = 0.05,
    linkage_method: str = "single",
    cvar_beta: float = DEFAULT_BETA,
) -> PortfolioOptResult:
    """
    对价格 DataFrame 进行组合优化（支持 D3/D4/D5 新方法，向后兼容）。

    Args:
        prices: 收盘价 DataFrame，index=日期, columns=symbol
        method: 优化方法
        include_frontier: 是否计算有效前沿
        risk_model: 协方差估计器（默认样本协方差，向后兼容）
        expected_returns_method: 预期收益估计器（默认历史均值，向后兼容）
        views: Black-Litterman 投资者观点（仅 black_litterman 使用）
        market_caps: 市值字典（BL 先验；缺省用等权代理）
        bl_risk_aversion: BL 风险厌恶 δ（缺省 2.5）
        bl_tau: BL 先验不确定性缩放
        linkage_method: HRP 层次聚类连接方式
        cvar_beta: CVaR/CDaR 置信水平（默认 0.95）

    Returns:
        PortfolioOptResult
    """
    symbols = list(prices.columns)
    n = len(symbols)

    if n < 2:
        raise ValueError("至少需要 2 个资产才能优化")
    if len(prices) < 60:
        raise ValueError("至少需要 60 个交易日数据")

    returns = prices.pct_change().dropna()
    returns_matrix = returns.values

    # 年化统计量（可插拔估计器，输出已年化）
    mu_series = expected_returns(prices, expected_returns_method)
    cov_df = risk_matrix(prices, risk_model)
    mu = mu_series.reindex(symbols).to_numpy()
    cov = cov_df.reindex(index=symbols, columns=symbols).to_numpy()

    bl: BLResult | None = None

    # 根据方法优化（stats 用的 mu/cov 可能被 BL 后验替换）
    if method == OptimizeMethod.MAX_SHARPE:
        weights = _max_sharpe(mu, cov, n)
        frontier = _compute_frontier(mu, cov, n) if include_frontier else []
    elif method == OptimizeMethod.MIN_VOLATILITY:
        weights = _min_volatility(cov, n)
        frontier = _compute_frontier(mu, cov, n) if include_frontier else []
    elif method == OptimizeMethod.RISK_PARITY:
        weights = _risk_parity(cov, n)
        frontier = []
    elif method == OptimizeMethod.MIN_CVAR:
        weights = min_cvar_weights(returns_matrix, beta=cvar_beta)
        frontier = []
    elif method == OptimizeMethod.MIN_CDAR:
        weights = min_cdar_weights(returns_matrix, beta=cvar_beta)
        frontier = []
    elif method == OptimizeMethod.HRP:
        weights = hrp_weights(
            returns, cov=cov_df, linkage_method=linkage_method
        ).reindex(symbols).to_numpy()
        frontier = []
    elif method == OptimizeMethod.BLACK_LITTERMAN:
        if not views:
            raise ValueError("black_litterman 方法需要至少 1 条投资者观点（views）")
        mu, cov, bl = _run_black_litterman(
            prices, cov_df, views,
            market_caps=market_caps,
            risk_aversion=bl_risk_aversion,
            tau=bl_tau,
        )
        weights = _max_sharpe(mu, cov, n)
        frontier = _compute_frontier(mu, cov, n) if include_frontier else []
    else:  # EQUAL_WEIGHT
        weights = np.ones(n) / n
        frontier = _compute_frontier(mu, cov, n) if include_frontier else []

    # 清理权重（数值噪声）
    weights = np.clip(weights, 0, 1)
    weights /= weights.sum()

    ret, vol, sharpe = _annual_stats(weights, mu, cov)
    cvar_val = _cvar_95(weights, returns_matrix)

    # 风险贡献
    sigma = float(np.sqrt(weights @ cov @ weights))
    if sigma > 1e-10:
        mrc = (cov @ weights) / sigma
        rc = weights * mrc
        rc_dict = {sym: round(float(rc[i] / sigma * 100), 2) for i, sym in enumerate(symbols)}
    else:
        rc_dict = {sym: round(100 / n, 2) for sym in symbols}

    weights_dict = {
        sym: round(float(weights[i]), 4)
        for i, sym in enumerate(symbols)
    }

    result = PortfolioOptResult(
        method=method.value,
        weights=weights_dict,
        expected_return=round(ret * 100, 2),
        expected_volatility=round(vol * 100, 2),
        sharpe_ratio=round(sharpe, 3),
        cvar_95=round(cvar_val, 2),
        frontier=frontier,
        risk_contributions=rc_dict,
        risk_model=(
            risk_model.value if isinstance(risk_model, RiskModel) else str(risk_model)
        ),
        expected_returns_method=(
            expected_returns_method.value
            if isinstance(expected_returns_method, ReturnsModel)
            else str(expected_returns_method)
        ),
    )

    # 方法专属元信息回显
    if method == OptimizeMethod.HRP:
        result.linkage_method = linkage_method
    elif method in (OptimizeMethod.MIN_CVAR, OptimizeMethod.MIN_CDAR):
        result.cvar_beta = round(float(cvar_beta), 4)
    elif method == OptimizeMethod.BLACK_LITTERMAN and bl is not None:
        result.bl_prior_returns = {
            s: round(float(v) * 100, 2) for s, v in bl.prior_returns.items()
        }
        result.bl_posterior_returns = {
            s: round(float(v) * 100, 2) for s, v in bl.posterior_returns.items()
        }
        result.bl_risk_aversion = bl.risk_aversion
        result.bl_views = bl.view_labels

    return result
