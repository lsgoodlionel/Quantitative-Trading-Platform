"""
归档层往返 asset_class（V4 Wave F-b / O5）

归档是「一个 (symbol, market, frequency) 一个列式文件」，asset_class 是新追加的**字符串**列。
两条必须钉住：
1. 写进去什么类别，读回来还是什么类别（含 npz / 列式字典两层）；
2. **老归档文件里没有这一列**，读回时必须回落到 EQUITY 而不是炸掉 ——
   否则本 wave 一上线，所有既有归档全部不可读。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.archive import ArchiveKey, NpzArchive
from app.data.archive.columns import COLUMNS, bars_to_columns, columns_to_bars
from app.data.models import AssetClass, Bar, Frequency, Market


def make_bar(day: int, asset_class: AssetClass = AssetClass.EQUITY, **extra) -> Bar:
    return Bar(
        time=datetime(2024, 3, day, 20, 0, tzinfo=UTC),
        symbol="AAPL",
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=99.0,
        high=102.0,
        low=97.0,
        close=100.0 + day,
        volume=1_000_000 + day,
        asset_class=asset_class,
        **extra,
    )


@pytest.fixture
def archive(tmp_path) -> NpzArchive:
    return NpzArchive(tmp_path / "archive")


@pytest.fixture
def key() -> ArchiveKey:
    return ArchiveKey(symbol="AAPL", market=Market.US, frequency=Frequency.DAY_1)


class TestColumnLayout:
    def test_asset_class_is_appended_at_the_end(self) -> None:
        """新列只能追加到列尾：插到中间会让既有归档文件的列整体错位。"""
        assert COLUMNS[-1] == "asset_class"
        assert COLUMNS[:-1] == (
            "time", "open", "high", "low", "close",
            "volume", "vwap", "turnover", "trade_count",
        )

    def test_columns_carry_the_enum_value_not_the_enum_repr(self) -> None:
        data = bars_to_columns([make_bar(1, AssetClass.FUTURES)])
        assert data["asset_class"] == ["futures"]


class TestColumnRoundTrip:
    @pytest.mark.parametrize("asset_class", list(AssetClass))
    def test_every_class_survives_the_column_round_trip(
        self, key, asset_class: AssetClass
    ) -> None:
        bars = [make_bar(1, asset_class), make_bar(2, asset_class)]
        assert columns_to_bars(key, bars_to_columns(bars)) == bars

    def test_mixed_classes_stay_aligned_per_row(self, key) -> None:
        bars = [make_bar(1, AssetClass.EQUITY), make_bar(2, AssetClass.CRYPTO)]
        restored = columns_to_bars(key, bars_to_columns(bars))
        assert [b.asset_class for b in restored] == [AssetClass.EQUITY, AssetClass.CRYPTO]

    def test_legacy_archive_without_the_column_falls_back_to_equity(self, key) -> None:
        """写于本 wave 之前的归档文件没有这一列 —— 必须读得出来，且是股票。"""
        legacy = bars_to_columns([make_bar(1)])
        legacy.pop("asset_class")
        restored = columns_to_bars(key, legacy)
        assert restored[0].asset_class is AssetClass.EQUITY

    def test_unknown_value_falls_back_to_equity_with_a_warning(self, key, caplog) -> None:
        """更新版本写入的新枚举值不该让整份老归档变得不可读。"""
        data = bars_to_columns([make_bar(1)])
        data["asset_class"] = ["bond"]
        with caplog.at_level("WARNING"):
            restored = columns_to_bars(key, data)
        assert restored[0].asset_class is AssetClass.EQUITY
        assert "asset_class" in caplog.text

    def test_blank_and_nan_placeholders_fall_back_to_equity(self, key) -> None:
        for placeholder in ("", "nan", None):
            data = bars_to_columns([make_bar(1)])
            data["asset_class"] = [placeholder]
            assert columns_to_bars(key, data)[0].asset_class is AssetClass.EQUITY


class TestNpzRoundTrip:
    @pytest.mark.parametrize("asset_class", list(AssetClass))
    def test_write_then_read_preserves_asset_class(
        self, archive, key, asset_class: AssetClass
    ) -> None:
        bars = [make_bar(1, asset_class, vwap=99.5, turnover=1.5e8, trade_count=42)]
        archive.write(key, bars)
        assert archive.read(key, date(2024, 3, 1), date(2024, 3, 31)) == bars

    def test_merge_of_two_writes_keeps_each_row_class(self, archive, key) -> None:
        archive.write(key, [make_bar(1, AssetClass.EQUITY)])
        archive.write(key, [make_bar(2, AssetClass.FUTURES)])
        read_back = archive.read(key, date(2024, 3, 1), date(2024, 3, 31))
        assert [b.asset_class for b in read_back] == [AssetClass.EQUITY, AssetClass.FUTURES]

    def test_string_column_is_not_coerced_to_float(self, archive, key) -> None:
        """回归防线：npz 后端曾把 time 以外的每一列都按 float64 落盘。"""
        import numpy as np

        archive.write(key, [make_bar(1, AssetClass.OPTION)])
        with np.load(archive.path_for(key), allow_pickle=False) as payload:
            assert payload["asset_class"].tolist() == ["option"]
