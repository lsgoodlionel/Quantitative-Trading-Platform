"""序列模型 API 端点（B8）。

PyTorch 序列模型（LSTM / GRU / ALSTM）训练接口，torch 为可选依赖。

端点列表（挂载于 /quant 前缀）:
  GET  /quant/ml/sequence-models  — 列出可用序列模型 + torch 就绪状态
  POST /quant/ml/sequence-train   — 训练序列模型，返回评估指标 + 预测信号

torch 未安装时训练端点返回 501，其余功能不受影响。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.quant.models.networks import SequenceModelType
from app.quant.models.sequence import (
    SequenceConfig,
    sequence_models_meta,
    torch_available,
    train_sequence_model,
    TORCH_INSTALL_HINT,
)

router = APIRouter(tags=["Sequence Models"])


# ── 请求模型 ──────────────────────────────────────────────────────

class SequenceTrainRequest(BaseModel):
    symbol:       str = Field(min_length=1, max_length=20)
    market:       str = Field(default="US", pattern="^(US|HK|A)$")
    frequency:    str = Field(default="1d")
    start:        str | None = None
    end:          str | None = None
    model_type:   SequenceModelType = "lstm"
    forward_days: int   = Field(default=5, ge=1, le=60)
    seq_len:      int   = Field(default=20, ge=5, le=120)
    epochs:       int   = Field(default=30, ge=5, le=200)
    hidden_size:  int   = Field(default=32, ge=8, le=256)
    num_layers:   int   = Field(default=2, ge=1, le=4)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    dropout:      float = Field(default=0.2, ge=0, le=0.8)
    test_size:    float = Field(default=0.2, ge=0.1, le=0.4)


# ── 端点实现 ──────────────────────────────────────────────────────

@router.get("/ml/sequence-models")
async def list_sequence_models() -> dict:
    """列出可用序列模型及 torch 就绪状态（torch 未装时 torch_ready=false）。"""
    return sequence_models_meta()


@router.post("/ml/sequence-train")
async def train_sequence(req: SequenceTrainRequest) -> dict:
    """
    训练 PyTorch 序列分类模型（LSTM / GRU / ALSTM）。

    复用 8 个技术指标特征构造滑动窗口序列，预测 n 日后价格方向，
    返回评估指标（准确率、AUC、排列重要度、损失曲线）与近期信号。

    torch 未安装时返回 501 + 安装提示。
    """
    if not torch_available():
        raise HTTPException(status_code=501, detail=TORCH_INSTALL_HINT)

    df = await _load_bars(req)

    try:
        config = SequenceConfig(
            model_type=req.model_type,
            forward_days=req.forward_days,
            seq_len=req.seq_len,
            epochs=req.epochs,
            hidden_size=req.hidden_size,
            num_layers=req.num_layers,
            learning_rate=req.learning_rate,
            dropout=req.dropout,
            test_size=req.test_size,
        )
        result = train_sequence_model(df=df, config=config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"序列模型训练错误: {e}") from e

    return {"symbol": req.symbol, "market": req.market, **result.__dict__}


# ── 私有辅助 ──────────────────────────────────────────────────────

async def _load_bars(req: SequenceTrainRequest) -> pd.DataFrame:
    """按请求加载 OHLCV 行情并转为 DataFrame（index 为时间字符串）。"""
    from app.core.database import AsyncSessionLocal
    from app.data.models import Frequency as FreqEnum, Market as MarketEnum
    from app.data.service import DataService

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
            bars = await svc.get_bars(
                req.symbol, market_enum, freq_enum, start_date, end_date,
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Data feed error: {e}") from e

    if len(bars) < 200:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough data: {len(bars)} bars (need >= 200)",
        )

    return pd.DataFrame([{
        "time": b.time.isoformat(),
        "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "volume": b.volume,
    } for b in bars]).set_index("time")
