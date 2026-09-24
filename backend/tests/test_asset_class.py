"""
资产类别标签（V4 Wave F-b / O5）

本文件钉住的是「**加了字段但什么都没改变**」——
`Bar` 被回归基线的 1364 笔成交钉着，只要 asset_class 有一处没做到「纯标签」，
这里就会红，而不是等到回归基线变色才发现。
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest

from app.data.models import Bar, Frequency, Market
from app.data.models.asset_class import DEFAULT_ASSET_CLASS, AssetClass

BASE_TIME = datetime(2024, 1, 15, 9, 30, tzinfo=UTC)


def make_bar(**overrides) -> Bar:
    defaults = {
        "time": BASE_TIME,
        "symbol": "AAPL",
        "market": Market.US,
        "frequency": Frequency.DAY_1,
        "open": 180.0,
        "high": 185.0,
        "low": 179.0,
        "close": 183.5,
        "volume": 1_000_000,
    }
    return Bar(**{**defaults, **overrides})


class TestAssetClassEnum:
    def test_covers_the_five_contracted_classes(self) -> None:
        assert {a.value for a in AssetClass} == {
            "equity", "futures", "option", "fx", "crypto"
        }

    def test_is_str_enum_so_it_serializes_as_plain_string(self) -> None:
        # Market / Frequency 都是 str 枚举，JSON 序列化直接得字符串；
        # asset_class 若不是，API 层会多出一个 "AssetClass.EQUITY" 之类的坏值。
        assert AssetClass.FUTURES == "futures"
        assert f"{AssetClass.FUTURES.value}" == "futures"

    def test_default_is_equity(self) -> None:
        assert DEFAULT_ASSET_CLASS is AssetClass.EQUITY


class TestBarAssetClassField:
    def test_bar_without_asset_class_is_equity(self) -> None:
        assert make_bar().asset_class is AssetClass.EQUITY

    def test_field_has_a_default_so_no_existing_construction_site_needs_changing(self) -> None:
        """反射断言：字段必须有默认值，否则全仓既有 Bar(...) 构造点会集体报错。"""
        field = next(f for f in dataclasses.fields(Bar) if f.name == "asset_class")
        assert field.default is AssetClass.EQUITY
        assert field.default_factory is dataclasses.MISSING

    def test_field_is_last_so_positional_construction_still_works(self) -> None:
        """字段必须排在最后：插到中间会让所有位置参数构造静默错位。"""
        names = [f.name for f in dataclasses.fields(Bar)]
        assert names[-1] == "asset_class"

    def test_positional_construction_still_yields_equity(self) -> None:
        bar = Bar(BASE_TIME, "AAPL", Market.US, Frequency.DAY_1, 180.0, 185.0, 179.0, 183.5, 100)
        assert bar.asset_class is AssetClass.EQUITY

    def test_signature_default_is_equity(self) -> None:
        param = inspect.signature(Bar).parameters["asset_class"]
        assert param.default is AssetClass.EQUITY

    @pytest.mark.parametrize("asset_class", list(AssetClass))
    def test_any_valid_value_constructs_without_error(self, asset_class: AssetClass) -> None:
        """asset_class 不参与 __post_init__ 校验：任何合法值都不该让构造失败。"""
        assert make_bar(asset_class=asset_class).asset_class is asset_class

    def test_post_init_validation_is_unchanged_by_asset_class(self) -> None:
        """既有的两条校验只看 high/low 与 volume，与 asset_class 无关。"""
        for asset_class in AssetClass:
            with pytest.raises(ValueError, match="high.*low"):
                make_bar(high=170.0, low=185.0, asset_class=asset_class)
            with pytest.raises(ValueError, match="[Nn]egative volume"):
                make_bar(volume=-1, asset_class=asset_class)

    def test_asset_class_does_not_affect_derived_properties(self) -> None:
        equity = make_bar()
        futures = make_bar(asset_class=AssetClass.FUTURES)
        assert (equity.mid, equity.change, equity.change_pct) == (
            futures.mid, futures.change, futures.change_pct
        )

    def test_bars_differing_only_in_asset_class_are_not_equal(self) -> None:
        """它确实是 Bar 身份的一部分，不是被 dataclass 忽略掉的装饰字段。"""
        assert make_bar() != make_bar(asset_class=AssetClass.CRYPTO)

    def test_bar_stays_frozen(self) -> None:
        bar = make_bar()
        with pytest.raises(dataclasses.FrozenInstanceError):
            bar.asset_class = AssetClass.FX  # type: ignore[misc]

    def test_replace_preserves_asset_class(self) -> None:
        """全仓的复权/拼接都走 dataclasses.replace，标签不能在中途掉回默认值。"""
        bar = make_bar(asset_class=AssetClass.FUTURES)
        assert dataclasses.replace(bar, close=190.0).asset_class is AssetClass.FUTURES


class TestNotWiredIntoTrading:
    """契约 §三：本期绝对不把 asset_class 接进撮合/风控。"""

    def test_backtest_broker_never_reads_asset_class(self) -> None:
        from app.engine.backtest import broker

        assert "asset_class" not in inspect.getsource(broker)
