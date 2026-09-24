"""
合约元数据（V4 Wave F-b / O5）

`Bar` 描述「某一时刻的价格」，`ContractSpec` 描述「这个代码到底是什么东西」：
乘数、最小变动价位、到期日、行权价……股票不需要这些，衍生品离了它们寸步难行。

**本期是预留，不是接线。** 这个类只被期权链映射（`data/providers/options_contracts.py`）
与连续合约拼接（`data/continuous_futures.py`）使用，
**没有任何撮合、保证金、逐日盯市逻辑读它**（`engine/backtest/broker.py` 本期一行不改）。

⚠️ 与 `Bar` 的关系：两者各自带 `asset_class`，**互不校验**。
   `stitch_continuous` 不会用 spec 的 asset_class 去覆写 bar 的标签
   （见 `continuous_futures.py` 的说明）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Literal, get_args

from app.data.models.asset_class import AssetClass

#: 期权方向。用 Literal 而非枚举：契约如此规定，且它天然与 JSON / yfinance 的
#: "call" / "put" 字符串对齐，少一层转换。
OptionRight = Literal["call", "put"]

VALID_OPTION_RIGHTS: frozenset[str] = frozenset(get_args(OptionRight))

#: 必须给出到期日的资产类别 —— 没有到期日就无法判断合约何时失效。
_EXPIRING_CLASSES: frozenset[AssetClass] = frozenset({AssetClass.FUTURES, AssetClass.OPTION})

#: 只有期权才允许出现的字段（字段名 → 取值）由 `_option_only_fields` 生成。


@dataclass(frozen=True)
class ContractSpec:
    """
    单份合约的静态元数据。

    字段：
      symbol       — 合约代码（期货为具体月份合约如 "CLZ24"，不是连续合约代号）
      asset_class  — 资产类别
      multiplier   — 合约乘数：1 手对应多少标的单位（股票 = 1，美股期权 = 100）
      tick_size    — 最小变动价位
      underlying   — 标的代码（期货/期权用；股票留空）
      expiry       — 到期日（期货/期权**必填**）
      strike       — 行权价（期权**必填**）
      option_right — 认购/认沽（期权**必填**）

    校验口径：只做「该有的没有」和「不该有的却有」两类判断，
    不做交易所规则校验（合约月份是否存在、行权价是否在挂牌序列上等）——
    那些依赖各交易所的合约表，本期不引入。
    """

    symbol: str
    asset_class: AssetClass
    multiplier: float = 1.0          # 合约乘数（股票=1）
    tick_size: float = 0.01
    # 期货
    underlying: str | None = None
    expiry: Date | None = None
    # 期权
    strike: float | None = None
    option_right: OptionRight | None = None

    def __post_init__(self) -> None:
        self._validate_identity()
        self._validate_numbers()
        self._validate_by_asset_class()

    # ── 校验 ──────────────────────────────────────────────────
    def _validate_identity(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"ContractSpec.symbol 必须是非空字符串，收到 {self.symbol!r}")
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError(
                f"ContractSpec.asset_class 必须是 AssetClass 枚举，收到 {self.asset_class!r}"
            )

    def _validate_numbers(self) -> None:
        # 乘数/tick 为 0 或负数会让任何下游计算（名义价值、最小跳动盈亏）静默出错，
        # 与其让错误传播到很远的地方，不如在构造点就炸。
        if not self.multiplier > 0:
            raise ValueError(f"{self.symbol}: multiplier 必须为正，收到 {self.multiplier!r}")
        if not self.tick_size > 0:
            raise ValueError(f"{self.symbol}: tick_size 必须为正，收到 {self.tick_size!r}")
        if self.strike is not None and not self.strike > 0:
            raise ValueError(f"{self.symbol}: strike 必须为正，收到 {self.strike!r}")

    def _validate_by_asset_class(self) -> None:
        if self.asset_class in _EXPIRING_CLASSES and self.expiry is None:
            raise ValueError(f"{self.symbol}: {self.asset_class.value} 合约必须提供 expiry")

        if self.asset_class is AssetClass.OPTION:
            self._validate_option()
            return

        # 非期权带着期权字段，多半是复制粘贴时改漏了 asset_class
        for name, value in self._option_only_fields():
            if value is not None:
                raise ValueError(
                    f"{self.symbol}: 非期权合约（{self.asset_class.value}）不应带 {name}={value!r}"
                )

    def _validate_option(self) -> None:
        if self.strike is None:
            raise ValueError(f"{self.symbol}: 期权合约必须提供 strike")
        if self.option_right is None:
            raise ValueError(f"{self.symbol}: 期权合约必须提供 option_right")
        if self.option_right not in VALID_OPTION_RIGHTS:
            raise ValueError(
                f"{self.symbol}: option_right 必须是 {sorted(VALID_OPTION_RIGHTS)} 之一，"
                f"收到 {self.option_right!r}"
            )

    def _option_only_fields(self) -> tuple[tuple[str, object], ...]:
        return (("strike", self.strike), ("option_right", self.option_right))

    # ── 便利属性 ──────────────────────────────────────────────
    @property
    def is_expiring(self) -> bool:
        """是否是有到期日的合约（期货/期权）。"""
        return self.expiry is not None

    def notional(self, price: float) -> float:
        """按给定价格计算 1 手的名义价值 = 价格 × 乘数。

        ⚠️ 这只是一个算术工具，**不是保证金**。保证金依赖交易所规则与
        跨品种优惠，本期明确不做。
        """
        return float(price) * self.multiplier


def equity_spec(symbol: str, *, tick_size: float = 0.01) -> ContractSpec:
    """股票合约的便捷构造：乘数恒为 1.0。"""
    return ContractSpec(
        symbol=symbol, asset_class=AssetClass.EQUITY, multiplier=1.0, tick_size=tick_size
    )
