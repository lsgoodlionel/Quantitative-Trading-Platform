"""Alpha101 因子集（M3）单元测试

覆盖契约 `docs/contracts/waveMb-cross-section-alpha101.md` 的验收项：
- 已实现的每个 alpha 至少一个形状 + 取值范围断言
- 注册进因子库后能被 generate_factor_library() 拿到
- 能走通 V3 A-a 的因子库回测入口（端到端一个用例）

外加：跳过清单与实现清单互不重叠、实现与表达式原文表严格同键
（防「名字叫 alpha42 但算法不是 alpha42」这类最危险的失败模式）。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from pathlib import Path

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
    FrameworkStrategy,
    LibraryFactorAlphaModel,
    PanelLibraryFactorAlphaModel,
)
from app.quant.factor_lib.alpha101 import (
    ALPHA101_GROUP,
    ALPHA_FUNCTIONS,
    SKIPPED_ALPHAS,
    build_fields,
    compute_alpha,
)
from app.quant.factor_lib.alpha101_exprs import ALPHA_EXPRESSIONS
from app.quant.factor_lib.loader import (
    attach_panel_factors,
    build_feature_fn,
    generate_factor_library,
    split_by_mode,
)
from app.quant.factor_lib.ranking import rank_factor_library
from app.quant.panel import attach_forward_label, bars_to_panel
from app.strategy.factor_strategy import FactorStrategySpec, build_factor_strategy

SYMBOLS: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
#: 面板长度：要覆盖到 250 期窗口的 alpha（alpha19 / alpha39），必须足够长
PANEL_BARS = 320


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


# ── 合成数据 ──────────────────────────────────────────────────────


def _synthetic_frame(seed: int, n: int = PANEL_BARS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    index = pd.Index(
        pd.date_range("2023-01-01", periods=n, freq="D")
        .strftime("%Y-%m-%dT00:00:00")
        .tolist(),
        name="datetime",
    )
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    frames = []
    for i, symbol in enumerate(SYMBOLS):
        frame = _synthetic_frame(seed=100 + i)
        frames.append(frame.assign(instrument=symbol).set_index("instrument", append=True))
    return pd.concat(frames).sort_index()


def _universe_bars(n: int = 120) -> dict[str, list[Bar]]:
    """回测入口用的多标的 bar 序列（比因子面板短，跑得快）。"""
    bars: dict[str, list[Bar]] = {}
    start = datetime(2024, 1, 1)
    for i, symbol in enumerate(SYMBOLS):
        frame = _synthetic_frame(seed=200 + i, n=n)
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


# ── 实现清单的自洽性 ──────────────────────────────────────────────


class TestRegistryConsistency:
    def test_implementations_and_expressions_share_exact_key_set(self) -> None:
        # 实现与原文表必须严格一一对应，否则就会出现「有实现没原文」或反之
        assert set(ALPHA_FUNCTIONS) == set(ALPHA_EXPRESSIONS)

    def test_skipped_and_implemented_do_not_overlap(self) -> None:
        assert not set(SKIPPED_ALPHAS) & set(ALPHA_FUNCTIONS)

    def test_every_alpha_is_either_implemented_or_explained(self) -> None:
        # Alpha101 共 101 条：要么实现，要么在跳过清单里给出原因，不留空白
        covered = set(ALPHA_FUNCTIONS) | set(SKIPPED_ALPHAS)
        assert covered == {f"alpha{n}" for n in range(1, 102)}

    def test_every_skip_reason_is_non_empty(self) -> None:
        assert all(reason.strip() for reason in SKIPPED_ALPHAS.values())

    def test_no_vwap_dependency_leaked_into_implementations(self) -> None:
        # 平台面板只有 OHLCV；实现里出现 vwap 就说明混进了近似顶替
        for name, (expr, _window) in ALPHA_EXPRESSIONS.items():
            assert "vwap" not in expr, f"{name} 的表达式仍引用 vwap"


# 移植来源（vnpy，MIT）。仓库外的参考目录，CI 上不存在时跳过 —— 这条用例保护的是
# 「有人改了表达式表却没同步原文」这一场景，那必然发生在有 refs 的开发机上。
_VNPY_SOURCE = Path(
    "/Users/lionel/Develop/LHJY/refs/vnpy-LHJY/vnpy/alpha/dataset/datasets/alpha_101.py"
)
_RETURNS_EXPANDED = "(close / ts_delay(close, 1) - 1)"


def _normalize_expression(expr: str) -> str:
    """抹掉不改变语义的写法差异：空白、分组括号、`x*-1` 与 `-1*x` 的乘法交换。"""
    expr = expr.replace("returns", _RETURNS_EXPANDED)
    expr = re.sub(r"\s+", "", expr)
    expr = expr.replace("(-1)", "-1").replace("(", "").replace(")", "")
    return re.sub(r"^(.*?)\*-1$", r"-1*\1", expr)


def _vnpy_expressions() -> dict[str, str]:
    """从 vnpy 源码里抠出 add_feature 的表达式字面量（含被注释掉的条目）。"""
    raw = _VNPY_SOURCE.read_text()
    pattern = re.compile(
        r'self\.add_feature\(\s*"(alpha\d+)"\s*,\s*f?"(.*?)"\s*\)\s*$',
        re.MULTILINE | re.DOTALL,
    )
    return {
        name: body.replace("{returns_expr}", "returns")
        for name, body in pattern.findall(raw)
    }


@pytest.mark.skipif(not _VNPY_SOURCE.exists(), reason="移植来源 refs/vnpy 不在本机")
class TestExpressionsMatchPortSource:
    """表达式表 vs 移植来源的机械对拍 —— 防「名字对了算法不是」的最后一道闸。"""

    def test_every_ported_expression_matches_vnpy_verbatim(self) -> None:
        # Arrange
        source = _vnpy_expressions()

        # Act / Assert
        for name, (expr, _window) in ALPHA_EXPRESSIONS.items():
            assert name in source, f"{name} 在移植来源中不存在"
            assert _normalize_expression(expr) == _normalize_expression(source[name]), (
                f"{name} 与移植来源不一致"
            )

    def test_skipped_set_covers_exactly_what_was_not_ported(self) -> None:
        # Arrange：来源里出现过但我们没实现的，必须全部落在跳过清单里
        source = _vnpy_expressions()
        not_ported = set(source) - set(ALPHA_EXPRESSIONS)

        # Assert
        assert not_ported <= set(SKIPPED_ALPHAS)
        # alpha56 在来源里连表达式都没有（缺 cap 字段），只能靠跳过清单交代
        assert "alpha56" in SKIPPED_ALPHAS


# ── 每个 alpha 的形状与取值范围 ───────────────────────────────────


class TestAlphaShapeAndRange:
    @pytest.mark.parametrize("name", sorted(ALPHA_FUNCTIONS))
    def test_shape_index_and_finiteness(self, name: str, panel: pd.DataFrame) -> None:
        # Act
        values = compute_alpha(panel, name)

        # Assert：形状与索引与面板严格一致
        assert isinstance(values, pd.Series)
        assert len(values) == len(panel)
        assert list(values.index) == list(panel.index)
        # 取值范围：非空、有限、无 ±inf
        valid = values.dropna()
        assert not valid.empty, f"{name} 在 {PANEL_BARS} 根 bar 上全为 NaN"
        assert np.isfinite(valid.to_numpy(dtype=float)).all(), f"{name} 含 inf"

    @pytest.mark.parametrize(
        ("name", "low", "high"),
        [
            # cs_rank 值域 (0,1]，故 cs_rank(x) - 0.5 ∈ (-0.5, 0.5]
            ("alpha1", -0.5, 0.5),
            # -1 * ts_rank(...)，ts_rank ∈ (0,1]
            ("alpha4", -1.0, 0.0),
            # -1 * cs_rank(...)
            ("alpha8", -1.0, 0.0),
            ("alpha13", -1.0, 0.0),
            ("alpha16", -1.0, 0.0),
            # cs_rank(...) 直接输出
            ("alpha10", 0.0, 1.0),
            ("alpha33", 0.0, 1.0),
            ("alpha34", 0.0, 1.0),
            # 相关系数 ∈ [-1, 1]
            ("alpha3", -1.0, 1.0),
            ("alpha6", -1.0, 1.0),
            ("alpha44", -1.0, 1.0),
            ("alpha55", -1.0, 1.0),
            # 指示变量 / pow2 的值域
            ("alpha68", -1.0, 0.0),
            ("alpha85", 0.0, 1.0),
            ("alpha95", 0.0, 1.0),
            ("alpha99", -1.0, 0.0),
            # 三分支常量 alpha
            ("alpha7", -1.0, 1.0),
            ("alpha21", -1.0, 1.0),
        ],
    )
    def test_declared_value_bounds_hold(
        self, name: str, low: float, high: float, panel: pd.DataFrame
    ) -> None:
        # Act
        valid = compute_alpha(panel, name).dropna().to_numpy(dtype=float)

        # Assert
        assert valid.size > 0
        assert valid.min() >= low - 1e-9, f"{name} 下界越界: {valid.min()}"
        assert valid.max() <= high + 1e-9, f"{name} 上界越界: {valid.max()}"


class TestHandCheckedAlphas:
    """少数可以直接手算的 alpha —— 用它们钉死算子接线是否正确。"""

    def test_alpha101_equals_body_over_range(self, panel: pd.DataFrame) -> None:
        # Arrange：alpha101 = (close - open) / ((high - low) + 0.001)，纯逐元素运算
        expected = (panel["close"] - panel["open"]) / (
            (panel["high"] - panel["low"]) + 0.001
        )

        # Act
        actual = compute_alpha(panel, "alpha101")

        # Assert
        np.testing.assert_allclose(
            actual.to_numpy(dtype=float), expected.to_numpy(dtype=float), rtol=1e-12
        )

    def test_alpha12_equals_volume_sign_times_negated_price_change(
        self, panel: pd.DataFrame
    ) -> None:
        # Arrange：alpha12 = sign(Δvolume) * (-Δclose)，逐标的一阶差分
        by_symbol = panel.groupby(level="instrument")
        expected = np.sign(by_symbol["volume"].diff()) * (-by_symbol["close"].diff())

        # Act
        actual = compute_alpha(panel, "alpha12")

        # Assert
        np.testing.assert_allclose(
            actual.to_numpy(dtype=float),
            expected.reindex(panel.index).to_numpy(dtype=float),
            rtol=1e-12,
            equal_nan=True,
        )

    def test_returns_field_is_simple_close_to_close_return(
        self, panel: pd.DataFrame
    ) -> None:
        # Arrange / Act
        fields = build_fields(panel)

        # Assert：returns = close / delay(close, 1) - 1
        closes = panel.xs(SYMBOLS[0], level="instrument")["close"]
        expected = (closes / closes.shift(1) - 1.0).to_numpy(dtype=float)
        np.testing.assert_allclose(
            fields.returns[SYMBOLS[0]].to_numpy(dtype=float), expected, equal_nan=True
        )

    def test_build_fields_rejects_incomplete_panel(self, panel: pd.DataFrame) -> None:
        with pytest.raises(ValueError, match="volume"):
            build_fields(panel.drop(columns=["volume"]))

    def test_compute_alpha_rejects_unknown_name(self, panel: pd.DataFrame) -> None:
        with pytest.raises(ValueError, match="未知 Alpha101 因子"):
            compute_alpha(panel, "alpha_not_real")


# ── 因子库注册 ────────────────────────────────────────────────────


class TestFactorLibraryRegistration:
    def test_alpha101_specs_present_in_default_library(self) -> None:
        # Act
        specs = generate_factor_library()

        # Assert
        alpha_specs = [s for s in specs if s.group == ALPHA101_GROUP]
        assert len(alpha_specs) == len(ALPHA_FUNCTIONS)
        assert all(s.is_panel for s in alpha_specs)
        assert {s.name for s in alpha_specs} == set(ALPHA_FUNCTIONS)

    def test_alpha101_selectable_by_group(self) -> None:
        specs = generate_factor_library(groups=(ALPHA101_GROUP,))
        assert {s.name for s in specs} == set(ALPHA_FUNCTIONS)

    def test_spec_meta_exposes_panel_flag_and_source_expression(self) -> None:
        spec = next(
            s for s in generate_factor_library(groups=(ALPHA101_GROUP,)) if s.name == "alpha101"
        )
        meta = spec.to_meta()
        assert meta["is_panel"] is True
        assert meta["expr"] == ALPHA_EXPRESSIONS["alpha101"][0]

    def test_split_by_mode_separates_the_two_evaluation_paths(self) -> None:
        single, panel_specs = split_by_mode(generate_factor_library())
        assert all(not s.is_panel for s in single)
        assert all(s.is_panel for s in panel_specs)
        assert len(panel_specs) == len(ALPHA_FUNCTIONS)

    def test_build_feature_fn_rejects_panel_specs_instead_of_skipping(self) -> None:
        # 静默跳过会让 IC 排行无声缺项，必须显式报错
        specs = generate_factor_library(groups=(ALPHA101_GROUP,))
        with pytest.raises(ValueError, match="面板型"):
            build_feature_fn(specs)


class TestAttachPanelFactors:
    def test_adds_one_column_per_spec_without_mutating_input(self) -> None:
        # Arrange
        bars = _universe_bars(n=90)
        raw = bars_to_panel(bars)
        original_columns = list(raw.columns)
        specs = [
            s
            for s in generate_factor_library(groups=(ALPHA101_GROUP,))
            if s.name in ("alpha101", "alpha12", "alpha33")
        ]

        # Act
        enriched = attach_panel_factors(raw, specs)

        # Assert
        assert list(raw.columns) == original_columns, "入参面板被就地修改了"
        for spec in specs:
            assert spec.name in enriched.columns
            assert enriched[spec.name].notna().any()

    def test_failing_spec_becomes_all_nan_column_not_a_crash(self) -> None:
        # Arrange：一个必然抛错的面板因子
        from app.quant.factor_lib.loader import FactorSpec

        raw = bars_to_panel(_universe_bars(n=70))
        broken = FactorSpec(
            name="broken",
            label="broken",
            group=ALPHA101_GROUP,
            window=5,
            expr="boom",
            compute_panel=lambda _panel: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        # Act
        enriched = attach_panel_factors(raw, [broken])

        # Assert：整批不被单个因子拖垮，但该列全 NaN（coverage=0，排行自然沉底）
        assert "broken" in enriched.columns
        assert enriched["broken"].isna().all()

    def test_rejects_single_mode_spec(self) -> None:
        raw = bars_to_panel(_universe_bars(n=70))
        kmid = next(s for s in generate_factor_library(groups=("K线",)) if s.name == "KMID")
        with pytest.raises(ValueError, match="非面板型"):
            attach_panel_factors(raw, [kmid])


class TestCrossSectionICRanking:
    def test_alpha101_factors_flow_through_the_ic_ranking_path(self) -> None:
        # Arrange：这正是 /quant/factor/library/analyze 的内部链路
        bars = _universe_bars(n=140)
        specs = [
            s
            for s in generate_factor_library(groups=(ALPHA101_GROUP,))
            if s.name in ("alpha101", "alpha12", "alpha33", "alpha4")
        ]
        raw = bars_to_panel(bars)
        enriched = attach_panel_factors(raw, specs)
        labeled = attach_forward_label(enriched, 5, label_field="forward_return")

        # Act
        ranking = rank_factor_library(
            labeled_panel=labeled,
            specs=specs,
            label_field="forward_return",
            method="rank_ic",
            min_names=3,
        )

        # Assert
        assert len(ranking) == len(specs)
        assert all(stat.n_dates > 0 for stat in ranking)
        assert all(0.0 < stat.coverage <= 1.0 for stat in ranking)


# ── V3 A-a 回测入口端到端 ────────────────────────────────────────


class TestPanelFactorBacktest:
    def test_panel_alpha_runs_a_full_portfolio_backtest(self) -> None:
        # Arrange：截面 alpha 必须能一路走到成交，这是 A-a 断链的验收点
        spec = next(
            s
            for s in generate_factor_library(groups=(ALPHA101_GROUP,))
            if s.name == "alpha101"
        )
        strategy = FrameworkStrategy(
            alpha=PanelLibraryFactorAlphaModel(spec, min_history=30, long_quantile=0.5),
            portfolio_construction=EqualWeightingPCM(),
        )
        config = PortfolioBacktestConfig(
            initial_cash=1_000_000.0,
            market=Market.US,
            slippage_model=NoSlippage(),
            commission_model=_ZeroCommission(),
        )

        # Act
        result = PortfolioBacktestEngine(config).run(strategy, _universe_bars())

        # Assert
        assert result.fills, "面板型因子应当能产生真实成交"
        assert math.isfinite(result.final_value)

    def test_factor_strategy_spec_routes_panel_entries_automatically(self) -> None:
        # Arrange：A-a 的纯数据 spec → 工厂 → 策略
        spec = FactorStrategySpec(
            library_factor="alpha101",
            universe=SYMBOLS,
            long_quantile=0.5,
            rebalance_days=5,
        )

        # Act
        strategy = build_factor_strategy(spec)

        # Assert：自动选中面板型模型，而不是拿单标的模型去调 compute=None
        assert isinstance(strategy.alpha.factor, PanelLibraryFactorAlphaModel)

    def test_panel_model_rejects_single_mode_spec(self) -> None:
        kmid = next(s for s in generate_factor_library(groups=("K线",)) if s.name == "KMID")
        with pytest.raises(ValueError, match="单标的型"):
            PanelLibraryFactorAlphaModel(kmid)

    def test_single_model_rejects_panel_spec(self) -> None:
        alpha = next(
            s
            for s in generate_factor_library(groups=(ALPHA101_GROUP,))
            if s.name == "alpha101"
        )
        with pytest.raises(ValueError, match="面板型"):
            LibraryFactorAlphaModel(alpha)
