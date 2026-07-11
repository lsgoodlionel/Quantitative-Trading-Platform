"""遗传/进化因子挖掘引擎单元测试（Wave-3 / B5）

覆盖：
- 表达式树基因型：随机生成 / 交叉 / 变异后始终产出栈平衡的合法 RPN、尺寸受约束
- evolve 主循环确定性：相同 seed → 完全相同结果（服务于实验可复现）
- 进化不崩：常数序列 / 退化因子退回触底适应度而非抛异常
- 边界：空 universe 抛错；elite_count >= population_size 不崩溃（优雅完成）
"""

from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_fitness import FitnessConfig
from app.quant.formula_factor import FEATURE_META, OPS, evaluate_formula
from app.quant.mining import expression_tree as et
from app.quant.mining.genetic import GAConfig, GAResult, evolve

# 词表：算子元数与叶子集合，用于栈平衡校验
_ARITY: dict[str, int] = {op.name: op.arity for op in OPS}
_LEAVES: frozenset[str] = frozenset(m["name"] for m in FEATURE_META)


# ── 公用构造器 ─────────────────────────────────────────────────

def _make_ohlcv(seed: int, n: int = 80, constant: bool = False) -> pd.DataFrame:
    """生成单标的合成 OHLCV（index 为 ISO 时间字符串，对齐引擎约定）。"""
    rng = np.random.default_rng(seed)
    idx = (
        pd.date_range("2020-01-01", periods=n, freq="D")
        .strftime("%Y-%m-%dT00:00:00")
        .tolist()
    )
    close = np.full(n, 100.0) if constant else 100.0 * np.cumprod(
        1 + rng.normal(0, 0.02, n)
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


def _make_universe(
    symbols: list[str], seed_base: int = 0, constant: bool = False
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """构建 {symbol: OHLCV} 与 (datetime, instrument) 前瞻收益单列面板。"""
    ohlcv: dict[str, pd.DataFrame] = {}
    idx: list[str] = []
    for i, sym in enumerate(symbols):
        frame = _make_ohlcv(seed_base + i, constant=constant)
        ohlcv[sym] = frame
        idx = frame.index.tolist()

    records: list[tuple[str, str, float]] = []
    for sym in symbols:
        fwd = ohlcv[sym]["close"].pct_change().shift(-1)
        for ts in idx:
            value = 0.0 if constant else float(fwd.loc[ts])
            records.append((ts, sym, value))
    panel = (
        pd.DataFrame(records, columns=["datetime", "instrument", "fwd"])
        .set_index(["datetime", "instrument"])
        .sort_index()
    )
    return ohlcv, panel


def _is_stack_balanced(tokens: list[str]) -> bool:
    """模拟栈式虚拟机执行，验证 RPN 恰好栈平衡（结束时栈深为 1、过程无下溢）。"""
    depth = 0
    for tok in tokens:
        if tok in _LEAVES:
            depth += 1
        elif tok in _ARITY:
            arity = _ARITY[tok]
            if depth < arity:
                return False
            depth -= arity - 1
        else:
            return False
    return depth == 1


# ── 表达式树基因型 ─────────────────────────────────────────────

class TestExpressionTree:
    def test_random_tree_is_stack_balanced(self) -> None:
        # Arrange
        rng = random.Random(11)

        # Act / Assert: 大量随机树全部栈平衡且不超 token 上限
        for _ in range(200):
            tree = et.random_tree(rng, max_depth=4)
            tokens = et.to_rpn(tree)
            assert _is_stack_balanced(tokens)
            assert len(tokens) <= 32

    def test_crossover_preserves_legality(self) -> None:
        # Arrange
        rng = random.Random(7)

        # Act / Assert: 交叉产物始终是合法（栈平衡）公式
        for _ in range(200):
            parent_a = et.random_tree(rng, 4)
            parent_b = et.random_tree(rng, 4)
            child = et.crossover(rng, parent_a, parent_b)
            assert _is_stack_balanced(et.to_rpn(child))

    def test_mutation_preserves_legality_and_bound(self) -> None:
        # Arrange
        rng = random.Random(23)

        # Act / Assert: 变异产物合法且节点数不超硬上限
        for _ in range(200):
            base = et.random_tree(rng, 4)
            mutated = et.mutate(rng, base, 4)
            assert _is_stack_balanced(et.to_rpn(mutated))
            assert et.tree_size(mutated) <= 24

    def test_to_rpn_matches_tree_size(self) -> None:
        # Arrange
        rng = random.Random(3)
        tree = et.random_tree(rng, 4)

        # Act
        tokens = et.to_rpn(tree)

        # Assert: RPN token 数 == 节点数（每个节点恰好发射一个 token）
        assert len(tokens) == et.tree_size(tree)

    def test_clamp_size_returns_bounded_tree(self) -> None:
        # Arrange
        rng = random.Random(5)

        # Act / Assert: clamp 后始终在 token/节点上限内
        for _ in range(100):
            tree = et.random_tree(rng, 5)
            clamped = et.clamp_size(rng, tree, 5)
            assert et.tree_size(clamped) <= 24
            assert len(et.to_rpn(clamped)) <= 30

    def test_leaf_tree_evaluates_on_single_symbol(self) -> None:
        # Arrange: 纯叶子公式在单标的 OHLCV 上可求值（非 RANK 类算子）
        df = _make_ohlcv(seed=1)
        leaf = et.Node("leaf", "MOM20")

        # Act
        series = evaluate_formula(df, et.to_rpn(leaf))

        # Assert
        assert isinstance(series, pd.Series)
        assert len(series) == len(df)


# ── evolve 确定性 ──────────────────────────────────────────────

class TestEvolveDeterminism:
    def test_same_seed_yields_identical_result(self) -> None:
        # Arrange
        ohlcv, fwd = _make_universe(["A", "B", "C", "D"], seed_base=0)
        cfg = GAConfig(population_size=8, generations=3, seed=7)

        # Act
        first = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)
        second = evolve(ohlcv, fwd, None, FitnessConfig(), GAConfig(
            population_size=8, generations=3, seed=7,
        ))

        # Assert: 最优个体与逐代历史完全一致
        assert first.best is not None and second.best is not None
        assert first.best.tokens == second.best.tokens
        assert first.best.fitness == pytest.approx(second.best.fitness, nan_ok=True)
        assert [h.best_expr for h in first.history] == [h.best_expr for h in second.history]

    def test_different_seed_may_diverge_but_stays_valid(self) -> None:
        # Arrange
        ohlcv, fwd = _make_universe(["A", "B", "C"], seed_base=5)

        # Act
        result = evolve(ohlcv, fwd, None, FitnessConfig(), GAConfig(
            population_size=6, generations=3, seed=99,
        ))

        # Assert: 结果结构完整，最优 token 栈平衡
        assert isinstance(result, GAResult)
        assert result.best is not None
        assert _is_stack_balanced(list(result.best.tokens))
        assert len(result.history) == 3


# ── 进化鲁棒性与边界 ───────────────────────────────────────────

class TestEvolveRobustness:
    def test_empty_universe_raises(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            evolve({}, pd.DataFrame(), None, FitnessConfig(), GAConfig())

    def test_constant_series_does_not_crash(self) -> None:
        # Arrange: 常数收盘价 → 退化因子，应回落到触底适应度而非抛异常
        ohlcv, fwd = _make_universe(["A", "B", "C"], seed_base=0, constant=True)

        # Act
        result = evolve(ohlcv, fwd, None, FitnessConfig(), GAConfig(
            population_size=6, generations=2, seed=1,
        ))

        # Assert: 每个候选适应度为触底值（退化因子被安全记账）
        assert result.best is not None
        assert result.best.fitness == pytest.approx(FitnessConfig().inactivity_floor)

    def test_elite_count_ge_population_completes(self) -> None:
        # Arrange: elite_count >= population_size 属退化超参
        ohlcv, fwd = _make_universe(["A", "B", "C"], seed_base=10)
        cfg = GAConfig(
            population_size=6, generations=4, elite_count=10, seed=2,
        )

        # Act: 不应崩溃（无 IndexError / 负数 range 异常），历史完整生成
        result = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)

        # Assert
        assert isinstance(result, GAResult)
        assert len(result.history) == cfg.generations
        assert result.best is not None

    def test_single_symbol_universe_runs(self) -> None:
        # Arrange: 仅 1 个标的（横截面 IC 无法计算，但不应崩溃）
        ohlcv, fwd = _make_universe(["A"], seed_base=3)

        # Act
        result = evolve(ohlcv, fwd, None, FitnessConfig(), GAConfig(
            population_size=5, generations=2, seed=4,
        ))

        # Assert
        assert isinstance(result, GAResult)
        assert len(result.history) == 2
