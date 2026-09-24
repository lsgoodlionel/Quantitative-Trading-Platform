"""
期权链 → ContractSpec 映射（V4 Wave F-b / O5）

契约 §二：只做映射。不新增数据源、不做定价、不做希腊值。
这里同时钉住「做了什么」和「没做什么」。
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from app.data.models import AssetClass, ContractSpec
from app.data.providers.options_contracts import (
    US_EQUITY_OPTION_MULTIPLIER,
    chain_to_specs,
    normalize_option_right,
    occ_symbol,
    option_contract_to_spec,
)
from app.data.providers.options_models import OptionContract, OptionsChainResponse

EXPIRY = date(2024, 12, 20)


def call(**overrides) -> OptionContract:
    defaults = {
        "contract_symbol": "AAPL241220C00190000",
        "option_type": "call",
        "expiration": EXPIRY,
        "strike": 190.0,
        "last_price": 5.25,
        "implied_volatility": 0.28,
        "delta": 0.55,
    }
    return OptionContract(**{**defaults, **overrides})


class TestNormalizeRight:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("call", "call"), ("CALL", "call"), (" Put ", "put"), ("c", "call"), ("puts", "put")],
    )
    def test_accepts_common_spellings(self, raw: str, expected: str) -> None:
        assert normalize_option_right(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "straddle"])
    def test_rejects_unknown(self, raw) -> None:
        with pytest.raises(ValueError, match="无法识别的期权方向"):
            normalize_option_right(raw)


class TestOccSymbol:
    def test_matches_the_21_char_convention(self) -> None:
        assert occ_symbol("aapl", call()) == "AAPL241220C00190000"

    def test_fractional_strike_keeps_three_decimals(self) -> None:
        assert occ_symbol("SPY", call(strike=190.5, option_type="put")).endswith("P00190500")

    def test_no_expiration_raises(self) -> None:
        with pytest.raises(ValueError, match="缺少到期日"):
            occ_symbol("AAPL", call(expiration=None))

    def test_result_is_archive_safe(self) -> None:
        """归档层的 symbol 白名单不接受空格 —— OCC 原始规范的补空格写法会被拒。"""
        from app.data.archive import validate_symbol

        assert validate_symbol(occ_symbol("AAPL", call())) == "AAPL241220C00190000"


class TestContractMapping:
    def test_maps_identity_fields(self) -> None:
        spec = option_contract_to_spec("AAPL", call())
        assert spec == ContractSpec(
            symbol="AAPL241220C00190000",
            asset_class=AssetClass.OPTION,
            multiplier=US_EQUITY_OPTION_MULTIPLIER,
            tick_size=0.01,
            underlying="AAPL",
            expiry=EXPIRY,
            strike=190.0,
            option_right="call",
        )

    def test_us_equity_option_multiplier_is_100(self) -> None:
        assert option_contract_to_spec("AAPL", call()).multiplier == 100.0

    def test_falls_back_to_occ_symbol_when_source_gives_none(self) -> None:
        spec = option_contract_to_spec("AAPL", call(contract_symbol=None))
        assert spec.symbol == "AAPL241220C00190000"

    def test_put_maps_to_put(self) -> None:
        spec = option_contract_to_spec("AAPL", call(option_type="put"))
        assert spec.option_right == "put"

    def test_missing_expiration_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError, match="缺少到期日"):
            option_contract_to_spec("AAPL", call(expiration=None))

    def test_empty_underlying_raises(self) -> None:
        with pytest.raises(ValueError, match="underlying 不能为空"):
            option_contract_to_spec("  ", call())

    def test_multiplier_is_overridable_for_non_standard_contracts(self) -> None:
        spec = option_contract_to_spec("AAPL", call(), multiplier=10.0)
        assert spec.multiplier == 10.0


class TestChainMapping:
    def test_calls_then_puts_in_source_order(self) -> None:
        chain = OptionsChainResponse(
            symbol="AAPL",
            calls=[call(contract_symbol="C1"), call(contract_symbol="C2", strike=200.0)],
            puts=[call(contract_symbol="P1", option_type="put")],
        )
        specs = chain_to_specs(chain)
        assert [s.symbol for s in specs] == ["C1", "C2", "P1"]
        assert [s.option_right for s in specs] == ["call", "call", "put"]

    def test_empty_chain_maps_to_empty_list(self) -> None:
        assert chain_to_specs(OptionsChainResponse(symbol="AAPL")) == []

    def test_every_spec_is_an_option(self) -> None:
        chain = OptionsChainResponse(symbol="AAPL", calls=[call()])
        assert all(s.asset_class is AssetClass.OPTION for s in chain_to_specs(chain))


class TestScopeIsRespected:
    """契约 §二/§三：只映射，不定价、不做希腊值、不新增数据源。"""

    def test_contract_spec_carries_no_greeks(self) -> None:
        spec = option_contract_to_spec("AAPL", call())
        for greek in ("delta", "gamma", "theta", "vega", "implied_volatility"):
            assert not hasattr(spec, greek)

    def test_mapper_never_imports_the_pricing_layer(self) -> None:
        import ast

        import app.data.providers.options_contracts as module

        tree = ast.parse(inspect.getsource(module))
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(name.startswith("app.quant") for name in imported)
