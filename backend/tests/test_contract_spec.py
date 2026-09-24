"""
合约元数据 ContractSpec（V4 Wave F-b / O5）

重点是「该有的没有就报错」：一份缺到期日的期货、缺行权价的期权，
在数据层看起来只是几个 None，到了定价/交割就是整条链路的错。
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from app.data.models import AssetClass, ContractSpec, equity_spec

EXPIRY = date(2024, 12, 20)


class TestEquitySpec:
    def test_multiplier_defaults_to_one(self) -> None:
        spec = ContractSpec(symbol="AAPL", asset_class=AssetClass.EQUITY)
        assert spec.multiplier == 1.0
        assert spec.tick_size == 0.01

    def test_equity_needs_no_expiry(self) -> None:
        assert ContractSpec(symbol="AAPL", asset_class=AssetClass.EQUITY).expiry is None

    def test_helper_builds_equity_with_multiplier_one(self) -> None:
        spec = equity_spec("00700")
        assert (spec.asset_class, spec.multiplier) == (AssetClass.EQUITY, 1.0)

    def test_is_frozen(self) -> None:
        spec = equity_spec("AAPL")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.multiplier = 2.0  # type: ignore[misc]


class TestFuturesSpec:
    def test_futures_requires_expiry(self) -> None:
        with pytest.raises(ValueError, match="必须提供 expiry"):
            ContractSpec(symbol="CLZ24", asset_class=AssetClass.FUTURES, multiplier=1000.0)

    def test_futures_with_expiry_is_valid(self) -> None:
        spec = ContractSpec(
            symbol="CLZ24",
            asset_class=AssetClass.FUTURES,
            multiplier=1000.0,
            tick_size=0.01,
            underlying="CL",
            expiry=EXPIRY,
        )
        assert spec.expiry == EXPIRY
        assert spec.is_expiring is True
        assert spec.notional(70.0) == pytest.approx(70_000.0)

    def test_futures_rejects_option_only_fields(self) -> None:
        """带着 strike 的期货多半是复制粘贴时忘了改 asset_class。"""
        with pytest.raises(ValueError, match="不应带 strike"):
            ContractSpec(
                symbol="CLZ24", asset_class=AssetClass.FUTURES, expiry=EXPIRY, strike=70.0
            )


class TestOptionSpec:
    def _option(self, **overrides) -> ContractSpec:
        defaults = {
            "symbol": "AAPL241220C00190000",
            "asset_class": AssetClass.OPTION,
            "multiplier": 100.0,
            "underlying": "AAPL",
            "expiry": EXPIRY,
            "strike": 190.0,
            "option_right": "call",
        }
        return ContractSpec(**{**defaults, **overrides})

    def test_valid_option(self) -> None:
        spec = self._option()
        assert (spec.strike, spec.option_right) == (190.0, "call")

    def test_option_requires_strike(self) -> None:
        with pytest.raises(ValueError, match="必须提供 strike"):
            self._option(strike=None)

    def test_option_requires_right(self) -> None:
        with pytest.raises(ValueError, match="必须提供 option_right"):
            self._option(option_right=None)

    def test_option_requires_expiry(self) -> None:
        # 契约 §四.3 只点名 strike + right，但一份没有到期日的期权无法定价也无法交割，
        # 故此处与期货同样强制 expiry（见 contract.py 说明）。
        with pytest.raises(ValueError, match="必须提供 expiry"):
            self._option(expiry=None)

    def test_option_rejects_unknown_right(self) -> None:
        with pytest.raises(ValueError, match="option_right 必须是"):
            self._option(option_right="CALL")

    def test_option_rejects_non_positive_strike(self) -> None:
        with pytest.raises(ValueError, match="strike 必须为正"):
            self._option(strike=0.0)


class TestGenericValidation:
    def test_rejects_empty_symbol(self) -> None:
        with pytest.raises(ValueError, match="symbol 必须是非空字符串"):
            ContractSpec(symbol="   ", asset_class=AssetClass.EQUITY)

    def test_rejects_non_enum_asset_class(self) -> None:
        with pytest.raises(ValueError, match="必须是 AssetClass 枚举"):
            ContractSpec(symbol="AAPL", asset_class="equity")  # type: ignore[arg-type]

    @pytest.mark.parametrize("multiplier", [0.0, -1.0])
    def test_rejects_non_positive_multiplier(self, multiplier: float) -> None:
        with pytest.raises(ValueError, match="multiplier 必须为正"):
            ContractSpec(symbol="AAPL", asset_class=AssetClass.EQUITY, multiplier=multiplier)

    @pytest.mark.parametrize("tick", [0.0, -0.01])
    def test_rejects_non_positive_tick_size(self, tick: float) -> None:
        with pytest.raises(ValueError, match="tick_size 必须为正"):
            ContractSpec(symbol="AAPL", asset_class=AssetClass.EQUITY, tick_size=tick)

    @pytest.mark.parametrize("asset_class", [AssetClass.FX, AssetClass.CRYPTO])
    def test_fx_and_crypto_need_no_expiry(self, asset_class: AssetClass) -> None:
        assert ContractSpec(symbol="EURUSD", asset_class=asset_class).expiry is None
