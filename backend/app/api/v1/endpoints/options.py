"""
期权链 + Greeks API 端点（Wave-3f / A5）— 仅美股

  GET /options/{symbol}/expirations  — 可选到期日 + 标的现价
  GET /options/{symbol}/chain        — 指定到期日的期权链（含本地 BSM Greeks）

Greeks 由 app.quant.bsm 本地计算，见 options_service。风格对齐 endpoints/fundamentals.py。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.logging import get_logger
from app.data.providers import OptionsService
from app.data.providers.options_models import (
    DEFAULT_RISK_FREE_RATE,
    OptionsChainResponse,
    OptionsExpirationsResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Options"])


@router.get("/{symbol}/expirations", response_model=OptionsExpirationsResponse)
async def get_option_expirations(
    symbol: str = Path(..., description="标的代码（美股），如 AAPL"),
) -> OptionsExpirationsResponse:
    """获取标的可选期权到期日。"""
    try:
        return await OptionsService().get_expirations(symbol=symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error("options expirations failed", symbol=symbol, error=str(e))
        raise HTTPException(status_code=503, detail=f"期权数据源不可用: {e}") from e


@router.get("/{symbol}/chain", response_model=OptionsChainResponse)
async def get_option_chain(
    symbol: str = Path(..., description="标的代码（美股），如 AAPL"),
    expiration: str | None = Query(None, description="到期日 YYYY-MM-DD；缺省取最近到期"),
    risk_free_rate: float = Query(
        DEFAULT_RISK_FREE_RATE, ge=0, le=0.5, description="Greeks 计算用无风险利率"
    ),
) -> OptionsChainResponse:
    """获取指定到期日的期权链（calls/puts + Greeks）。"""
    try:
        return await OptionsService().get_chain(
            symbol=symbol, expiration=expiration, risk_free_rate=risk_free_rate
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error("options chain failed", symbol=symbol, error=str(e))
        raise HTTPException(status_code=503, detail=f"期权数据源不可用: {e}") from e
