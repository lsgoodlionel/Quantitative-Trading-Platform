"""
期权链 + Greeks 服务（Wave-3f / A5）— 仅美股

职责:
  1. 拉取到期日 / 期权链（yfinance）
  2. 用本仓库 quant/bsm.py（BSM）为每份合约本地计算 delta/gamma/theta/vega
  3. 统一封装响应 + 降级 warning

Greeks 计算说明:
  - T（到期年数）= 剩余自然日 / 365；当日到期(T<=0)跳过 Greeks
  - sigma 取 yfinance 隐含波动率；缺失或 <=0 跳过 Greeks
  - S 取标的现价；缺失则无法定价（跳过 Greeks，仍返回行情）
  - q（股息率）默认 0（美股期权链 Greeks 近似，避免额外数据依赖）
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.data.providers.options_models import (
    DEFAULT_RISK_FREE_RATE,
    OptionContract,
    OptionsChainResponse,
    OptionsExpirationsResponse,
)
from app.data.providers.options_provider import (
    YFinanceOptionsChainFetcher,
    YFinanceOptionsExpirationsFetcher,
)
from app.quant.bsm import price_option

logger = get_logger(__name__)

_DAYS_PER_YEAR = 365.0
_MIN_DTE_DAYS = 0


def _with_greeks(
    contract: OptionContract,
    underlying_price: float | None,
    risk_free_rate: float,
    today: DateType,
) -> OptionContract:
    """为单份合约计算 Greeks；数据不足则原样返回。"""
    sigma = contract.implied_volatility
    strike = contract.strike
    expiration = contract.expiration
    if (
        underlying_price is None
        or underlying_price <= 0
        or sigma is None
        or sigma <= 0
        or expiration is None
    ):
        return contract.model_copy(update={"dte": _dte(expiration, today)})

    dte_days = (expiration - today).days
    if dte_days <= _MIN_DTE_DAYS:
        return contract.model_copy(update={"dte": dte_days})

    T = dte_days / _DAYS_PER_YEAR
    try:
        result = price_option(
            S=underlying_price,
            K=strike,
            r=risk_free_rate,
            sigma=sigma,
            T=T,
            q=0.0,
            option_type=contract.option_type,
        )
    except ValueError as e:
        logger.debug("greeks skip", strike=strike, error=str(e))
        return contract.model_copy(update={"dte": dte_days})

    return contract.model_copy(
        update={
            "dte": dte_days,
            "delta": result.delta,
            "gamma": result.gamma,
            "theta": result.theta,
            "vega": result.vega,
        }
    )


def _dte(expiration: DateType | None, today: DateType) -> int | None:
    return (expiration - today).days if expiration else None


class OptionsService:
    """期权链统一入口。无状态，可直接实例化（仅美股）。"""

    async def get_expirations(self, symbol: str) -> OptionsExpirationsResponse:
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("标的代码不能为空")
        data = await YFinanceOptionsExpirationsFetcher.fetch_data({"symbol": symbol})
        warnings: list[str] = []
        if not data.get("expirations"):
            warnings.append("未获取到期权到期日（可能非期权标的或数据源不可用）")
        return OptionsExpirationsResponse(
            symbol=symbol.upper(),
            underlying_price=data.get("underlying_price"),
            expirations=data.get("expirations", []),
            warnings=warnings,
        )

    async def get_chain(
        self,
        symbol: str,
        expiration: str | None = None,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> OptionsChainResponse:
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("标的代码不能为空")

        params = {
            "symbol": symbol,
            "expiration": expiration,
            "risk_free_rate": risk_free_rate,
        }
        data = await YFinanceOptionsChainFetcher.fetch_data(params)
        today = datetime.now(tz=timezone.utc).date()

        underlying = data.get("underlying_price")
        calls = [
            _with_greeks(c, underlying, risk_free_rate, today)
            for c in data.get("calls", [])
        ]
        puts = [
            _with_greeks(p, underlying, risk_free_rate, today)
            for p in data.get("puts", [])
        ]

        warnings: list[str] = []
        if not calls and not puts:
            warnings.append("该到期日无期权链数据")
        if underlying is None:
            warnings.append("未获取标的现价，Greeks 不可用")

        return OptionsChainResponse(
            symbol=symbol.upper(),
            underlying_price=underlying,
            expiration=data.get("expiration"),
            risk_free_rate=risk_free_rate,
            calls=calls,
            puts=puts,
            warnings=warnings,
        )
