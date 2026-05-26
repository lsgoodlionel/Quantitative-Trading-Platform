"""
量化算法 API 端点

提供 10 种高阶量化算法的 REST 接口，全部为 POST（参数较多）。

端点列表:
  POST /quant/gbm          — GBM Monte Carlo 模拟
  POST /quant/bsm          — BSM 期权定价 + Greeks
  POST /quant/garch        — GARCH(1,1) 波动率拟合与预测
  POST /quant/kelly        — 凯利准则仓位计算
  POST /quant/cointegration — 协整检验与统计套利信号
  POST /quant/pca          — PCA 因子风险分解
  POST /quant/hmm          — HMM 市场状态识别
  POST /quant/copula       — Copula 尾部相关性分析
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.quant.bsm import price_option
from app.quant.cointegration import analyze_cointegration
from app.quant.copula import analyze_copula
from app.quant.gbm import simulate_gbm
from app.quant.garch import fit_garch11
from app.quant.hmm_regime import fit_hmm
from app.quant.kelly import compute_kelly
from app.quant.pca_factor import analyze_pca

router = APIRouter(tags=["Quant Algorithms"])


# ── 请求模型 ──────────────────────────────────────────────────────

class GBMRequest(BaseModel):
    S0: float = Field(gt=0, description="当前价格")
    mu: float = Field(description="年化漂移率（如 0.10=10%）")
    sigma: float = Field(gt=0, description="年化波动率（如 0.20=20%）")
    T: float = Field(gt=0, le=10, description="时间跨度（年）")
    n_paths: int = Field(default=1000, ge=100, le=10000, description="模拟路径数")
    n_steps: int = Field(default=252, ge=10, le=2520, description="时间步数")
    seed: int = Field(default=42, description="随机种子（-1=不固定）")


class BSMRequest(BaseModel):
    S: float = Field(gt=0, description="标的现价")
    K: float = Field(gt=0, description="行权价")
    r: float = Field(description="无风险年化利率（如 0.05=5%）")
    sigma: float = Field(gt=0, description="年化波动率（如 0.20=20%）")
    T: float = Field(gt=0, description="到期年数（如 0.25=3个月）")
    q: float = Field(default=0.0, ge=0, description="连续股息率")
    option_type: str = Field(default="call", pattern="^(call|put)$")


class GARCHRequest(BaseModel):
    returns: list[float] = Field(min_length=30, description="日收益率序列（小数）")
    forecast_horizon: int = Field(default=30, ge=1, le=252, description="预测步数")


class KellyRequest(BaseModel):
    win_rate: float = Field(gt=0, lt=1, description="胜率（如 0.55=55%）")
    avg_win: float = Field(gt=0, description="平均盈利金额")
    avg_loss: float = Field(gt=0, description="平均亏损金额")
    fraction: float = Field(default=0.5, gt=0, le=1, description="分数凯利比例")
    max_f: float = Field(default=0.25, gt=0, le=1, description="最大仓位上限")


class CointegrationRequest(BaseModel):
    y: list[float] = Field(min_length=30, description="价格序列1（因变量）")
    x: list[float] = Field(min_length=30, description="价格序列2（自变量）")
    lookback: int = Field(default=60, ge=10, le=500, description="滚动Z-score窗口")
    entry_z: float = Field(default=2.0, gt=0, description="开仓Z-score阈值")
    exit_z: float = Field(default=0.5, ge=0, description="平仓Z-score阈值")
    use_log: bool = Field(default=True, description="是否对数处理价格")


class PCARequest(BaseModel):
    returns_matrix: list[list[float]] = Field(
        description="收益率矩阵，shape=(T, N)，T=时间点数，N=资产数"
    )
    asset_names: list[str] | None = Field(default=None, description="资产名称列表")
    n_components: int | None = Field(default=None, ge=1, description="主成分数量")


class HMMRequest(BaseModel):
    returns: list[float] = Field(min_length=20, description="收益率序列")
    n_states: int = Field(default=2, ge=2, le=5, description="状态数")
    n_iterations: int = Field(default=100, ge=10, le=500, description="EM最大迭代次数")


class CopulaRequest(BaseModel):
    returns_x: list[float] = Field(min_length=30, description="资产1收益率")
    returns_y: list[float] = Field(min_length=30, description="资产2收益率")
    copula_type: str = Field(default="gaussian", pattern="^(gaussian|t)$")
    tail_q: float = Field(default=0.05, gt=0, lt=0.5, description="尾部分位数阈值")


# ── 端点实现 ──────────────────────────────────────────────────────

@router.post("/gbm")
async def run_gbm(req: GBMRequest) -> dict:
    """几何布朗运动 Monte Carlo 模拟（用于价格区间预测、VaR 计算）。"""
    try:
        result = simulate_gbm(
            S0=req.S0, mu=req.mu, sigma=req.sigma,
            T=req.T, n_paths=req.n_paths, n_steps=req.n_steps, seed=req.seed,
        )
        # 返回紧凑格式（sample_paths 限制为 50 条，避免响应过大）
        d = result.__dict__.copy()
        d["sample_paths"] = d["sample_paths"][:50]
        return d
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/bsm")
async def run_bsm(req: BSMRequest) -> dict:
    """Black-Scholes-Merton 期权定价 + Greeks。"""
    try:
        result = price_option(
            S=req.S, K=req.K, r=req.r, sigma=req.sigma,
            T=req.T, q=req.q, option_type=req.option_type,
        )
        return result.__dict__
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/garch")
async def run_garch(req: GARCHRequest) -> dict:
    """GARCH(1,1) 波动率模型拟合与预测。"""
    try:
        result = fit_garch11(returns=req.returns, forecast_horizon=req.forecast_horizon)
        # conditional_vol 可能很长，裁剪到最后 252 个
        d = result.__dict__.copy()
        d["conditional_vol"] = d["conditional_vol"][-252:]
        return d
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/kelly")
async def run_kelly(req: KellyRequest) -> dict:
    """凯利准则仓位优化（单资产，分数凯利推荐）。"""
    try:
        result = compute_kelly(
            win_rate=req.win_rate, avg_win=req.avg_win, avg_loss=req.avg_loss,
            fraction=req.fraction, max_f=req.max_f,
        )
        return result.__dict__
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/cointegration")
async def run_cointegration(req: CointegrationRequest) -> dict:
    """Engle-Granger 协整检验与配对交易信号生成。"""
    try:
        result = analyze_cointegration(
            y=req.y, x=req.x, lookback=req.lookback,
            entry_z=req.entry_z, exit_z=req.exit_z, use_log=req.use_log,
        )
        d = result.__dict__.copy()
        # 序列太长时裁剪用于绘图
        d["spread_series"] = d["spread_series"][-500:]
        d["z_score_series"] = d["z_score_series"][-500:]
        return d
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/pca")
async def run_pca(req: PCARequest) -> dict:
    """PCA 主成分分析 — 多资产因子风险分解。"""
    try:
        result = analyze_pca(
            returns_matrix=req.returns_matrix,
            asset_names=req.asset_names,
            n_components=req.n_components,
        )
        # factor_returns 可能很大，裁剪
        d = result.__dict__.copy()
        d["factor_returns"] = d["factor_returns"][-252:]
        return d
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/hmm")
async def run_hmm(req: HMMRequest) -> dict:
    """隐马尔可夫模型 (HMM) 市场状态识别（牛市/熊市/震荡）。"""
    try:
        result = fit_hmm(
            returns=req.returns,
            n_states=req.n_states,
            n_iterations=req.n_iterations,
        )
        d = result.__dict__.copy()
        d["state_sequence"] = d["state_sequence"][-500:]
        d["state_probs"] = d["state_probs"][-500:]
        return d
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/copula")
async def run_copula(req: CopulaRequest) -> dict:
    """Copula 尾部相关性分析（极端事件联合风险）。"""
    try:
        result = analyze_copula(
            returns_x=req.returns_x, returns_y=req.returns_y,
            copula_type=req.copula_type, tail_q=req.tail_q,
        )
        return result.__dict__
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
