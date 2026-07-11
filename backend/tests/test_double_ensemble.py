"""DoubleEnsemble 集成模型单元测试（Wave-3 / B6）

覆盖：
- 样本重加权（SR）方向：难样本（高损失）权重高于易样本
- 特征筛选（FS）：开启时子模型使用特征子集、关闭时保留全部特征
- 集成分类器端到端：predict_proba 行和为 1、诊断视图结构完整
- 训练入口 train_double_ensemble 输出结构对齐 DoubleEnsembleResult
- 配置校验：num_models < 1 / sample_ratios 与 bins_fs 不匹配 抛错
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.double_ensemble import (
    DoubleEnsembleClassifier,
    DoubleEnsembleConfig,
    DoubleEnsembleResult,
    train_double_ensemble,
)


# ── 公用构造器 ─────────────────────────────────────────────────

def _make_classification(
    n: int = 400, n_features: int = 8, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """合成二分类数据：前两列携带信号，其余为噪声列。"""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, n_features))
    logits = 1.5 * X[:, 0] - 1.0 * X[:, 1]
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (rng.uniform(size=n) < probs).astype(int)
    return X, y


def _make_ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """合成 OHLCV DataFrame（DatetimeIndex），供训练入口构建特征。"""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, n))
    idx = pd.date_range("2019-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 3_000_000, n).astype(float),
        },
        index=idx,
    )


# ── 样本重加权方向 ─────────────────────────────────────────────

class TestSampleReweight:
    def test_hard_samples_get_higher_weight(self) -> None:
        # Arrange: 前 50 个为难样本（高且稳定损失），后 150 个为易样本（损失衰减）
        clf = DoubleEnsembleClassifier(DoubleEnsembleConfig(num_models=2, bins_sr=5))
        n, n_trees = 200, 20
        loss_values = np.concatenate([np.full(50, 0.9), np.full(150, 0.05)])
        curve = np.zeros((n, n_trees))
        for t in range(n_trees):
            curve[:50, t] = 0.9
            curve[50:, t] = 0.5 * (1 - t / n_trees) + 0.02
        loss_curve = pd.DataFrame(curve)

        # Act
        weights = clf._sample_reweight(loss_curve, loss_values, k_th=1)

        # Assert: 难样本平均权重严格高于易样本
        assert weights[:50].mean() > weights[150:].mean()
        assert np.all(weights > 0)

    def test_reweight_returns_all_positive_weights(self) -> None:
        # Arrange
        clf = DoubleEnsembleClassifier(DoubleEnsembleConfig(num_models=2))
        rng = np.random.default_rng(1)
        n, n_trees = 120, 10
        loss_values = rng.uniform(0.0, 1.0, n)
        loss_curve = pd.DataFrame(rng.uniform(0.0, 1.0, (n, n_trees)))

        # Act
        weights = clf._sample_reweight(loss_curve, loss_values, k_th=2)

        # Assert: 权重全正、长度对齐样本数（无零权重残留）
        assert weights.shape == (n,)
        assert np.all(weights > 0)


# ── 特征筛选 ───────────────────────────────────────────────────

class TestFeatureSelection:
    def test_fs_disabled_keeps_all_features(self) -> None:
        # Arrange
        X, y = _make_classification(seed=1)
        cfg = DoubleEnsembleConfig(
            num_models=3, enable_fs=False, enable_sr=False, n_estimators=20,
        )

        # Act
        clf = DoubleEnsembleClassifier(cfg).fit(X, y)

        # Assert: 每个子模型都使用全部特征列
        assert all(len(feats) == X.shape[1] for feats in clf.sub_features)

    def test_fs_enabled_selects_subset(self) -> None:
        # Arrange
        X, y = _make_classification(seed=1)
        cfg = DoubleEnsembleConfig(
            num_models=3, enable_fs=True, enable_sr=False, n_estimators=20,
        )

        # Act
        clf = DoubleEnsembleClassifier(cfg).fit(X, y)

        # Assert: 至少一个后续子模型使用了特征子集（< 全集）
        assert any(len(feats) < X.shape[1] for feats in clf.sub_features[1:])
        # 首个子模型始终从全集起步
        assert len(clf.sub_features[0]) == X.shape[1]


# ── 集成分类器端到端 ───────────────────────────────────────────

class TestClassifierEndToEnd:
    def test_predict_proba_rows_sum_to_one(self) -> None:
        # Arrange
        X, y = _make_classification(seed=2)
        clf = DoubleEnsembleClassifier(
            DoubleEnsembleConfig(num_models=3, n_estimators=25)
        ).fit(X, y, [f"f{i}" for i in range(X.shape[1])])

        # Act
        proba = clf.predict_proba(X)

        # Assert
        assert proba.shape == (X.shape[0], 2)
        assert np.allclose(proba.sum(axis=1), 1.0)

    def test_predict_returns_binary_labels(self) -> None:
        # Arrange
        X, y = _make_classification(seed=2)
        clf = DoubleEnsembleClassifier(
            DoubleEnsembleConfig(num_models=2, n_estimators=20)
        ).fit(X, y)

        # Act
        preds = clf.predict(X)

        # Assert: 输出仅含 0/1，且在可分数据上准确率高于随机
        assert set(np.unique(preds)).issubset({0, 1})
        assert (preds == y).mean() > 0.6

    def test_single_submodel_config_trains(self) -> None:
        # Arrange: num_models=1 应正常训练（循环内提前 break 分支）
        X, y = _make_classification(seed=3)

        # Act
        clf = DoubleEnsembleClassifier(
            DoubleEnsembleConfig(num_models=1, n_estimators=10)
        ).fit(X, y)

        # Assert
        assert len(clf.submodels) == 1

    def test_diagnostic_views_are_well_formed(self) -> None:
        # Arrange
        X, y = _make_classification(seed=4)
        names = [f"f{i}" for i in range(X.shape[1])]
        clf = DoubleEnsembleClassifier(
            DoubleEnsembleConfig(num_models=3, n_estimators=20)
        ).fit(X, y, names)

        # Act
        importance = clf.aggregated_importance()
        usage = clf.feature_usage()

        # Assert: 两个视图均覆盖全部特征、按降序排列
        assert len(importance) == len(names)
        assert len(usage) == len(names)
        imps = [row["importance"] for row in importance]
        assert imps == sorted(imps, reverse=True)
        counts = [row["used_by"] for row in usage]
        assert counts == sorted(counts, reverse=True)
        assert all(1 <= row["used_by"] <= clf.config.num_models for row in usage)


# ── 训练入口结构 ───────────────────────────────────────────────

class TestTrainDoubleEnsemble:
    def test_result_structure_matches_dataclass(self) -> None:
        # Arrange
        df = _make_ohlcv(n=400, seed=0)
        cfg = DoubleEnsembleConfig(num_models=3, n_estimators=25)

        # Act
        result = train_double_ensemble(df, forward_days=5, config=cfg)

        # Assert: 输出为 DoubleEnsembleResult 且关键字段自洽
        assert isinstance(result, DoubleEnsembleResult)
        assert result.model_type == "double_ensemble"
        assert result.forward_days == 5
        assert result.n_features == len(result.feature_names)
        assert len(result.feature_importance) == result.n_features
        assert len(result.feature_usage) == result.n_features
        assert len(result.sub_feature_counts) == cfg.num_models
        assert result.recent_signal in {"BUY", "SELL", "NEUTRAL"}
        assert len(result.confusion_matrix) == 2
        assert 0.0 <= result.test_accuracy <= 1.0

    def test_too_few_samples_raises(self) -> None:
        # Arrange: 行数远低于 MIN_SAMPLES → 清洗后样本不足
        df = _make_ohlcv(n=60, seed=1)

        # Act / Assert
        with pytest.raises(ValueError):
            train_double_ensemble(df, forward_days=5)


# ── 配置校验 ───────────────────────────────────────────────────

class TestConfigValidation:
    def test_num_models_below_one_raises(self) -> None:
        with pytest.raises(ValueError):
            DoubleEnsembleConfig(num_models=0)

    def test_sample_ratios_length_mismatch_raises(self) -> None:
        # sample_ratios 默认长度 5，与 bins_fs=3 不匹配
        with pytest.raises(ValueError):
            DoubleEnsembleConfig(bins_fs=3)
