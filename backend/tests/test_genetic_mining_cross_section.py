"""遗传因子挖掘的截面（CS_*）搜索空间测试（M2）

关键不变量：
- `use_cross_section` **默认关闭**，关闭时 seed → 结果的映射与改动前完全一致
  （已记录的实验必须保持可复现）
- 打开后搜索空间确实包含 CS_* 算子，且含 CS_* 的个体走 panel 求值而非触底适应度
"""

from __future__ import annotations

import random

import numpy as np
import pandas as pd

from app.quant.factor_fitness import FitnessConfig
from app.quant.formula_factor import CS_OPS, formula_requires_panel
from app.quant.mining import expression_tree as et
from app.quant.mining.genetic import GAConfig, _Evaluator, evolve

_CS_NAMES = frozenset(op.name for op in CS_OPS)


def _make_ohlcv(seed: int, n: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.02, n))
    idx = (
        pd.date_range("2020-01-01", periods=n, freq="D")
        .strftime("%Y-%m-%dT00:00:00")
        .tolist()
    )
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": (np.arange(n) + 1_000_000).astype(float),
        },
        index=idx,
    )


def _make_universe(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    ohlcv = {sym: _make_ohlcv(i) for i, sym in enumerate(symbols)}
    records: list[tuple[str, str, float]] = []
    for sym, frame in ohlcv.items():
        fwd = frame["close"].pct_change().shift(-1)
        for ts in frame.index:
            records.append((ts, sym, float(fwd.loc[ts])))
    panel = (
        pd.DataFrame(records, columns=["datetime", "instrument", "fwd"])
        .set_index(["datetime", "instrument"])
        .sort_index()
    )
    return ohlcv, panel


class TestSearchSpaceDefaultsToSingleSymbol:
    def test_cs_ops_absent_from_default_vocabulary(self) -> None:
        # Arrange / Act
        unary = et.op_names_by_arity()[1]

        # Assert：默认词表里没有 CS_*，否则挖掘会搜出单标的路径必然报错的公式
        assert not set(unary) & _CS_NAMES

    def test_default_random_trees_never_contain_cs_tokens(self) -> None:
        rng = random.Random(11)
        for _ in range(200):
            tokens = et.to_rpn(et.random_tree(rng, max_depth=4))
            assert not set(tokens) & _CS_NAMES

    def test_ga_config_defaults_to_cross_section_off(self) -> None:
        assert GAConfig().use_cross_section is False

    def test_disabled_run_is_bit_identical_to_enabled_flag_absent(self) -> None:
        # Arrange：显式 False 与不传参必须完全等价（默认值没有偷偷改行为）
        ohlcv, fwd = _make_universe(["AAA", "BBB", "CCC"])
        cfg_a = GAConfig(population_size=8, generations=3, seed=5)
        cfg_b = GAConfig(population_size=8, generations=3, seed=5, use_cross_section=False)

        # Act
        first = evolve(ohlcv, fwd, None, FitnessConfig(), cfg_a)
        second = evolve(ohlcv, fwd, None, FitnessConfig(), cfg_b)

        # Assert
        assert first.best is not None
        assert first.best.tokens == second.best.tokens
        assert [h.best_expr for h in first.history] == [h.best_expr for h in second.history]


class TestSearchSpaceWithCrossSection:
    def test_cs_ops_present_when_enabled(self) -> None:
        unary = et.op_names_by_arity(include_cross_section=True)[1]
        assert _CS_NAMES.issubset(set(unary))

    def test_enabled_random_trees_eventually_use_cs_tokens(self) -> None:
        # Arrange
        rng = random.Random(3)

        # Act
        seen = set()
        for _ in range(400):
            tokens = et.to_rpn(et.random_tree(rng, 4, include_cross_section=True))
            seen |= set(tokens) & _CS_NAMES

        # Assert：搜索空间真的能触达截面算子（这正是 M2 对挖掘的价值）
        assert seen

    def test_evolve_with_cross_section_produces_a_best_candidate(self) -> None:
        # Arrange
        ohlcv, fwd = _make_universe(["AAA", "BBB", "CCC", "DDD"])
        cfg = GAConfig(
            population_size=8, generations=3, seed=17, use_cross_section=True
        )

        # Act
        result = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)

        # Assert
        assert result.best is not None
        assert result.n_evaluated > 0

    def test_evolve_with_cross_section_is_deterministic(self) -> None:
        ohlcv, fwd = _make_universe(["AAA", "BBB", "CCC"])
        cfg = GAConfig(population_size=8, generations=3, seed=23, use_cross_section=True)

        first = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)
        second = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)

        assert first.best.tokens == second.best.tokens
        assert [h.best_expr for h in first.history] == [h.best_expr for h in second.history]


class TestEvaluatorPanelRouting:
    def test_cs_formula_is_scored_through_the_panel_path(self) -> None:
        # Arrange
        ohlcv, fwd = _make_universe(["AAA", "BBB", "CCC"])
        evaluator = _Evaluator(ohlcv, fwd, None, FitnessConfig())
        tokens = ("MOM20", "CS_RANK")
        assert formula_requires_panel(list(tokens))

        # Act
        panel = evaluator._factor_panel(tokens)

        # Assert：真的算出了截面排名，而不是退化成触底候选
        assert list(panel.columns) == ["factor"]
        assert list(panel.index.names) == ["datetime", "instrument"]
        valid = panel["factor"].dropna()
        assert not valid.empty
        assert valid.min() > 0.0
        assert valid.max() <= 1.0

    def test_non_cs_formula_still_uses_the_single_symbol_path(self) -> None:
        # Arrange
        ohlcv, fwd = _make_universe(["AAA", "BBB", "CCC"])
        evaluator = _Evaluator(ohlcv, fwd, None, FitnessConfig())

        # Act
        panel = evaluator._factor_panel(("MOM20",))

        # Assert：没走 panel 路径就不会构建面板缓存
        assert evaluator._panel is None
        assert list(panel.columns) == ["factor"]
