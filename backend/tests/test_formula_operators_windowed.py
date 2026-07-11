"""公式化因子引擎（formula_factor.py）带窗口算子单元测试

覆盖 B3 带窗口算子（SLOPE10/CORR20 等）：
- 已注册到 OPS / OP_META，arity 正确
- 在 evaluate_formula RPN 中可用、栈平衡（求值返回单一序列）
- 操作数不足时抛 FormulaError
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.formula_factor import (
    OP_META,
    OPS,
    FormulaError,
    evaluate_formula,
)

# 期望存在的带窗口算子（_OP_WINDOWS = (10, 20)）
WINDOWED_UNARY = [
    "SLOPE10", "RSQR10", "RESI10", "WMA10", "EMA10",
    "MAD10", "QTLU10", "QTLD10", "IMAX10", "IMIN10",
    "SLOPE20", "RSQR20", "RESI20", "WMA20", "EMA20",
    "MAD20", "QTLU20", "QTLD20", "IMAX20", "IMIN20",
]
WINDOWED_BINARY = ["CORR10", "CORR20", "COV10", "COV20"]


def _make_ohlcv(n_days: int = 80, seed: int = 42) -> pd.DataFrame:
    """生成 OHLCV 帧，供特征叶子节点求值。"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, n_days)
    close = 100.0 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_days)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_days)))
    open_ = close * (1 + rng.normal(0, 0.003, n_days))
    volume = rng.integers(1_000_000, 5_000_000, n_days).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def _op_by_name() -> dict[str, object]:
    return {op.name: op for op in OPS}


class TestWindowedOpRegistration:
    @pytest.mark.parametrize("name", WINDOWED_UNARY)
    def test_unary_windowed_op_registered_with_arity_one(self, name: str) -> None:
        # Arrange
        ops = _op_by_name()

        # Act / Assert
        assert name in ops
        assert ops[name].arity == 1

    @pytest.mark.parametrize("name", WINDOWED_BINARY)
    def test_binary_windowed_op_registered_with_arity_two(self, name: str) -> None:
        # Arrange
        ops = _op_by_name()

        # Act / Assert
        assert name in ops
        assert ops[name].arity == 2

    def test_op_meta_exposes_windowed_ops(self) -> None:
        # Arrange
        meta_names = {m["name"] for m in OP_META}

        # Act / Assert：前端元数据含带窗口算子
        assert {"SLOPE10", "CORR20", "COV10"} <= meta_names

    def test_op_meta_arity_matches_ops(self) -> None:
        # Arrange
        by_meta = {m["name"]: m["arity"] for m in OP_META}

        # Act / Assert
        assert by_meta["SLOPE10"] == 1
        assert by_meta["CORR20"] == 2


class TestWindowedOpEvaluation:
    def test_unary_windowed_op_balances_stack(self) -> None:
        # Arrange：MOM20 特征后接 SLOPE10 一元算子
        df = _make_ohlcv()

        # Act
        result = evaluate_formula(df, ["MOM20", "SLOPE10"])

        # Assert：求值返回单一等长序列（栈平衡）
        assert isinstance(result, pd.Series)
        assert len(result) == len(df)
        assert result.dropna().notna().all()

    def test_binary_windowed_op_consumes_two_operands(self) -> None:
        # Arrange：两个特征喂给 CORR20 二元算子
        df = _make_ohlcv()

        # Act
        result = evaluate_formula(df, ["RET1", "LOG_VOL", "CORR20"])

        # Assert
        assert isinstance(result, pd.Series)
        assert len(result) == len(df)
        # 相关系数落在 [-1, 1]
        vals = result.dropna().to_numpy()
        assert vals.size > 0
        assert np.all(vals >= -1.0 - 1e-9) and np.all(vals <= 1.0 + 1e-9)

    def test_cov_windowed_op_evaluates(self) -> None:
        # Arrange
        df = _make_ohlcv()

        # Act
        result = evaluate_formula(df, ["RET1", "LOG_VOL", "COV10"])

        # Assert
        assert isinstance(result, pd.Series)
        assert result.dropna().size > 0

    def test_composed_windowed_formula(self) -> None:
        # Arrange：SLOPE10(MOM20) 与 EMA10(RET1) 相除
        df = _make_ohlcv()

        # Act
        result = evaluate_formula(
            df, ["MOM20", "SLOPE10", "RET1", "EMA10", "DIV"]
        )

        # Assert
        assert isinstance(result, pd.Series)
        assert len(result) == len(df)


class TestWindowedOpErrors:
    def test_unary_windowed_op_without_operand_raises(self) -> None:
        # Arrange：SLOPE10 需 1 操作数，栈为空
        df = _make_ohlcv()

        # Act / Assert
        with pytest.raises(FormulaError, match="SLOPE10"):
            evaluate_formula(df, ["SLOPE10"])

    def test_binary_windowed_op_with_single_operand_raises(self) -> None:
        # Arrange：CORR20 需 2 操作数，栈只有 1
        df = _make_ohlcv()

        # Act / Assert
        with pytest.raises(FormulaError, match="CORR20"):
            evaluate_formula(df, ["MOM20", "CORR20"])

    def test_unbalanced_formula_raises(self) -> None:
        # Arrange：两个特征无消费算子 → 栈剩 2
        df = _make_ohlcv()

        # Act / Assert
        with pytest.raises(FormulaError, match="不平衡"):
            evaluate_formula(df, ["MOM20", "RET1", "SLOPE10"])
