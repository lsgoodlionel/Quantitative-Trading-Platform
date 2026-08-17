"""
连续合约拼接（V4 Wave F-b / O5）

两种口径的语义差异是这一项的核心，所以正反两面都要钉住：
- back_adjust：衔接处**无跳空**，但历史价**不是真实成交价**；
- raw：历史价是真实成交价，衔接处**确实有跳空**（断言它有，而不是回避它）。
只钉一半就等于允许后来的人把两者悄悄改成同一个东西。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.data.continuous_futures import stitch_continuous
from app.data.models import AssetClass, Bar, ContractSpec, Frequency, Market

START = datetime(2024, 1, 2, tzinfo=UTC)
GAP_TOLERANCE = 1e-9


def futures_spec(symbol: str, expiry: date) -> ContractSpec:
    return ContractSpec(
        symbol=symbol,
        asset_class=AssetClass.FUTURES,
        multiplier=1000.0,
        tick_size=0.01,
        underlying="CL",
        expiry=expiry,
    )


def make_bars(symbol: str, closes: list[float], *, day0: int = 0, vwap: bool = False) -> list[Bar]:
    """每根 bar 的 open = 前一根 close（段内本就连续），high/low 各留 1.0 余量。"""
    bars: list[Bar] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        bars.append(
            Bar(
                time=START + timedelta(days=day0 + i),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=open_,
                high=max(open_, close) + 1.0,
                low=min(open_, close) - 1.0,
                close=close,
                volume=1_000 + i,
                vwap=(open_ + close) / 2 if vwap else None,
                turnover=close * 1_000,
                asset_class=AssetClass.FUTURES,
            )
        )
    return bars


@pytest.fixture
def two_contracts() -> list[tuple[ContractSpec, list[Bar]]]:
    """老合约收在 100，新合约从 110 开盘 —— 换月处一个 +10 的跳空。"""
    old = (futures_spec("CLM24", date(2024, 6, 20)), make_bars("CLM24", [98.0, 99.0, 100.0]))
    new = (
        futures_spec("CLZ24", date(2024, 12, 20)),
        make_bars("CLZ24", [110.0, 112.0, 111.0], day0=10),
    )
    return [old, new]


def junction_gap(bars: list[Bar], boundary: int) -> float:
    """衔接处跳空 = 新段首根 open − 老段末根 close。"""
    return bars[boundary].open - bars[boundary - 1].close


class TestEdgeCases:
    def test_empty_input_returns_empty_list(self) -> None:
        assert stitch_continuous([]) == []

    def test_all_empty_segments_return_empty_list(self) -> None:
        spec = futures_spec("CLZ24", date(2024, 12, 20))
        assert stitch_continuous([(spec, [])]) == []

    def test_single_contract_returned_unchanged(self) -> None:
        spec = futures_spec("CLZ24", date(2024, 12, 20))
        bars = make_bars("CLZ24", [100.0, 101.0, 99.0])
        for method in ("raw", "back_adjust"):
            assert stitch_continuous([(spec, bars)], method=method) == bars

    def test_empty_segments_are_skipped_not_fatal(self, two_contracts) -> None:
        spec = futures_spec("CLU24", date(2024, 9, 20))
        padded = [two_contracts[0], (spec, []), two_contracts[1]]
        assert stitch_continuous(padded) == stitch_continuous(two_contracts)

    def test_unknown_method_raises(self, two_contracts) -> None:
        with pytest.raises(ValueError, match="未知拼接口径"):
            stitch_continuous(two_contracts, method="panama")  # type: ignore[arg-type]

    def test_malformed_input_raises(self) -> None:
        with pytest.raises(TypeError, match="二元组"):
            stitch_continuous(["CLZ24"])  # type: ignore[list-item]

    def test_input_bars_are_never_mutated(self, two_contracts) -> None:
        before = [list(bars) for _, bars in two_contracts]
        stitch_continuous(two_contracts, method="back_adjust")
        assert [list(bars) for _, bars in two_contracts] == before


class TestRaw:
    def test_raw_preserves_every_original_bar(self, two_contracts) -> None:
        out = stitch_continuous(two_contracts, method="raw")
        assert out == [*two_contracts[0][1], *two_contracts[1][1]]

    def test_raw_junction_really_has_a_gap(self, two_contracts) -> None:
        """跳空是 raw 口径的特性，不是 bug —— 断言它确实存在。"""
        out = stitch_continuous(two_contracts, method="raw")
        assert junction_gap(out, boundary=3) == pytest.approx(10.0)

    def test_raw_sorts_within_a_segment(self) -> None:
        spec = futures_spec("CLZ24", date(2024, 12, 20))
        bars = make_bars("CLZ24", [100.0, 101.0, 102.0])
        shuffled = [bars[2], bars[0], bars[1]]
        assert stitch_continuous([(spec, shuffled)], method="raw") == bars


class TestBackAdjust:
    def test_is_the_default_method(self, two_contracts) -> None:
        assert stitch_continuous(two_contracts) == stitch_continuous(
            two_contracts, method="back_adjust"
        )

    def test_junction_has_no_gap(self, two_contracts) -> None:
        out = stitch_continuous(two_contracts, method="back_adjust")
        assert junction_gap(out, boundary=3) == pytest.approx(0.0, abs=GAP_TOLERANCE)

    def test_newest_contract_keeps_real_prices(self, two_contracts) -> None:
        """后向回填以最新合约为基准：最后一段一个字节都不能动。"""
        out = stitch_continuous(two_contracts, method="back_adjust")
        assert out[3:] == two_contracts[1][1]

    def test_history_is_no_longer_the_real_traded_price(self, two_contracts) -> None:
        """语义差异的另一半：早期价格被整体平移，不再是当时的真实成交价。"""
        out = stitch_continuous(two_contracts, method="back_adjust")
        raw = stitch_continuous(two_contracts, method="raw")
        assert out[:3] != raw[:3]
        assert [b.close for b in out[:3]] == pytest.approx([108.0, 109.0, 110.0])

    def test_within_segment_price_differences_are_preserved(self, two_contracts) -> None:
        """加法平移的定义性质：段内任意两点的**价差**不变。"""
        out = stitch_continuous(two_contracts, method="back_adjust")
        raw = stitch_continuous(two_contracts, method="raw")
        assert (out[2].close - out[0].close) == pytest.approx(raw[2].close - raw[0].close)

    def test_ohlc_all_shift_by_the_same_offset(self, two_contracts) -> None:
        out = stitch_continuous(two_contracts, method="back_adjust")
        raw = stitch_continuous(two_contracts, method="raw")
        for adjusted, original in zip(out[:3], raw[:3], strict=True):
            offsets = {
                round(adjusted.open - original.open, 9),
                round(adjusted.high - original.high, 9),
                round(adjusted.low - original.low, 9),
                round(adjusted.close - original.close, 9),
            }
            assert offsets == {10.0}

    def test_vwap_follows_the_shift(self) -> None:
        old = (futures_spec("CLM24", date(2024, 6, 20)), make_bars("CLM24", [100.0], vwap=True))
        new = (
            futures_spec("CLZ24", date(2024, 12, 20)),
            make_bars("CLZ24", [110.0], day0=10, vwap=True),
        )
        out = stitch_continuous([old, new], method="back_adjust")
        assert out[0].vwap == pytest.approx(old[1][0].vwap + 10.0)

    def test_volume_and_turnover_are_untouched(self, two_contracts) -> None:
        """量不随价平移；turnover 刻意不动（加法平移下无法自洽，见 docstring）。"""
        out = stitch_continuous(two_contracts, method="back_adjust")
        raw = stitch_continuous(two_contracts, method="raw")
        assert [b.volume for b in out] == [b.volume for b in raw]
        assert [b.turnover for b in out] == [b.turnover for b in raw]

    def test_symbol_and_asset_class_are_preserved(self, two_contracts) -> None:
        out = stitch_continuous(two_contracts, method="back_adjust")
        assert [b.symbol for b in out[:3]] == ["CLM24"] * 3
        assert all(b.asset_class is AssetClass.FUTURES for b in out)

    def test_three_contracts_chain_without_any_gap(self) -> None:
        """偏移必须**累加**：只补最近一次换月，更早的接缝会留下跳空。"""
        segments = [
            (futures_spec("CLH24", date(2024, 3, 20)), make_bars("CLH24", [50.0, 52.0])),
            (futures_spec("CLM24", date(2024, 6, 20)), make_bars("CLM24", [70.0, 71.0], day0=10)),
            (futures_spec("CLZ24", date(2024, 12, 20)), make_bars("CLZ24", [90.0, 95.0], day0=20)),
        ]
        out = stitch_continuous(segments, method="back_adjust")
        for boundary in (2, 4):
            assert junction_gap(out, boundary) == pytest.approx(0.0, abs=GAP_TOLERANCE)
        # 最新一段保持真实价，早期段被累计平移 (90-71) + (70-52) = 37
        assert out[-1].close == pytest.approx(95.0)
        assert out[0].close == pytest.approx(50.0 + 37.0)

    def test_downward_roll_shifts_history_up(self) -> None:
        """新合约贴水（新价 < 老价）时偏移为负，历史价被下调。"""
        segments = [
            (futures_spec("CLM24", date(2024, 6, 20)), make_bars("CLM24", [100.0])),
            (futures_spec("CLZ24", date(2024, 12, 20)), make_bars("CLZ24", [90.0], day0=10)),
        ]
        out = stitch_continuous(segments, method="back_adjust")
        assert out[0].close == pytest.approx(90.0)
        assert junction_gap(out, boundary=1) == pytest.approx(0.0, abs=GAP_TOLERANCE)

    def test_adjusted_bars_still_satisfy_high_ge_low(self, two_contracts) -> None:
        """加法平移不改变 high/low 次序，因此绝不会触发 Bar 的 __post_init__ 校验。"""
        for bar in stitch_continuous(two_contracts, method="back_adjust"):
            assert bar.high >= bar.low

    def test_negative_prices_are_possible_and_warned(self, caplog) -> None:
        """Panama 法的已知代价：持续升水会把早期价格推到 0 以下，此时记 warning。"""
        segments = [
            (futures_spec("CLM24", date(2024, 6, 20)), make_bars("CLM24", [100.0])),
            (futures_spec("CLZ24", date(2024, 12, 20)), make_bars("CLZ24", [0.5], day0=10)),
        ]
        with caplog.at_level("WARNING"):
            out = stitch_continuous(segments, method="back_adjust")
        assert out[0].close == pytest.approx(0.5)
        assert out[0].low < 0
        assert "回填后" in caplog.text


class TestSemanticsAreDocumented:
    """契约要求语义差异必须写进 docstring —— 这条也钉住，防止后人删注释。"""

    def test_docstring_states_both_sides_of_the_tradeoff(self) -> None:
        doc = stitch_continuous.__doc__ or ""
        assert "无跳空" in doc
        assert "不再是当时的真实成交价" in doc

    def test_module_docstring_warns_against_mixing_with_equity_adjustments(self) -> None:
        import app.data.continuous_futures as module

        doc = module.__doc__ or ""
        assert "adjustments.py" in doc
        assert "不要混用" in doc
