"""成本感知因子适应度单元测试

覆盖：
- 活跃度门控：交易过少 → 适应度触底
- 空 universe / 全 NaN 因子 → 触底且门控未通过
- 正常打分：显著因子 → 门控通过、可解释拆解合理
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_fitness import (
    FitnessConfig,
    FitnessResult,
    compute_factor_fitness,
)


# ── 公用构造器 ─────────────────────────────────────────────────

def _panel(
    dates: list[str],
    instruments: list[str],
    matrix: np.ndarray,
    col: str = "v",
) -> pd.DataFrame:
    """由 time × instrument 矩阵构造 (datetime, instrument) 单列面板。"""
    idx = pd.MultiIndex.from_product(
        [dates, instruments], names=["datetime", "instrument"]
    )
    flat = matrix.reshape(-1)
    return pd.DataFrame({col: flat}, index=idx).sort_index()


def _dates(n: int) -> list[str]:
    return [f"2020-01-{d:02d}" for d in range(1, n + 1)]


# ── 活跃度门控 ─────────────────────────────────────────────────

class TestActivityGate:
    def test_too_few_trades_floors_fitness(self) -> None:
        # Arrange: 因子几乎全为强负（sigmoid<门槛）→ 极少开仓
        dates = _dates(6)
        instruments = ["A", "B", "C"]
        # 全为 -10 → sigmoid≈0 < entry_threshold → 无仓位
        factor = np.full((len(dates), len(instruments)), -10.0)
        fwd = np.full((len(dates), len(instruments)), 0.01)
        cfg = FitnessConfig(min_activity=5)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, factor),
            _panel(dates, instruments, fwd),
            config=cfg,
        )

        # Assert: 活跃度不足 → 触底、门控未通过
        assert result.activity_gate_passed is False
        assert result.fitness == pytest.approx(cfg.inactivity_floor)

    def test_sufficient_activity_passes_gate(self) -> None:
        # Arrange: 因子恒为强正 → 每 bar 每标的开仓，活跃度充足
        dates = _dates(10)
        instruments = ["A", "B", "C"]
        factor = np.full((len(dates), len(instruments)), 10.0)
        fwd = np.full((len(dates), len(instruments)), 0.005)
        cfg = FitnessConfig(min_activity=5)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, factor),
            _panel(dates, instruments, fwd),
            config=cfg,
        )

        # Assert
        assert result.activity_gate_passed is True
        assert result.avg_activity >= cfg.min_activity


# ── 空 / 退化输入 ──────────────────────────────────────────────

class TestDegenerateInput:
    def test_empty_universe_floors(self) -> None:
        # Arrange: 空面板
        empty = pd.DataFrame(
            {"v": []},
            index=pd.MultiIndex.from_tuples(
                [], names=["datetime", "instrument"]
            ),
        )

        # Act
        result = compute_factor_fitness(empty, empty)

        # Assert
        assert isinstance(result, FitnessResult)
        assert result.activity_gate_passed is False
        assert result.fitness == pytest.approx(FitnessConfig().inactivity_floor)
        assert result.per_instrument_score == {}

    def test_all_nan_factor_floors(self) -> None:
        # Arrange
        dates = _dates(5)
        instruments = ["A", "B"]
        factor = np.full((len(dates), len(instruments)), np.nan)
        fwd = np.full((len(dates), len(instruments)), 0.01)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, factor),
            _panel(dates, instruments, fwd),
        )

        # Assert
        assert result.activity_gate_passed is False
        assert result.fitness == pytest.approx(FitnessConfig().inactivity_floor)
        # 逐标的分数全部触底
        assert set(result.per_instrument_score.values()) == {
            FitnessConfig().inactivity_floor
        }


# ── 正常打分 ───────────────────────────────────────────────────

class TestNormalScoring:
    def test_profitable_factor_scores_above_floor(self) -> None:
        # Arrange: 强正因子 + 正前瞻收益 → 净盈利
        dates = _dates(12)
        instruments = ["A", "B", "C"]
        factor = np.full((len(dates), len(instruments)), 8.0)
        fwd = np.full((len(dates), len(instruments)), 0.01)
        cfg = FitnessConfig(fee_rate=0.0005, min_activity=3)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, factor),
            _panel(dates, instruments, fwd),
            config=cfg,
        )

        # Assert
        assert result.activity_gate_passed is True
        assert result.fitness > cfg.inactivity_floor
        assert result.gross_return > 0
        assert result.total_cost >= 0
        assert result.turnover >= 0
        assert set(result.per_instrument_score.keys()) == set(instruments)

    def test_deterministic_same_input_same_output(self) -> None:
        # Arrange
        dates = _dates(8)
        instruments = ["A", "B"]
        rng = np.random.default_rng(0)
        factor = rng.normal(0.0, 3.0, (len(dates), len(instruments)))
        fwd = rng.normal(0.001, 0.02, (len(dates), len(instruments)))
        fp = _panel(dates, instruments, factor)
        rp = _panel(dates, instruments, fwd)

        # Act
        r1 = compute_factor_fitness(fp, rp)
        r2 = compute_factor_fitness(fp, rp)

        # Assert: 确定性
        assert r1 == r2

    def test_gross_return_exceeds_net_when_trading(self) -> None:
        # Arrange: 有换手 → 成本使净收益 ≤ 毛收益
        dates = _dates(10)
        instruments = ["A", "B", "C"]
        # 交替开/平仓制造换手
        raw = np.array([[8.0, 8.0, 8.0] if d % 2 == 0 else [-8.0, -8.0, -8.0]
                        for d in range(len(dates))])
        fwd = np.full((len(dates), len(instruments)), 0.01)
        cfg = FitnessConfig(fee_rate=0.002, min_activity=1)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, raw),
            _panel(dates, instruments, fwd),
            config=cfg,
        )

        # Assert: 有换手即有成本
        assert result.turnover > 0
        assert result.total_cost > 0

    def test_liquidity_floor_blocks_illiquid_cells(self) -> None:
        # Arrange: 提供流动性面板，一个标的极低流动性应被限仓
        dates = _dates(10)
        instruments = ["A", "B", "C"]
        factor = np.full((len(dates), len(instruments)), 8.0)
        fwd = np.full((len(dates), len(instruments)), 0.01)
        liq = np.tile(np.array([1e9, 1e9, 1.0]), (len(dates), 1))
        cfg = FitnessConfig(min_activity=1)

        # Act
        result = compute_factor_fitness(
            _panel(dates, instruments, factor),
            _panel(dates, instruments, fwd),
            liquidity_panel=_panel(dates, instruments, liq),
            config=cfg,
        )

        # Assert: 返回结构完整、门控通过
        assert result.activity_gate_passed is True
        assert isinstance(result.avg_activity, float)
