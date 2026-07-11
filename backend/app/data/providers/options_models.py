"""
标准化期权链 + Greeks 数据模型（Wave-3f / A5）

设计参考: refs/OpenBB/.../provider/standard_models/options_chains.py
  该仓库用「列式」结构（每字段一个 list）；本平台前端按「行式」展开更直观，
  故采用行式 OptionContract（每合约一条），Greeks 由本仓库 quant/bsm.py 本地计算。

仅覆盖美股（yfinance option_chain）。
"""

from __future__ import annotations

from datetime import date as DateType

from pydantic import Field

from app.data.providers.base import Data, QueryParams

# 默认无风险年化利率（Greeks 计算用；可由端点 query 覆盖）
DEFAULT_RISK_FREE_RATE = 0.045


class OptionsExpirationsQueryParams(QueryParams):
    """期权到期日查询参数。"""

    symbol: str = Field(description="标的代码（美股）")


class OptionsChainQueryParams(QueryParams):
    """期权链查询参数。"""

    symbol: str = Field(description="标的代码（美股）")
    expiration: str | None = Field(
        default=None, description="到期日 YYYY-MM-DD；缺省取最近到期"
    )
    risk_free_rate: float = Field(
        default=DEFAULT_RISK_FREE_RATE, ge=0, le=0.5, description="无风险年化利率"
    )


class OptionsExpirationsResponse(Data):
    """可选到期日列表。"""

    symbol: str
    underlying_price: float | None = None
    expirations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OptionContract(Data):
    """单份期权合约（含本地计算的 Greeks）。"""

    contract_symbol: str | None = Field(default=None, description="合约代码")
    option_type: str = Field(description="call / put")
    expiration: DateType | None = Field(default=None, description="到期日")
    dte: int | None = Field(default=None, description="剩余天数")
    strike: float = Field(description="行权价")

    last_price: float | None = Field(default=None, description="最新价")
    bid: float | None = Field(default=None, description="买价")
    ask: float | None = Field(default=None, description="卖价")
    mark: float | None = Field(default=None, description="买卖中值")
    change: float | None = Field(default=None, description="涨跌额")
    percent_change: float | None = Field(default=None, description="涨跌幅(%)")
    volume: float | None = Field(default=None, description="成交量")
    open_interest: float | None = Field(default=None, description="未平仓量 OI")
    implied_volatility: float | None = Field(default=None, description="隐含波动率(分数)")
    in_the_money: bool | None = Field(default=None, description="是否价内")

    # Greeks（BSM 本地计算；IV 缺失时为 None）
    delta: float | None = Field(default=None, description="Delta Δ")
    gamma: float | None = Field(default=None, description="Gamma Γ")
    theta: float | None = Field(default=None, description="Theta Θ（日衰减）")
    vega: float | None = Field(default=None, description="Vega ν（每1%波动）")


class OptionsChainResponse(Data):
    """期权链响应（按到期日，分 calls/puts）。"""

    symbol: str
    underlying_price: float | None = None
    expiration: str | None = None
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    calls: list[OptionContract] = Field(default_factory=list)
    puts: list[OptionContract] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
