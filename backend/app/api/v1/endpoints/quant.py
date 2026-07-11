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

from typing import Literal

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


# ── Factor Analysis ───────────────────────────────────────────────

class FactorAnalysisRequest(BaseModel):
    symbol: str
    market: str = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    factor_name: str = "momentum_20"
    forward_periods: list[int] = Field(default=[5, 10, 20], max_length=4)


@router.get("/factor/list")
async def list_factors() -> list[dict]:
    """获取所有可用因子列表。"""
    from app.quant.factor_analysis import AVAILABLE_FACTORS
    return AVAILABLE_FACTORS


@router.post("/factor/analyze")
async def run_factor_analysis(req: FactorAnalysisRequest) -> dict:
    """
    因子 IC 分析。

    计算指定因子与多个前瞻期收益率的滚动 IC（信息系数），
    返回 IC 时间序列、IC IR、分位数收益分析。
    """
    from datetime import date, timedelta
    import pandas as pd
    from app.quant.factor_analysis import analyze_factor
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService
    from app.core.database import AsyncSessionLocal

    # parse dates
    end_date = date.fromisoformat(req.end) if req.end else date.today()
    start_date = date.fromisoformat(req.start) if req.start else end_date - timedelta(days=365 * 2)

    try:
        market_enum = MarketEnum(req.market)
        freq_enum = FreqEnum(req.frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        try:
            bars = await svc.get_bars(req.symbol, market_enum, freq_enum, start_date, end_date)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Data feed error: {e}") from e

    if len(bars) < 60:
        raise HTTPException(status_code=400, detail=f"Not enough data: {len(bars)} bars (need ≥ 60)")

    df = pd.DataFrame([{
        "time": b.time.isoformat(),
        "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "volume": b.volume,
    } for b in bars]).set_index("time")

    try:
        result = analyze_factor(df, req.factor_name, req.forward_periods)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Factor computation error: {e}") from e

    return {
        "symbol": req.symbol,
        "market": req.market,
        "factor_name": result.factor_name,
        "forward_periods": result.forward_periods,
        "factor_series": result.factor_series[-500:],
        "ic_series": {k: v[-500:] for k, v in result.ic_series.items()},
        "cumulative_ic": {k: v[-500:] for k, v in result.cumulative_ic.items()},
        "ic_mean": result.ic_mean,
        "ic_std": result.ic_std,
        "ic_ir": result.ic_ir,
        "ic_positive_rate": result.ic_positive_rate,
        "ic_abs_mean": result.ic_abs_mean,
        "quantile_returns": result.quantile_returns,
    }


# ── 公式因子（Formula Factor）─────────────────────────────────────

class FormulaFactorRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    market: str = Field(default="US", pattern="^(US|HK|A)$")
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    tokens: list[str] = Field(..., min_length=1, max_length=32, description="RPN 公式 token 列表")
    forward_periods: list[int] = Field(default=[5, 10, 20], max_length=4)


@router.get("/factor/formula/meta")
async def get_formula_meta() -> dict:
    """返回公式因子构建器的元数据：可用特征、算子、预设公式。"""
    from app.quant.formula_factor import FEATURE_META, OP_META, PRESET_FORMULAS
    return {
        "features": FEATURE_META,
        "operators": OP_META,
        "presets": PRESET_FORMULAS,
    }


@router.post("/factor/formula")
async def run_formula_factor(req: FormulaFactorRequest) -> dict:
    """
    自定义公式因子 IC 分析。

    用 RPN（逆波兰）token 列表构造 alpha 表达式，执行后复用因子 IC 分析流程，
    返回与 /factor/analyze 相同结构的结果。

    示例 tokens：["MOM20", "ATR_RATIO", "DIV"]（动量除以波动率）
    """
    from datetime import date, timedelta
    import pandas as pd
    from app.quant.factor_analysis import analyze_factor
    from app.quant.formula_factor import evaluate_formula, FormulaError
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService
    from app.core.database import AsyncSessionLocal

    end_date = date.fromisoformat(req.end) if req.end else date.today()
    start_date = date.fromisoformat(req.start) if req.start else end_date - timedelta(days=365 * 2)

    try:
        market_enum = MarketEnum(req.market)
        freq_enum = FreqEnum(req.frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        try:
            bars = await svc.get_bars(req.symbol, market_enum, freq_enum, start_date, end_date)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Data feed error: {e}") from e

    if len(bars) < 60:
        raise HTTPException(status_code=400, detail=f"Not enough data: {len(bars)} bars (need ≥ 60)")

    df = pd.DataFrame([{
        "time": b.time.isoformat(),
        "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "volume": b.volume,
    } for b in bars]).set_index("time")

    # 执行公式得到因子序列
    try:
        factor_series = evaluate_formula(df, req.tokens)
    except FormulaError as e:
        raise HTTPException(status_code=400, detail=f"公式错误: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"公式求值失败: {e}") from e

    formula_label = " ".join(req.tokens)

    try:
        result = analyze_factor(
            df, formula_label, req.forward_periods, factor_override=factor_series
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Factor analysis error: {e}") from e

    return {
        "symbol": req.symbol,
        "market": req.market,
        "factor_name": formula_label,
        "tokens": req.tokens,
        "forward_periods": result.forward_periods,
        "factor_series": result.factor_series[-500:],
        "ic_series": {k: v[-500:] for k, v in result.ic_series.items()},
        "cumulative_ic": {k: v[-500:] for k, v in result.cumulative_ic.items()},
        "ic_mean": result.ic_mean,
        "ic_std": result.ic_std,
        "ic_ir": result.ic_ir,
        "ic_positive_rate": result.ic_positive_rate,
        "ic_abs_mean": result.ic_abs_mean,
        "quantile_returns": result.quantile_returns,
    }


# ── ML Strategy ───────────────────────────────────────────────────

MLModelType = Literal[
    "logistic_regression", "random_forest", "gradient_boosting", "double_ensemble",
]


class MLTrainRequest(BaseModel):
    symbol:       str          = Field(min_length=1, max_length=20)
    market:       str          = Field(default="US", pattern="^(US|HK|A)$")
    frequency:    str          = Field(default="1d")
    start:        str | None   = None
    end:          str | None   = None
    model_type:   MLModelType  = "random_forest"
    forward_days: int          = Field(default=5, ge=1, le=60)
    test_size:    float        = Field(default=0.2, ge=0.1, le=0.4)


@router.post("/ml/train")
async def train_ml_strategy(req: MLTrainRequest) -> dict:
    """
    训练 ML 分类策略。

    使用 8 个技术指标特征训练 sklearn 分类模型，预测 n 日后价格方向。
    返回模型评估指标（准确率、AUC、特征重要度）和近期信号。
    """
    from datetime import date, timedelta
    import pandas as pd
    from app.quant.ml_strategy import train_ml_strategy as _train
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService
    from app.core.database import AsyncSessionLocal

    end_date = date.fromisoformat(req.end) if req.end else date.today()
    start_date = (
        date.fromisoformat(req.start) if req.start
        else end_date - timedelta(days=365 * 3)
    )

    try:
        market_enum = MarketEnum(req.market)
        freq_enum = FreqEnum(req.frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        try:
            bars = await svc.get_bars(req.symbol, market_enum, freq_enum, start_date, end_date)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Data feed error: {e}") from e

    if len(bars) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough data: {len(bars)} bars (need >= 100)",
        )

    df = pd.DataFrame([{
        "time": b.time.isoformat(),
        "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "volume": b.volume,
    } for b in bars]).set_index("time")

    try:
        if req.model_type == "double_ensemble":
            from app.quant.double_ensemble import train_double_ensemble
            result = train_double_ensemble(
                df=df,
                forward_days=req.forward_days,
                test_size=req.test_size,
            )
        else:
            result = _train(
                df=df,
                model_type=req.model_type,
                forward_days=req.forward_days,
                test_size=req.test_size,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"ML training error: {e}") from e

    payload = {
        "symbol": req.symbol,
        "market": req.market,
        "model_type": result.model_type,
        "forward_days": result.forward_days,
        "n_samples": result.n_samples,
        "n_features": result.n_features,
        "feature_names": result.feature_names,
        "train_accuracy": result.train_accuracy,
        "test_accuracy": result.test_accuracy,
        "precision": result.precision,
        "recall": result.recall,
        "f1_score": result.f1_score,
        "auc_roc": result.auc_roc,
        "feature_importance": result.feature_importance,
        "confusion_matrix": result.confusion_matrix,
        "predictions": result.predictions[-30:],
        "recent_signal": result.recent_signal,
        "recent_prob": result.recent_prob,
        "cv_mean": result.cv_mean,
        "cv_std": result.cv_std,
    }

    # DoubleEnsemble 附带集成诊断（其余模型无此字段，前端可选读取）
    if req.model_type == "double_ensemble":
        payload["ensemble"] = {
            "num_models": result.num_models,
            "enable_sr": result.enable_sr,
            "enable_fs": result.enable_fs,
            "sub_feature_counts": result.sub_feature_counts,
            "feature_usage": result.feature_usage,
        }

    return payload
