"""
yfinance 期权链提供者（Wave-3f / A5）— 仅美股

数据来源:
  - ticker.options              → 到期日元组（YYYY-MM-DD 字符串）
  - ticker.option_chain(date)   → namedtuple(.calls, .puts) 两张 DataFrame
  - ticker.fast_info / .info    → 标的现价（Greeks 计算需要）

期权链 DataFrame 列（yfinance 约定）:
  contractSymbol, lastTradeDate, strike, lastPrice, bid, ask, change,
  percentChange, volume, openInterest, impliedVolatility, inTheMoney,
  contractSize, currency

Greeks 不由 yfinance 提供，由 service 层用 quant/bsm.py 本地计算。
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.data.providers.base import Fetcher
from app.data.providers.options_models import (
    OptionContract,
    OptionsChainQueryParams,
    OptionsExpirationsQueryParams,
)

logger = get_logger(__name__)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return bool(value)


def _underlying_price(ticker: Any) -> float | None:
    """尽力取标的现价：fast_info 优先，回退 info。"""
    try:
        fast = ticker.fast_info
        price = getattr(fast, "last_price", None)
        if price is None and isinstance(fast, dict):
            price = fast.get("lastPrice") or fast.get("last_price")
        val = _num(price)
        if val is not None:
            return val
    except Exception as e:  # noqa: BLE001
        logger.debug("fast_info failed", error=str(e))
    try:
        info = ticker.info
        if isinstance(info, dict):
            return _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    except Exception as e:  # noqa: BLE001
        logger.debug("info price failed", error=str(e))
    return None


def _contracts_from_df(
    df: pd.DataFrame | None, option_type: str, expiration: DateType | None
) -> list[OptionContract]:
    """一张 calls/puts DataFrame → List[OptionContract]（无 Greeks，后续填充）。"""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out: list[OptionContract] = []
    for _, row in df.iterrows():
        strike = _num(row.get("strike"))
        if strike is None:
            continue
        last = _num(row.get("lastPrice"))
        bid = _num(row.get("bid"))
        ask = _num(row.get("ask"))
        mark = (bid + ask) / 2 if bid is not None and ask is not None else last
        out.append(
            OptionContract(
                contract_symbol=str(row.get("contractSymbol"))
                if row.get("contractSymbol") is not None
                else None,
                option_type=option_type,
                expiration=expiration,
                strike=strike,
                last_price=last,
                bid=bid,
                ask=ask,
                mark=mark,
                change=_num(row.get("change")),
                percent_change=_num(row.get("percentChange")),
                volume=_num(row.get("volume")),
                open_interest=_num(row.get("openInterest")),
                implied_volatility=_num(row.get("impliedVolatility")),
                in_the_money=_bool(row.get("inTheMoney")),
            )
        )
    return out


class YFinanceOptionsExpirationsFetcher(
    Fetcher[OptionsExpirationsQueryParams, dict[str, Any]]
):
    """期权到期日 + 标的现价。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> OptionsExpirationsQueryParams:
        return OptionsExpirationsQueryParams(**params)

    @staticmethod
    def extract_data(query: OptionsExpirationsQueryParams) -> dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker(query.symbol.upper())
        try:
            expirations = list(ticker.options or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("options expirations failed", symbol=query.symbol, error=str(e))
            expirations = []
        return {
            "expirations": [str(x) for x in expirations],
            "underlying_price": _underlying_price(ticker),
        }

    @staticmethod
    def transform_data(
        query: OptionsExpirationsQueryParams, data: dict[str, Any]
    ) -> dict[str, Any]:
        return data


class YFinanceOptionsChainFetcher(Fetcher[OptionsChainQueryParams, dict[str, Any]]):
    """单一到期日的期权链（calls + puts，含标的现价）。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> OptionsChainQueryParams:
        return OptionsChainQueryParams(**params)

    @staticmethod
    def extract_data(query: OptionsChainQueryParams) -> dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker(query.symbol.upper())
        expirations = list(ticker.options or [])
        if not expirations:
            return {"expiration": None, "calls": None, "puts": None, "underlying_price": None}

        expiration = query.expiration
        if expiration not in expirations:
            expiration = expirations[0]  # 缺省/非法 → 最近到期

        chain = ticker.option_chain(expiration)
        return {
            "expiration": expiration,
            "calls": getattr(chain, "calls", None),
            "puts": getattr(chain, "puts", None),
            "underlying_price": _underlying_price(ticker),
        }

    @staticmethod
    def transform_data(
        query: OptionsChainQueryParams, data: dict[str, Any]
    ) -> dict[str, Any]:
        expiration = data.get("expiration")
        exp_date: DateType | None = None
        if expiration:
            try:
                exp_date = pd.Timestamp(expiration).date()
            except (ValueError, TypeError):
                exp_date = None
        return {
            "expiration": expiration,
            "underlying_price": data.get("underlying_price"),
            "calls": _contracts_from_df(data.get("calls"), "call", exp_date),
            "puts": _contracts_from_df(data.get("puts"), "put", exp_date),
        }
