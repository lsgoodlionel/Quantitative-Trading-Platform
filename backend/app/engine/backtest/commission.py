"""
佣金模型

参考: refs/backtrader/backtrader/broker.py CommInfoBase 设计
针对美股/港股实际佣金结构实现。

美股（Alpaca）: 零佣金（$0 commission）; 但有 SEC fee + FINRA fee
港股（富途）: 0.03% 平台佣金 + 交易所规费
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.data.models import Market

# 美股 SEC 规费: 每卖出 $1,000,000 收 $8.00（2024年费率）
US_SEC_FEE_RATE = 8.0 / 1_000_000.0
# FINRA TAF: 每股 $0.000166，最低 $0.01，最高 $8.30
US_FINRA_TAF_PER_SHARE = 0.000166
US_FINRA_TAF_MIN = 0.01
US_FINRA_TAF_MAX = 8.30

# 港股规费率（固定）
HK_STAMP_DUTY_RATE = 0.0013       # 印花税 0.13%（买卖双方各付）
HK_TRANSACTION_LEVY_RATE = 0.000027   # 证监会交易征费
HK_TRADING_FEE_RATE = 0.000005    # 交易所费
HK_SFC_LEVY_RATE = 0.000001       # 投资者赔偿征费


@dataclass
class CommissionResult:
    commission: float     # 券商佣金
    fees: float           # 交易所/政府规费
    total: float          # 总费用


class CommissionModel(ABC):
    """
    佣金模型抽象基类。
    参考 refs/backtrader/backtrader/broker.py CommInfoBase
    """

    @abstractmethod
    def calculate(
        self,
        price: float,
        qty: int,
        direction: str,   # BUY / SELL
    ) -> CommissionResult:
        ...

    @property
    @abstractmethod
    def market(self) -> Market:
        ...


class USCommissionModel(CommissionModel):
    """
    美股佣金模型（Alpaca 零佣金 + SEC/FINRA 规费）。

    Alpaca 美股交易零佣金，但有：
    - SEC fee: 卖出时按交易金额计费
    - FINRA TAF: 卖出时按股数计费
    """

    _market = Market.US

    def __init__(self, commission_per_share: float = 0.0) -> None:
        self._commission_per_share = commission_per_share  # Alpaca 默认 0

    @property
    def market(self) -> Market:
        return self._market

    def calculate(self, price: float, qty: int, direction: str) -> CommissionResult:
        trade_value = price * qty
        commission = self._commission_per_share * qty

        fees = 0.0
        if direction == "SELL":
            # SEC fee（只在卖出时收）
            sec_fee = trade_value * US_SEC_FEE_RATE
            # FINRA TAF（只在卖出时收）
            finra_taf = max(
                US_FINRA_TAF_MIN,
                min(qty * US_FINRA_TAF_PER_SHARE, US_FINRA_TAF_MAX)
            )
            fees = round(sec_fee + finra_taf, 4)

        total = commission + fees
        return CommissionResult(commission=round(commission, 4), fees=fees, total=round(total, 4))


class HKCommissionModel(CommissionModel):
    """
    港股佣金模型（富途 + 交易所规费）。

    佣金: 0.03%（富途默认，最低 HK$3）
    印花税: 0.13%（买卖双方）
    交易所费: 0.0005%
    证监会征费: 0.0027%
    """

    _market = Market.HK

    def __init__(
        self,
        commission_rate: float = 0.0003,  # 0.03%
        min_commission: float = 3.0,      # 最低 HK$3
    ) -> None:
        self._commission_rate = commission_rate
        self._min_commission = min_commission

    @property
    def market(self) -> Market:
        return self._market

    def calculate(self, price: float, qty: int, direction: str) -> CommissionResult:
        trade_value = price * qty

        commission = max(
            self._min_commission,
            trade_value * self._commission_rate
        )

        # 港股规费（买卖双方均收）
        stamp_duty = trade_value * HK_STAMP_DUTY_RATE
        transaction_levy = trade_value * HK_TRANSACTION_LEVY_RATE
        trading_fee = trade_value * HK_TRADING_FEE_RATE
        sfc_levy = trade_value * HK_SFC_LEVY_RATE
        fees = stamp_duty + transaction_levy + trading_fee + sfc_levy

        total = commission + fees
        return CommissionResult(
            commission=round(commission, 4),
            fees=round(fees, 4),
            total=round(total, 4)
        )


A_STAMP_DUTY_RATE = 0.001           # 印花税 0.1%（仅卖出）
A_TRANSFER_FEE_RATE = 0.00002       # 过户费（上交所股票，买卖双方）
A_REGULATORY_FEE_RATE = 0.0000487   # 证监会监管费


class AShareCommissionModel(CommissionModel):
    """
    A股佣金模型（沪深两市）。

    参考: refs/rqalpha/rqalpha/mod/rqalpha_mod_sys_accounts/
    费用结构:
    - 券商佣金: 0.025% 双边，最低 5 元
    - 印花税: 0.1% 仅卖出
    - 过户费: 0.002% 买卖双边（主要针对上交所股票）
    - 证监会监管费: 0.00487/万，双边
    """

    _market = Market.A

    def __init__(
        self,
        commission_rate: float = 0.00025,  # 0.025%
        min_commission: float = 5.0,        # 最低 5 元
    ) -> None:
        self._commission_rate = commission_rate
        self._min_commission = min_commission

    @property
    def market(self) -> Market:
        return self._market

    def calculate(self, price: float, qty: int, direction: str) -> CommissionResult:
        trade_value = price * qty

        commission = max(self._min_commission, trade_value * self._commission_rate)

        # 印花税：仅卖出时收
        stamp_duty = trade_value * A_STAMP_DUTY_RATE if direction == "SELL" else 0.0
        # 过户费（双边）
        transfer_fee = trade_value * A_TRANSFER_FEE_RATE
        # 监管费（双边）
        regulatory_fee = trade_value * A_REGULATORY_FEE_RATE
        fees = stamp_duty + transfer_fee + regulatory_fee

        total = commission + fees
        return CommissionResult(
            commission=round(commission, 4),
            fees=round(fees, 4),
            total=round(total, 4),
        )


def get_commission_model(market: Market) -> CommissionModel:
    """工厂函数：根据市场返回对应佣金模型。"""
    if market == Market.US:
        return USCommissionModel()
    if market == Market.HK:
        return HKCommissionModel()
    if market == Market.A:
        return AShareCommissionModel()
    raise ValueError(f"No commission model for market: {market}")
