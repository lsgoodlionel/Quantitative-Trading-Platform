"""Hyperopt 参数优化单元测试（C1）

覆盖：
- ParamSpace.from_spec 三形态：list 离散 / dict 连续区间 / dict choices 分类
- grid_size 估算（各维取值数相乘，不物化）
- _grid_search 超 5 万组退化随机采样（巨大网格不物化完整笛卡尔积）
- 11 个损失函数方向正确（Sharpe/收益越高 score 越高，回撤越小 score 越高）
- score_metrics 成交过少重罚 / 非有限值重罚
- random / grid 搜索返回 trials 与最优参数
"""

from __future__ import annotations

import numpy as np
import pytest

from app.engine.backtest.hyperopt import (
    LOSS_FUNCTIONS,
    HyperoptOutcome,
    ParamDim,
    ParamSpace,
    Trial,
    _grid_search,
    _TOO_FEW_TRADES_PENALTY,
    list_loss_functions,
    run_hyperopt,
    score_metrics,
)


# ── 测试辅助 ─────────────────────────────────────────────────────

def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _metrics(**overrides) -> dict:
    """构造带默认成交数的 metrics dict。"""
    base = {"total_trades": 10}
    base.update(overrides)
    return base


class TestParamSpaceFromSpec:
    """ParamSpace.from_spec 三形态解析。"""

    def test_list_form_builds_categorical_dim(self):
        # Arrange
        spec = {"fast": [5, 10, 20]}

        # Act
        space = ParamSpace.from_spec(spec)

        # Assert
        assert len(space.dims) == 1
        dim = space.dims[0]
        assert dim.is_categorical
        assert dim.choices == [5, 10, 20]
        assert dim.grid_values() == [5, 10, 20]

    def test_dict_range_form_builds_numeric_dim(self):
        # Arrange
        spec = {"period": {"low": 5, "high": 50, "step": 5, "type": "int"}}

        # Act
        space = ParamSpace.from_spec(spec)

        # Assert
        dim = space.dims[0]
        assert not dim.is_categorical
        assert dim.is_int is True
        assert dim.low == 5.0
        assert dim.high == 50.0
        assert dim.grid_values() == [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]

    def test_dict_choices_form_builds_categorical_dim(self):
        # Arrange
        spec = {"ma_type": {"choices": ["sma", "ema"]}}

        # Act
        space = ParamSpace.from_spec(spec)

        # Assert
        dim = space.dims[0]
        assert dim.is_categorical
        assert dim.choices == ["sma", "ema"]
        assert dim.is_numeric_choice is False

    def test_multi_param_space_names_ordered(self):
        # Arrange
        spec = {"fast": [5, 10], "slow": {"low": 20, "high": 40, "type": "int"}}

        # Act
        space = ParamSpace.from_spec(spec)

        # Assert
        assert space.names == ["fast", "slow"]

    def test_empty_spec_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="参数空间为空"):
            ParamSpace.from_spec({})

    def test_empty_list_choices_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="取值列表为空"):
            ParamSpace.from_spec({"fast": []})

    def test_range_missing_bounds_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="缺少 low/high"):
            ParamSpace.from_spec({"period": {"step": 1}})

    def test_invalid_definition_type_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="定义无效"):
            ParamSpace.from_spec({"period": "not-a-list-or-dict"})


class TestGridSize:
    """grid_size 笛卡尔积规模估算。"""

    def test_grid_size_multiplies_dim_cardinalities(self):
        # Arrange: 3 * 2 * (0..10 step1 = 11) = 66
        space = ParamSpace.from_spec(
            {
                "a": [5, 10, 20],
                "b": {"choices": ["x", "y"]},
                "c": {"low": 0, "high": 10, "step": 1, "type": "int"},
            }
        )

        # Act
        size = space.grid_size()

        # Assert
        assert size == 3 * 2 * 11

    def test_grid_size_single_dim(self):
        # Arrange
        space = ParamSpace.from_spec({"a": [1, 2, 3, 4]})

        # Act / Assert
        assert space.grid_size() == 4

    def test_grid_materializes_full_cartesian_product(self):
        # Arrange
        space = ParamSpace.from_spec({"a": [1, 2], "b": [10, 20, 30]})

        # Act
        combos = space.grid()

        # Assert
        assert len(combos) == 6
        assert {"a": 1, "b": 10} in combos
        assert {"a": 2, "b": 30} in combos


class TestGridSearchDegradation:
    """_grid_search 超 5 万组退化为随机采样，不物化完整网格。"""

    def test_huge_grid_does_not_materialize(self, monkeypatch):
        # Arrange: 200 * 200 * 200 = 8,000,000 组，远超 50k 上限
        space = ParamSpace.from_spec(
            {
                "a": {"low": 1, "high": 200, "step": 1, "type": "int"},
                "b": {"low": 1, "high": 200, "step": 1, "type": "int"},
                "c": {"low": 1, "high": 200, "step": 1, "type": "int"},
            }
        )
        assert space.grid_size() > 50_000

        # 若尝试物化完整笛卡尔积会 OOM/超时 —— 监视 grid() 确保绝不被调用
        def _boom(_self) -> list:
            raise AssertionError("巨大网格不应物化完整笛卡尔积")

        monkeypatch.setattr(ParamSpace, "grid", _boom)

        eval_calls = {"n": 0}

        def _evaluate(params: dict) -> dict:
            eval_calls["n"] += 1
            return _metrics(sharpe_ratio=1.0)

        # Act
        trials, total = _grid_search(
            space, _evaluate, "sharpe", n_trials=25, min_trades=1, rng=_rng(),
        )

        # Assert: 退化随机采样恰好 n_trials 个点，total 报告真实网格规模
        assert total == space.grid_size()
        assert len(trials) == 25
        assert eval_calls["n"] == 25

    def test_small_grid_materializes_and_subsamples(self):
        # Arrange: 5 * 5 = 25 组，未超上限
        space = ParamSpace.from_spec({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]})

        def _evaluate(params: dict) -> dict:
            return _metrics(sharpe_ratio=params["a"] + params["b"])

        # Act: n_trials < 网格规模 → 随机子采样
        trials, total = _grid_search(
            space, _evaluate, "sharpe", n_trials=10, min_trades=1, rng=_rng(),
        )

        # Assert
        assert total == 25
        assert len(trials) == 10


class TestLossFunctions:
    """11 个损失函数方向正确性。"""

    def test_all_eleven_loss_functions_registered(self):
        # Arrange / Act / Assert
        assert len(LOSS_FUNCTIONS) == 11
        assert len(list_loss_functions()) == 11

    def test_higher_sharpe_gives_higher_score(self):
        # Arrange
        low = _metrics(sharpe_ratio=0.5)
        high = _metrics(sharpe_ratio=2.5)

        # Act / Assert
        assert score_metrics(high, "sharpe", 1) > score_metrics(low, "sharpe", 1)

    @pytest.mark.parametrize(
        "loss_name, metric_key",
        [
            ("sharpe", "sharpe_ratio"),
            ("sortino", "sortino_ratio"),
            ("calmar", "calmar_ratio"),
            ("omega", "omega_ratio"),
            ("sqn", "sqn"),
            ("profit", "total_return_pct"),
            ("annual_return", "annual_return_pct"),
            ("profit_factor", "profit_factor"),
        ],
    )
    def test_positive_metrics_monotonic_increasing(self, loss_name, metric_key):
        # Arrange
        low = _metrics(**{metric_key: 1.0})
        high = _metrics(**{metric_key: 5.0})

        # Act / Assert: 指标越大 score 越大
        assert score_metrics(high, loss_name, 1) > score_metrics(low, loss_name, 1)

    def test_smaller_drawdown_gives_higher_score(self):
        # Arrange: 回撤为负，绝对值越小越好
        shallow = _metrics(max_drawdown_pct=-5.0)
        deep = _metrics(max_drawdown_pct=-30.0)

        # Act / Assert
        assert score_metrics(shallow, "max_drawdown", 1) > score_metrics(deep, "max_drawdown", 1)

    def test_max_drawdown_score_is_negative_abs(self):
        # Arrange
        m = _metrics(max_drawdown_pct=-12.0)

        # Act / Assert
        assert score_metrics(m, "max_drawdown", 1) == pytest.approx(-12.0)

    def test_profit_drawdown_ratio_direction(self):
        # Arrange: 相同收益，回撤越小比值越大
        good = _metrics(total_return_pct=20.0, max_drawdown_pct=-5.0)
        bad = _metrics(total_return_pct=20.0, max_drawdown_pct=-20.0)

        # Act / Assert
        assert score_metrics(good, "profit_drawdown", 1) > score_metrics(bad, "profit_drawdown", 1)

    def test_profit_drawdown_zero_drawdown_returns_raw_return(self):
        # Arrange: 无回撤时回退为总收益
        m = _metrics(total_return_pct=15.0, max_drawdown_pct=0.0)

        # Act / Assert
        assert score_metrics(m, "profit_drawdown", 1) == pytest.approx(15.0)

    def test_multi_metric_penalizes_drawdown(self):
        # Arrange: sharpe + annual/100 - 2*|dd|/100
        m = _metrics(sharpe_ratio=1.0, annual_return_pct=20.0, max_drawdown_pct=-10.0)

        # Act
        score = score_metrics(m, "multi_metric", 1)

        # Assert: 1.0 + 0.2 - 2*0.1 = 1.0
        assert score == pytest.approx(1.0)

    def test_multi_metric_higher_drawdown_lowers_score(self):
        # Arrange
        low_dd = _metrics(sharpe_ratio=1.0, annual_return_pct=10.0, max_drawdown_pct=-5.0)
        high_dd = _metrics(sharpe_ratio=1.0, annual_return_pct=10.0, max_drawdown_pct=-25.0)

        # Act / Assert
        assert score_metrics(low_dd, "multi_metric", 1) > score_metrics(high_dd, "multi_metric", 1)


class TestScoreMetrics:
    """score_metrics 边界与惩罚逻辑。"""

    def test_too_few_trades_returns_penalty(self):
        # Arrange
        m = _metrics(total_trades=2, sharpe_ratio=3.0)

        # Act / Assert
        assert score_metrics(m, "sharpe", min_trades=5) == _TOO_FEW_TRADES_PENALTY

    def test_non_finite_score_returns_penalty(self):
        # Arrange
        m = _metrics(sharpe_ratio=float("inf"))

        # Act / Assert
        assert score_metrics(m, "sharpe", min_trades=1) == _TOO_FEW_TRADES_PENALTY

    def test_unknown_loss_name_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="未知损失函数"):
            score_metrics(_metrics(), "does_not_exist", 1)

    def test_missing_metric_defaults_to_zero(self):
        # Arrange: metrics 缺少 sharpe_ratio 键
        m = {"total_trades": 5}

        # Act / Assert
        assert score_metrics(m, "sharpe", 1) == 0.0


class TestRunHyperopt:
    """run_hyperopt 主入口 —— random / grid 返回 trials。"""

    @staticmethod
    def _quadratic_evaluate(params: dict) -> dict:
        # 最优在 fast=10：越接近 sharpe 越高
        sharpe = 3.0 - abs(params["fast"] - 10) * 0.1
        return _metrics(sharpe_ratio=sharpe)

    def test_random_search_returns_trials(self):
        # Arrange
        space = ParamSpace.from_spec(
            {"fast": {"low": 2, "high": 30, "step": 1, "type": "int"}}
        )

        # Act
        outcome = run_hyperopt(
            self._quadratic_evaluate, space, loss_name="sharpe",
            algorithm="random", n_trials=15, seed=7,
        )

        # Assert
        assert isinstance(outcome, HyperoptOutcome)
        assert outcome.algorithm == "random"
        assert len(outcome.trials) > 0
        assert all(isinstance(t, Trial) for t in outcome.trials)
        # trials 按 score 降序，best 即首个
        scores = [t.score for t in outcome.trials]
        assert scores == sorted(scores, reverse=True)
        assert outcome.best_score == pytest.approx(outcome.trials[0].score)

    def test_grid_search_returns_trials_and_finds_optimum(self):
        # Arrange
        space = ParamSpace.from_spec({"fast": [8, 9, 10, 11, 12]})

        # Act
        outcome = run_hyperopt(
            self._quadratic_evaluate, space, loss_name="sharpe",
            algorithm="grid", n_trials=50, seed=1,
        )

        # Assert: 网格完整评估，最优参数应为 fast=10
        assert outcome.algorithm == "grid"
        assert outcome.total_space == 5
        assert outcome.best_params["fast"] == 10
        assert outcome.evaluated == 5

    def test_unknown_algorithm_raises(self):
        # Arrange
        space = ParamSpace.from_spec({"fast": [5, 10]})

        # Act / Assert
        with pytest.raises(ValueError, match="未知算法"):
            run_hyperopt(self._quadratic_evaluate, space, algorithm="genetic")

    def test_unknown_loss_name_raises(self):
        # Arrange
        space = ParamSpace.from_spec({"fast": [5, 10]})

        # Act / Assert
        with pytest.raises(ValueError, match="未知损失函数"):
            run_hyperopt(self._quadratic_evaluate, space, loss_name="bogus")

    def test_all_evaluations_fail_raises(self):
        # Arrange
        space = ParamSpace.from_spec({"fast": [5, 10]})

        def _always_fail(params: dict) -> dict:
            raise RuntimeError("boom")

        # Act / Assert
        with pytest.raises(ValueError, match="评估均失败"):
            run_hyperopt(_always_fail, space, algorithm="grid", n_trials=10)
