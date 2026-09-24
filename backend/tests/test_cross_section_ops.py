"""截面算子（M2）单元测试

覆盖契约 `docs/contracts/waveMb-cross-section-alpha101.md` 的验收项：
- 6 个 CS_* 算子各自的数值正确性（手算小样本对拍）
- 单标的时点返回 NaN（不是 1.0 也不是 0.0）
- NaN 标的被排除在截面之外，不影响其他标的的排名
- evaluate_formula 遇到 CS_* 抛 FormulaError 且错误信息指向 panel 模式
- evaluate_formula 对不含 CS_* 的公式逐值等于改动前的结果（硬编码期望值）
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.framework import (
    EqualWeightingPCM,
    FormulaFactorAlphaModel,
    FrameworkStrategy,
)
from app.quant.cross_section import (
    MIN_CROSS_SECTION_NAMES,
    cs_demean,
    cs_mean,
    cs_rank,
    cs_scale,
    cs_std,
    cs_zscore,
    to_long,
    to_wide,
)
from app.quant.formula_factor import (
    CS_OPS,
    OP_META,
    OPS,
    PANEL_PRESET_FORMULAS,
    PRESET_FORMULAS,
    FormulaError,
    evaluate_formula,
    evaluate_formula_panel,
    formula_requires_panel,
)
from app.strategy.factor_strategy import FactorStrategySpec, build_factor_strategy

# ── 固定样本 ──────────────────────────────────────────────────────

#: 手算对拍用的小截面：两个时点 × 四个标的
_WIDE = pd.DataFrame(
    {
        "AAA": [1.0, 10.0],
        "BBB": [2.0, -10.0],
        "CCC": [3.0, 20.0],
        "DDD": [4.0, -20.0],
    },
    index=pd.Index(["t1", "t2"], name="datetime"),
)
_WIDE.columns.name = "instrument"


def _row(frame: pd.DataFrame, key: str) -> list[float]:
    return [float(v) for v in frame.loc[key].to_numpy(dtype=float)]


def _make_ohlcv(n: int = 90, seed: int = 7) -> pd.DataFrame:
    """与基线快照脚本完全一致的确定性 OHLCV 构造器。"""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.integers(1_000_000, 4_000_000, n).astype(float)
    index = (
        pd.date_range("2024-01-01", periods=n, freq="D")
        .strftime("%Y-%m-%dT00:00:00")
        .tolist()
    )
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _make_panel(symbols: tuple[str, ...] = ("AAA", "BBB", "CCC"), n: int = 90) -> pd.DataFrame:
    """由多个确定性 OHLCV 帧拼成 (datetime, instrument) 面板。"""
    frames = []
    for i, sym in enumerate(symbols):
        frame = _make_ohlcv(n=n, seed=7 + i)
        frame.index.name = "datetime"
        frame = frame.assign(instrument=sym).set_index("instrument", append=True)
        frames.append(frame)
    return pd.concat(frames).sort_index()


# ── 六个算子的数值正确性 ──────────────────────────────────────────


class TestCrossSectionNumerics:
    def test_cs_rank_is_pct_rank_within_timestamp(self) -> None:
        # Arrange：t1 = [1,2,3,4] → pct rank = [0.25, 0.5, 0.75, 1.0]
        # Act
        result = cs_rank(_WIDE)

        # Assert
        assert _row(result, "t1") == [0.25, 0.5, 0.75, 1.0]
        assert _row(result, "t2") == [0.75, 0.5, 1.0, 0.25]

    def test_cs_mean_broadcasts_row_mean(self) -> None:
        # Arrange：t1 均值 (1+2+3+4)/4 = 2.5；t2 均值 0.0
        # Act
        result = cs_mean(_WIDE)

        # Assert
        assert _row(result, "t1") == [2.5, 2.5, 2.5, 2.5]
        assert _row(result, "t2") == [0.0, 0.0, 0.0, 0.0]

    def test_cs_std_uses_sample_ddof(self) -> None:
        # Arrange：t1 = [1,2,3,4]，样本标准差 = sqrt(5/3)
        expected = math.sqrt(5.0 / 3.0)

        # Act
        result = cs_std(_WIDE)

        # Assert
        assert _row(result, "t1") == pytest.approx([expected] * 4)

    def test_cs_demean_subtracts_row_mean(self) -> None:
        # Arrange / Act
        result = cs_demean(_WIDE)

        # Assert：t1 减去 2.5
        assert _row(result, "t1") == [-1.5, -0.5, 0.5, 1.5]
        # 去均值后截面和为 0
        assert result.sum(axis=1).abs().max() == pytest.approx(0.0)

    def test_cs_zscore_is_demean_over_std(self) -> None:
        # Arrange
        std = math.sqrt(5.0 / 3.0)
        expected = [-1.5 / std, -0.5 / std, 0.5 / std, 1.5 / std]

        # Act
        result = cs_zscore(_WIDE)

        # Assert
        assert _row(result, "t1") == pytest.approx(expected)

    def test_cs_scale_normalizes_abs_sum_to_one(self) -> None:
        # Arrange：t1 Σ|x| = 10；t2 Σ|x| = 60
        # Act
        result = cs_scale(_WIDE)

        # Assert
        assert _row(result, "t1") == pytest.approx([0.1, 0.2, 0.3, 0.4])
        assert list(result.abs().sum(axis=1)) == pytest.approx([1.0, 1.0])

    def test_cs_scale_all_zero_row_stays_zero(self) -> None:
        # Arrange：全 0 截面无法缩放到 Σ|x|=1
        wide = pd.DataFrame({"A": [0.0], "B": [0.0]}, index=["t1"])

        # Act
        result = cs_scale(wide)

        # Assert：保持 0（而非 NaN / 1/n）
        assert _row(result, "t1") == [0.0, 0.0]

    def test_cs_zscore_degenerate_section_is_nan_not_inf(self) -> None:
        # Arrange：截面全等 → 标准差 0
        wide = pd.DataFrame({"A": [5.0], "B": [5.0], "C": [5.0]}, index=["t1"])

        # Act
        result = cs_zscore(wide)

        # Assert
        assert result.loc["t1"].isna().all()


# ── 薄截面 / NaN 处理 ─────────────────────────────────────────────


class TestThinAndMissingSections:
    def test_single_name_section_returns_nan_for_every_op(self) -> None:
        # Arrange：t1 只有一个有效标的
        wide = pd.DataFrame({"A": [7.0], "B": [np.nan]}, index=["t1"])

        # Act / Assert：六个算子一律 NaN，绝不是 1.0 或 0.0
        for op in (cs_rank, cs_zscore, cs_demean, cs_scale, cs_mean, cs_std):
            result = op(wide)
            assert result.loc["t1"].isna().all(), f"{op.__name__} 未对单标的截面返回 NaN"

    def test_min_names_threshold_is_two(self) -> None:
        assert MIN_CROSS_SECTION_NAMES == 2

    def test_nan_name_excluded_without_shifting_other_ranks(self) -> None:
        # Arrange：同一组数值，其中一行把 D 置为 NaN
        full = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=["t1"])
        with_nan = pd.DataFrame(
            {"A": [1.0], "B": [2.0], "C": [3.0], "D": [np.nan]}, index=["t1"]
        )

        # Act
        ranked_full = cs_rank(full)
        ranked_with_nan = cs_rank(with_nan)

        # Assert：NaN 标的自身仍为 NaN，且不改变其余标的的排名
        assert math.isnan(ranked_with_nan.loc["t1", "D"])
        for name in ("A", "B", "C"):
            assert ranked_with_nan.loc["t1", name] == ranked_full.loc["t1", name]

    def test_nan_cell_stays_nan_in_broadcast_ops(self) -> None:
        # Arrange
        wide = pd.DataFrame({"A": [1.0], "B": [3.0], "C": [np.nan]}, index=["t1"])

        # Act
        result = cs_mean(wide)

        # Assert：缺数据的标的不会凭空得到一个均值
        assert result.loc["t1", "A"] == pytest.approx(2.0)
        assert math.isnan(result.loc["t1", "C"])


class TestLongWideRoundTrip:
    def test_to_wide_then_to_long_preserves_values(self) -> None:
        # Arrange
        panel = _make_panel(n=10)
        series = panel["close"]

        # Act
        restored = to_long(to_wide(series), series.index)

        # Assert
        pd.testing.assert_series_equal(restored, series.astype(float), check_names=False)

    def test_to_wide_rejects_single_level_index(self) -> None:
        with pytest.raises(ValueError, match="双层索引"):
            to_wide(pd.Series([1.0, 2.0], index=["a", "b"]))


# ── 算子注册与公式引擎接线 ────────────────────────────────────────


class TestOperatorRegistration:
    def test_cs_ops_are_not_in_single_symbol_vocabulary(self) -> None:
        # 遗传挖掘以 OPS 为搜索空间；CS_* 混进去会生成必然报错的公式
        single_names = {op.name for op in OPS}
        assert not single_names & {op.name for op in CS_OPS}

    def test_cs_ops_exposed_in_op_meta_with_panel_flag(self) -> None:
        by_name = {m["name"]: m for m in OP_META}
        for op in CS_OPS:
            assert by_name[op.name]["requires_panel"] is True
            assert by_name[op.name]["group"] == "截面"
        assert by_name["MOM20" if "MOM20" in by_name else "ADD"]["requires_panel"] is False

    def test_rank_is_labelled_as_time_series_not_cross_section(self) -> None:
        # 契约 1.3：RANK 的实现是 rolling(60) 时序近似，分类标签必须诚实
        rank_spec = next(op for op in OPS if op.name == "RANK")
        assert rank_spec.group == "时序"
        assert "时序近似" in rank_spec.label

    def test_formula_requires_panel_detects_cs_tokens(self) -> None:
        assert formula_requires_panel(["MOM20", "CS_RANK"]) is True
        assert formula_requires_panel(["MOM20", "ATR_RATIO", "DIV"]) is False

    def test_single_symbol_presets_never_contain_cs_tokens(self) -> None:
        # PRESET_FORMULAS 服务于单标的公式分析页；混进 CS_* 会让用户一点就报错
        for preset in PRESET_FORMULAS:
            assert not formula_requires_panel(preset["tokens"]), preset["name"]

    def test_panel_presets_are_all_cross_sectional_and_evaluable(self) -> None:
        # Arrange
        panel = _make_panel()

        # Act / Assert：面板预设必须真的用到截面算子，且能在 panel 上跑通
        assert PANEL_PRESET_FORMULAS
        for preset in PANEL_PRESET_FORMULAS:
            assert formula_requires_panel(preset["tokens"]), preset["name"]
            result = evaluate_formula_panel(panel, preset["tokens"])
            assert result.notna().any(), preset["name"]


class TestEvaluateFormulaRejectsCrossSection:
    def test_raises_formula_error_pointing_at_panel_mode(self) -> None:
        # Arrange
        df = _make_ohlcv(n=30)

        # Act / Assert
        with pytest.raises(FormulaError) as exc:
            evaluate_formula(df, ["MOM20", "CS_RANK"])
        message = str(exc.value)
        assert "CS_RANK" in message
        assert "evaluate_formula_panel" in message

    def test_error_lists_every_cs_token_used(self) -> None:
        df = _make_ohlcv(n=30)
        with pytest.raises(FormulaError) as exc:
            evaluate_formula(df, ["MOM20", "CS_RANK", "RET1", "CS_ZSCORE", "ADD"])
        assert "CS_RANK" in str(exc.value)
        assert "CS_ZSCORE" in str(exc.value)


# ── 既有单标的路径未受影响的证据（硬编码期望值）────────────────────

#: 改动前（commit 97a0ac9 基线）由 evaluate_formula 求得的末 5 个值。
#: 这是「既有路径逐值未变」的硬证据，任何回归都会在这里立刻暴露。
_BASELINE_TAILS: dict[str, tuple[list[str], list[float]]] = {
    "mom_div_atr": (
        ["MOM20", "ATR_RATIO", "DIV"],
        [-0.91698233926, -2.149672040326, -4.10426134768, -0.919946260783, 1.054706111687],
    ),
    "rsi_gate": (
        ["RSI14", "MOM20", "ZERO", "GATE"],
        [-0.008287128485, -0.019000201246, -0.037442762094, -0.008233299604, 0.009155292705],
    ),
    "decay_mom": (
        ["MOM5", "DECAY"],
        [-0.011184643779, -0.026628954499, -0.038191707469, -0.028493866538, -0.011853630684],
    ),
    "zscore_rev": (
        ["RET1", "NEG", "ZSCORE"],
        [1.122890504717, -0.065539359874, 0.452291847222, -1.420613673793, -0.803817990466],
    ),
    "slope_corr": (
        ["RET1", "LOG_VOL", "CORR20"],
        [-0.119524359478, -0.217416137242, -0.205987102669, -0.03312577107, 0.064555417268],
    ),
    "vol_mul": (
        ["MOM20", "VOL_CHG", "MUL"],
        [-0.002924713428, 0.000435980103, -0.009177095846, -0.003016282016, 0.001814692587],
    ),
    "mfi_smooth": (
        ["MFI14", "TS_MEAN5"],
        [-0.316444579967, -0.299038822338, -0.338256616941, -0.374262851977, -0.361769097021],
    ),
    "wma_ema": (
        ["PX_SMA20", "WMA10", "PX_SMA20", "EMA20", "SUB"],
        [0.002042702054, 0.001417576386, 0.000477172634, 0.000724936398, 0.001429068627],
    ),
    "slope20": (
        ["OBV_MOM", "SLOPE20"],
        [0.115782224479, 0.101978762214, 0.094827852463, 0.072980266357, 0.047125580105],
    ),
}


class TestExistingSingleSymbolPathUnchanged:
    @pytest.mark.parametrize("case", sorted(_BASELINE_TAILS))
    def test_matches_pre_change_baseline_value_for_value(self, case: str) -> None:
        # Arrange
        tokens, expected = _BASELINE_TAILS[case]
        df = _make_ohlcv()

        # Act
        actual = evaluate_formula(df, tokens).to_numpy(dtype=float)[-5:]

        # Assert
        assert list(actual) == pytest.approx(expected, abs=1e-10)

    def test_output_index_still_matches_input_frame(self) -> None:
        df = _make_ohlcv(n=40)
        result = evaluate_formula(df, ["MOM5", "DECAY"])
        assert list(result.index) == list(df.index)


# ── panel 求值路径 ────────────────────────────────────────────────


class TestEvaluateFormulaPanel:
    def test_cs_rank_of_momentum_is_bounded_and_indexed(self) -> None:
        # Arrange
        panel = _make_panel()

        # Act
        result = evaluate_formula_panel(panel, ["MOM20", "CS_RANK"])

        # Assert
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == list(panel.index)
        valid = result.dropna()
        assert not valid.empty
        assert valid.min() > 0.0
        assert valid.max() <= 1.0

    def test_each_timestamp_has_a_top_rank_of_one(self) -> None:
        # Arrange
        panel = _make_panel()

        # Act
        result = evaluate_formula_panel(panel, ["MOM20", "CS_RANK"])

        # Assert：每个有效截面的最大 pct rank 恒为 1
        maxima = result.dropna().groupby(level="datetime").max().to_numpy(dtype=float)
        assert maxima.size > 0
        np.testing.assert_allclose(maxima, 1.0)

    def test_time_series_only_formula_matches_single_symbol_path(self) -> None:
        # Arrange：不含 CS_* 的公式，panel 路径应与逐标的单独求值完全一致
        panel = _make_panel()
        tokens = ["MOM20", "ATR_RATIO", "DIV"]

        # Act
        panel_result = evaluate_formula_panel(panel, tokens)

        # Assert
        for symbol in ("AAA", "BBB", "CCC"):
            single = evaluate_formula(
                panel.xs(symbol, level="instrument"), tokens
            ).to_numpy(dtype=float)
            from_panel = panel_result.xs(symbol, level="instrument").to_numpy(dtype=float)
            np.testing.assert_allclose(from_panel, single, rtol=1e-12, equal_nan=True)

    def test_cs_demean_sums_to_zero_per_timestamp(self) -> None:
        # Arrange
        panel = _make_panel()

        # Act
        result = evaluate_formula_panel(panel, ["RET1", "CS_DEMEAN"]).dropna()

        # Assert
        sums = result.groupby(level="datetime").sum().abs()
        assert float(sums.max()) < 1e-9

    def test_single_instrument_panel_yields_all_nan(self) -> None:
        # Arrange：只有一个标的 ⇒ 截面不成立
        panel = _make_panel(symbols=("AAA",))

        # Act
        result = evaluate_formula_panel(panel, ["MOM20", "CS_RANK"])

        # Assert
        assert result.isna().all()

    def test_rejects_non_panel_frame(self) -> None:
        with pytest.raises(FormulaError, match="双层索引"):
            evaluate_formula_panel(_make_ohlcv(n=20), ["MOM20", "CS_RANK"])

    def test_rejects_panel_missing_ohlcv_columns(self) -> None:
        panel = _make_panel(n=20).drop(columns=["volume"])
        with pytest.raises(FormulaError, match="volume"):
            evaluate_formula_panel(panel, ["MOM20", "CS_RANK"])

    def test_rejects_empty_and_overlong_formula(self) -> None:
        panel = _make_panel(n=20)
        with pytest.raises(FormulaError, match="公式为空"):
            evaluate_formula_panel(panel, [])
        with pytest.raises(FormulaError, match="过长"):
            evaluate_formula_panel(panel, ["RET1"] * 40)

    def test_rejects_unbalanced_formula(self) -> None:
        panel = _make_panel(n=20)
        with pytest.raises(FormulaError, match="不平衡"):
            evaluate_formula_panel(panel, ["MOM20", "RET1", "CS_RANK"])

    def test_rejects_unknown_token(self) -> None:
        panel = _make_panel(n=20)
        with pytest.raises(FormulaError, match="未知 token"):
            evaluate_formula_panel(panel, ["NOPE"])

    def test_jump_operator_matches_single_symbol_semantics(self) -> None:
        # JUMP 用的是整段序列的均值/标准差；panel 模式必须逐标的算，不能串台
        panel = _make_panel()
        panel_result = evaluate_formula_panel(panel, ["BB_POS", "JUMP"])
        for symbol in ("AAA", "BBB", "CCC"):
            single = evaluate_formula(
                panel.xs(symbol, level="instrument"), ["BB_POS", "JUMP"]
            ).to_numpy(dtype=float)
            np.testing.assert_allclose(
                panel_result.xs(symbol, level="instrument").to_numpy(dtype=float),
                single,
                rtol=1e-12,
                equal_nan=True,
            )

    def test_unsorted_panel_input_still_returns_caller_row_order(self) -> None:
        # Arrange：打乱行序（真实来源未必有序）
        panel = _make_panel(n=40)
        shuffled = panel.iloc[::-1]

        # Act
        result = evaluate_formula_panel(shuffled, ["MOM20", "CS_RANK"])

        # Assert：返回索引与调用方传入的一致，值与有序输入一致
        assert list(result.index) == list(shuffled.index)
        ordered = evaluate_formula_panel(panel, ["MOM20", "CS_RANK"])
        np.testing.assert_allclose(
            result.reindex(panel.index).to_numpy(dtype=float),
            ordered.to_numpy(dtype=float),
            equal_nan=True,
        )


# ── CS_* 公式一路走到成交（M2 的价值兑现点）──────────────────────

SYMBOLS: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD")


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _universe_bars(n: int = 120) -> dict[str, list[Bar]]:
    bars: dict[str, list[Bar]] = {}
    start = datetime(2024, 1, 1)
    for i, symbol in enumerate(SYMBOLS):
        frame = _make_ohlcv(n=n, seed=300 + i)
        bars[symbol] = [
            Bar(
                time=start + timedelta(days=k),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume),
            )
            for k, row in enumerate(frame.itertuples())
        ]
    return bars


def _backtest_config() -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        initial_cash=1_000_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        commission_model=_ZeroCommission(),
    )


class TestCrossSectionFormulaReachesTrades:
    def test_alpha_model_flags_cs_formula_as_panel_mode(self) -> None:
        panel_alpha = FormulaFactorAlphaModel(tokens=["MOM20", "CS_RANK"])
        plain_alpha = FormulaFactorAlphaModel(tokens=["MOM20"])
        assert panel_alpha._needs_panel is True
        assert plain_alpha._needs_panel is False

    def test_cs_formula_runs_a_full_portfolio_backtest(self) -> None:
        # Arrange：截面公式必须能一路走到成交，这是 M2 对策略侧的兑现
        strategy = FrameworkStrategy(
            alpha=FormulaFactorAlphaModel(
                tokens=["MOM20", "CS_RANK"], min_history=30, long_quantile=0.5
            ),
            portfolio_construction=EqualWeightingPCM(),
        )

        # Act
        result = PortfolioBacktestEngine(_backtest_config()).run(
            strategy, _universe_bars()
        )

        # Assert
        assert result.fills, "截面公式应当能产生真实成交"
        assert math.isfinite(result.final_value)

    def test_factor_strategy_spec_accepts_cs_formula(self) -> None:
        # Arrange / Act：A-a 的纯数据 spec 必须能承载截面公式
        spec = FactorStrategySpec(
            formula="MOM20 CS_RANK", universe=SYMBOLS, long_quantile=0.5
        )
        strategy = build_factor_strategy(spec)

        # Assert
        assert isinstance(strategy.alpha.factor, FormulaFactorAlphaModel)
        assert strategy.alpha.factor._needs_panel is True

    def test_panel_scores_are_bounded_ranks_not_raw_momentum(self) -> None:
        # Arrange：直接检查打分本身落在 CS_RANK 的 (0,1] 值域内
        alpha = FormulaFactorAlphaModel(
            tokens=["MOM20", "CS_RANK"], min_history=30, long_quantile=0.75
        )
        captured: list[dict[str, float]] = []
        original = alpha._panel_scores

        def _spy(ctx):
            scores = original(ctx)
            if scores:
                captured.append(scores)
            return scores

        alpha._panel_scores = _spy  # type: ignore[method-assign]
        strategy = FrameworkStrategy(
            alpha=alpha, portfolio_construction=EqualWeightingPCM()
        )

        # Act
        PortfolioBacktestEngine(_backtest_config()).run(strategy, _universe_bars())

        # Assert
        assert captured, "面板打分路径从未被触发"
        for scores in captured:
            assert all(0.0 < v <= 1.0 for v in scores.values()), scores

    def test_short_universe_yields_no_insights(self) -> None:
        # Arrange：只有一个标的时截面不成立，应当安静地不出观点而非抛错
        alpha = FormulaFactorAlphaModel(
            tokens=["MOM20", "CS_RANK"], min_history=30, long_quantile=0.5
        )
        strategy = FrameworkStrategy(
            alpha=alpha, portfolio_construction=EqualWeightingPCM()
        )
        single = {"AAA": _universe_bars()["AAA"]}

        # Act
        result = PortfolioBacktestEngine(_backtest_config()).run(strategy, single)

        # Assert
        assert not result.fills
        assert math.isfinite(result.final_value)
