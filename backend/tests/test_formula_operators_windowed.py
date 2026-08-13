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
        assert np.all(vals >= -1.0 - 1e-9)
        assert np.all(vals <= 1.0 + 1e-9)

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


# ── RANK / ZSCORE：非窗口标注但同样带滚动窗口的两个算子 ────────────
#
# 回归用例：`RANK` 曾经对**任何**输入都抛 FormulaError —— `raw=False` 让 lambda
# 收到 Series，而 `series[-1]` 在 pandas 3 下是标签查找而非位置索引。
#
# 它能死这么久，是因为整个测试套件里没有一条用例碰过它；而失败又是安静的：
# 遗传挖掘捕获 FormulaError 后给候选打底分、不留日志，含 RANK 的个体
# 全被静默淘汰，还有一个用到它的 preset 一直是坏的。

class TestRollingRank:
    @staticmethod
    def _frame(closes: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame({
            "open": closes, "high": closes + 1.0, "low": closes - 1.0,
            "close": closes, "volume": np.full(len(closes), 1e6),
        })

    def test_rank_evaluates_without_error(self) -> None:
        # Arrange / Act：这条断言本身就是回归 —— 它曾经必抛 FormulaError
        values = evaluate_formula(self._frame(np.arange(100.0) + 100.0), ["MOM20", "RANK"])

        # Assert
        assert len(values) == 100
        assert values.notna().any()

    def test_rank_is_one_when_latest_is_the_window_maximum(self) -> None:
        from app.quant.formula_factor import _op_rank

        # Arrange：单调上行 → 末值是窗口内最大
        ranked = _op_rank(pd.Series(np.arange(100.0)))

        # Assert
        assert float(ranked.iloc[-1]) == pytest.approx(1.0)

    def test_rank_is_lowest_bucket_when_latest_is_the_window_minimum(self) -> None:
        from app.quant.formula_factor import _op_rank

        # Arrange：单调下行 → 末值是窗口内最小，分位 = 1/60
        ranked = _op_rank(pd.Series(np.arange(100.0)[::-1].copy()))

        # Assert
        assert float(ranked.iloc[-1]) == pytest.approx(1.0 / 60.0)

    def test_rank_stays_within_unit_interval(self) -> None:
        from app.quant.formula_factor import _op_rank

        rng = np.random.default_rng(7)
        ranked = _op_rank(pd.Series(rng.normal(size=200))).dropna()

        assert len(ranked) > 0
        assert ranked.between(0.0, 1.0).all()

    def test_zscore_evaluates_without_error(self) -> None:
        values = evaluate_formula(self._frame(np.arange(100.0) + 100.0), ["MOM20", "ZSCORE"])

        assert len(values) == 100
        assert values.notna().any()
