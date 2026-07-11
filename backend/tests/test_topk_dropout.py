"""Topk-Dropout 组合构建单元测试（Wave-3 / D6）

覆盖：
- 最短持有期 off-by-one：hold_thresh=1 确实允许持有满 1 期即卖，
  且 hold_thresh=1 与 hold_thresh=2 产生不同卖出时点
- 卖出受候选约束：topk == 标的池规模时无换手空转（首期建仓后不再买卖）
- 最大回撤含首期亏损：起始基准 1.0，首期即亏也计入 max_drawdown
- 换手率计算：等权轮动的双边换手口径
- 输入校验：期数不足 / 索引不一致 / 非法超参 抛错
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.topk_dropout import (
    TopkConfig,
    TopkDropoutResult,
    run_topk_dropout,
)


# ── 公用构造器 ─────────────────────────────────────────────────

def _dates(n: int) -> list[str]:
    return pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()


def _random_panels(
    n_dates: int, symbols: list[str], seed: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """生成对齐的分数面板与价格面板。"""
    rng = np.random.default_rng(seed)
    dates = _dates(n_dates)
    scores = pd.DataFrame(
        rng.normal(size=(n_dates, len(symbols))), index=dates, columns=symbols
    )
    prices = pd.DataFrame(
        100.0 * np.cumprod(1 + rng.normal(0, 0.02, (n_dates, len(symbols))), axis=0),
        index=dates,
        columns=symbols,
    )
    return scores, prices


# ── 最短持有期 off-by-one ──────────────────────────────────────

class TestHoldThreshold:
    def test_hold_thresh_one_vs_two_differ(self) -> None:
        # Arrange: 相同数据、仅 hold_thresh 不同
        scores, prices = _random_panels(8, ["A", "B", "C", "D", "E", "F"], seed=1)

        # Act
        r1 = run_topk_dropout(scores, prices, TopkConfig(topk=3, n_drop=1, hold_thresh=1))
        r2 = run_topk_dropout(scores, prices, TopkConfig(topk=3, n_drop=1, hold_thresh=2))

        # Assert: hold_thresh=1 与 =2 的卖出时点序列不同（off-by-one 已修）
        sells1 = [p.sells for p in r1.periods]
        sells2 = [p.sells for p in r2.periods]
        assert sells1 != sells2

    def test_hold_thresh_one_allows_sell_after_single_period(self) -> None:
        # Arrange: hold_thresh=1 应允许在建仓后的下一期即可卖出
        scores, prices = _random_panels(8, ["A", "B", "C", "D", "E", "F"], seed=1)

        # Act
        r1 = run_topk_dropout(scores, prices, TopkConfig(topk=3, n_drop=1, hold_thresh=1))

        # Assert: 存在非首期的实际卖出（若 off-by-one 未修则会被强制多持一期）
        later_sells = [p.sells for p in r1.periods[1:]]
        assert any(len(s) > 0 for s in later_sells)


# ── 卖出受候选约束（无空转换手）───────────────────────────────

class TestNoWhipsaw:
    def test_topk_equals_universe_has_no_churn(self) -> None:
        # Arrange: topk 等于标的池规模 → 无新候选可买，不应来回打脸
        symbols = ["A", "B", "C", "D"]
        scores, prices = _random_panels(6, symbols, seed=2)

        # Act
        result = run_topk_dropout(
            scores, prices, TopkConfig(topk=len(symbols), n_drop=1, hold_thresh=1)
        )

        # Assert: 首期一次性建仓，之后无买入 / 卖出、换手为 0
        assert len(result.periods[0].buys) == len(symbols)
        for period in result.periods[1:]:
            assert period.buys == []
            assert period.sells == []
            assert period.turnover == pytest.approx(0.0)


# ── 最大回撤含首期亏损 ─────────────────────────────────────────

class TestMaxDrawdown:
    def test_first_period_loss_counted(self) -> None:
        # Arrange: 构造首期即亏 10% 的确定性场景（全仓持有 A、B）
        dates = _dates(3)
        scores = pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [0.5, 0.5, 0.5]}, index=dates)
        # 首期 -10%、次期 +10%，末期无后续 → 收益 0
        prices = pd.DataFrame(
            {"A": [100.0, 90.0, 99.0], "B": [100.0, 90.0, 99.0]}, index=dates
        )

        # Act
        result = run_topk_dropout(
            scores, prices, TopkConfig(topk=2, n_drop=1, hold_thresh=1, risk_degree=1.0)
        )

        # Assert: 起始基准 1.0 → 首期亏损被计入回撤（≈ -0.10）
        assert result.periods[0].period_return == pytest.approx(-0.10, abs=1e-9)
        assert result.metrics["max_drawdown"] == pytest.approx(-0.10, abs=1e-6)

    def test_all_positive_returns_have_nonpositive_drawdown(self) -> None:
        # Arrange: 单调上涨 → 回撤应为 0（基准 1.0 起从不跌破）
        dates = _dates(3)
        scores = pd.DataFrame({"A": [1.0, 1.0, 1.0]}, index=dates)
        prices = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=dates)

        # Act
        result = run_topk_dropout(
            scores, prices, TopkConfig(topk=1, n_drop=1, hold_thresh=1, risk_degree=1.0)
        )

        # Assert
        assert result.metrics["max_drawdown"] == pytest.approx(0.0, abs=1e-9)


# ── 换手率计算 ─────────────────────────────────────────────────

class TestTurnover:
    def test_initial_build_turnover(self) -> None:
        # Arrange: 首期从空仓建 2 只等权（risk_degree=1.0）→ 双边换手 = 0.5
        dates = _dates(2)
        scores = pd.DataFrame({"A": [1.0, 1.0], "B": [0.9, 0.9]}, index=dates)
        prices = pd.DataFrame({"A": [100.0, 101.0], "B": [100.0, 101.0]}, index=dates)

        # Act
        result = run_topk_dropout(
            scores, prices, TopkConfig(topk=2, n_drop=1, hold_thresh=1, risk_degree=1.0)
        )

        # Assert: sum(|Δw|)/2 = (0.5 + 0.5) / 2 = 0.5
        assert result.periods[0].turnover == pytest.approx(0.5, abs=1e-9)
        assert result.metrics["avg_turnover"] >= 0.0

    def test_rotation_produces_positive_turnover(self) -> None:
        # Arrange: 分数每期反转 → 触发轮动换手
        dates = _dates(4)
        scores = pd.DataFrame(
            {
                "A": [3.0, 0.0, 3.0, 0.0],
                "B": [2.0, 1.0, 2.0, 1.0],
                "C": [1.0, 2.0, 1.0, 2.0],
                "D": [0.0, 3.0, 0.0, 3.0],
            },
            index=dates,
        )
        rng = np.random.default_rng(3)
        prices = pd.DataFrame(
            100.0 * np.cumprod(1 + rng.normal(0, 0.01, (4, 4)), axis=0),
            index=dates,
            columns=["A", "B", "C", "D"],
        )

        # Act
        result = run_topk_dropout(
            scores, prices, TopkConfig(topk=2, n_drop=1, hold_thresh=1)
        )

        # Assert: 至少一期发生非零换手
        assert any(p.turnover > 0 for p in result.periods[1:])


# ── 输入校验与结构 ─────────────────────────────────────────────

class TestValidation:
    def test_result_structure(self) -> None:
        # Arrange
        scores, prices = _random_panels(6, ["A", "B", "C", "D"], seed=4)

        # Act
        result = run_topk_dropout(scores, prices, TopkConfig(topk=2))

        # Assert
        assert isinstance(result, TopkDropoutResult)
        assert result.n_periods == len(result.periods)
        assert len(result.equity_curve) == result.n_periods
        for key in ("total_return", "annual_return", "sharpe", "max_drawdown"):
            assert key in result.metrics

    def test_too_few_periods_raises(self) -> None:
        dates = _dates(1)
        scores = pd.DataFrame({"A": [1.0]}, index=dates)
        prices = pd.DataFrame({"A": [100.0]}, index=dates)
        with pytest.raises(ValueError):
            run_topk_dropout(scores, prices)

    def test_mismatched_index_raises(self) -> None:
        scores = pd.DataFrame({"A": [1.0, 2.0]}, index=_dates(2))
        prices = pd.DataFrame({"A": [100.0, 101.0]}, index=["2021-05-01", "2021-05-02"])
        with pytest.raises(ValueError):
            run_topk_dropout(scores, prices)

    def test_invalid_config_raises(self) -> None:
        with pytest.raises(ValueError):
            TopkConfig(topk=0)
        with pytest.raises(ValueError):
            TopkConfig(risk_degree=1.5)
        with pytest.raises(ValueError):
            TopkConfig(method_sell="middle")
