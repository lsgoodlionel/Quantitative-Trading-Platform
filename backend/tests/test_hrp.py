"""HRP 层次风险平价（hrp.py）单元测试

覆盖：
- hrp_weights 权重和为 1、全部落在 [0, 1]、index 按 symbol 排序
- 各 linkage 连接方式聚类不崩溃
- 输入校验（非法 linkage / < 2 资产）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.hrp import VALID_LINKAGE, hrp_weights

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN"]


def _make_returns(n_days: int = 250, seed: int = 42) -> pd.DataFrame:
    """生成 250 日 4 资产日收益（含一定相关结构以形成聚类）。"""
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, 0.01, n_days)
    data = {}
    for i, sym in enumerate(SYMBOLS):
        # 前两只与后两只分别更依赖不同的公共因子，形成两个簇
        beta = 1.0 if i < 2 else 0.3
        idio = rng.normal(0.0, 0.008, n_days)
        data[sym] = beta * market + idio
    return pd.DataFrame(data)


class TestHrpWeights:
    def test_weights_sum_to_one(self) -> None:
        # Arrange
        returns = _make_returns()

        # Act
        weights = hrp_weights(returns)

        # Assert
        assert abs(weights.sum() - 1.0) < 1e-9

    def test_weights_within_unit_interval(self) -> None:
        # Arrange
        returns = _make_returns()

        # Act
        weights = hrp_weights(returns)

        # Assert：long-only，无杠杆
        assert (weights >= 0.0).all()
        assert (weights <= 1.0).all()

    def test_index_sorted_and_complete(self) -> None:
        # Arrange
        returns = _make_returns()

        # Act
        weights = hrp_weights(returns)

        # Assert：按 symbol 字典序排序，覆盖全部标的
        assert list(weights.index) == sorted(SYMBOLS)

    def test_explicit_cov_respected(self) -> None:
        # Arrange：显式传入年化协方差
        returns = _make_returns()
        cov = returns.cov() * 252

        # Act
        weights = hrp_weights(returns, cov=cov)

        # Assert
        assert abs(weights.sum() - 1.0) < 1e-9
        assert list(weights.index) == sorted(SYMBOLS)

    @pytest.mark.parametrize("method", list(VALID_LINKAGE))
    def test_all_linkage_methods_do_not_crash(self, method: str) -> None:
        # Arrange
        returns = _make_returns()

        # Act
        weights = hrp_weights(returns, linkage_method=method)

        # Assert：任何连接方式都产出合法权重
        assert abs(weights.sum() - 1.0) < 1e-9
        assert (weights >= 0.0).all()

    def test_two_assets_minimal_case(self) -> None:
        # Arrange：最小合法资产数
        returns = _make_returns()[["AAPL", "MSFT"]]

        # Act
        weights = hrp_weights(returns)

        # Assert
        assert len(weights) == 2
        assert abs(weights.sum() - 1.0) < 1e-9


class TestHrpValidation:
    def test_invalid_linkage_raises(self) -> None:
        # Arrange
        returns = _make_returns()

        # Act / Assert
        with pytest.raises(ValueError, match="linkage_method"):
            hrp_weights(returns, linkage_method="bogus")

    def test_single_asset_raises(self) -> None:
        # Arrange
        returns = _make_returns()[["AAPL"]]

        # Act / Assert
        with pytest.raises(ValueError, match="至少需要 2 个资产"):
            hrp_weights(returns)
