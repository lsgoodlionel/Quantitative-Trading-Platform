"""
特征分布漂移检测测试（V4 · M6）

覆盖契约 §4.2 的五条验收，外加数值边界：

1. 同分布数据 → is_drifting=False
2. 明显平移的数据 → is_drifting=True 且 outlier_ratio 显著上升
3. **标准化用的是训练集统计量** —— 「新数据方差大 10 倍」的用例；
   若误用新数据的统计量，方差会被归一化掉，这个用例会漏报
4. 训练集超过抽样上限时 sampled_n 如实出现在报告里
5. 特征全常数（std=0）不产生 NaN / 除零
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.drift import (
    DEFAULT_OUTLIER_RATIO_THRESHOLD,
    DriftAwareAlphaModel,
    DriftError,
    build_baseline,
    detect_drift,
)
from app.quant.models.linear import LassoAlphaModel
from app.quant.models.template import ModelNotFittedError

FEATURES = ["f1", "f2", "f3"]


def _gaussian(n: int, seed: int, shift: float = 0.0, scale: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    values = rng.normal(loc=shift, scale=scale, size=(n, len(FEATURES)))
    return pd.DataFrame(values, columns=FEATURES)


# ── 1. 同分布 ─────────────────────────────────────────────────────

class TestSameDistribution:
    def test_same_distribution_is_not_drifting(self):
        # Arrange
        baseline = build_baseline(_gaussian(400, seed=1))

        # Act
        report = detect_drift(baseline, _gaussian(200, seed=2))

        # Assert
        assert report.is_drifting is False
        assert report.outlier_ratio <= DEFAULT_OUTLIER_RATIO_THRESHOLD
        assert report.n_samples == 200

    def test_report_carries_raw_evidence_not_just_verdict(self):
        """契约 §2.2.4：阈值与 outlier_ratio 原始值都必须在报告里。"""
        baseline = build_baseline(_gaussian(200, seed=3))

        payload = detect_drift(baseline, _gaussian(100, seed=4)).to_dict()

        for key in ("threshold", "outlier_ratio", "outlier_ratio_threshold", "di_p95"):
            assert key in payload
        assert "verdict_note" in payload


# ── 2. 平移 ───────────────────────────────────────────────────────

class TestShiftedDistribution:
    def test_shifted_data_is_flagged_with_higher_outlier_ratio(self):
        # Arrange
        baseline = build_baseline(_gaussian(400, seed=1))
        same = detect_drift(baseline, _gaussian(200, seed=2))

        # Act：整体平移 5 个标准差
        shifted = detect_drift(baseline, _gaussian(200, seed=2, shift=5.0))

        # Assert
        assert shifted.is_drifting is True
        assert shifted.outlier_ratio > same.outlier_ratio
        assert shifted.di_mean > same.di_mean

    def test_di_grows_monotonically_with_shift(self):
        baseline = build_baseline(_gaussian(400, seed=1))

        di_means = [
            detect_drift(baseline, _gaussian(150, seed=7, shift=s)).di_mean
            for s in (0.0, 2.0, 6.0)
        ]

        assert di_means[0] < di_means[1] < di_means[2]


# ── 3. 标准化必须用训练集统计量（本 wave 最致命的一处）────────────

class TestTrainStatisticsAreUsed:
    def test_variance_inflation_is_detected(self):
        """
        新数据方差是训练集的 10 倍 —— 用**训练集** mean/std 标准化时，
        新样本会散到训练集云团之外，判为漂移。

        若实现误用新数据自身的 mean/std，这团数据会被归一化回单位方差，
        与训练集云团重合，`is_drifting` 变成 False —— 本用例即漏报。
        """
        # Arrange
        baseline = build_baseline(_gaussian(500, seed=11, scale=1.0))

        # Act
        report = detect_drift(baseline, _gaussian(300, seed=12, scale=10.0))

        # Assert
        assert report.is_drifting is True
        assert report.outlier_ratio > DEFAULT_OUTLIER_RATIO_THRESHOLD

    def test_baseline_stats_come_from_train_not_from_new_data(self):
        """直接断言 baseline 存的就是训练集的逐列 mean/std。"""
        train = _gaussian(300, seed=13, shift=7.0, scale=3.0)

        baseline = build_baseline(train)

        np.testing.assert_allclose(baseline.mean, train.mean().to_numpy(), rtol=1e-9)
        np.testing.assert_allclose(baseline.std, train.std(ddof=0).to_numpy(), rtol=1e-9)

    def test_detect_drift_is_pure_wrt_new_data_statistics(self):
        """
        同一批新数据，整体缩放后结论必须改变。

        这是「有没有偷用新数据统计量」的行为学证据：若用新数据自身的 mean/std，
        缩放会被完全吸收，两次报告的 DI 会一模一样。
        """
        baseline = build_baseline(_gaussian(400, seed=14))
        new = _gaussian(200, seed=15)

        plain = detect_drift(baseline, new)
        scaled = detect_drift(baseline, new * 8.0)

        assert scaled.di_mean > plain.di_mean * 2


# ── 4. 抽样口径必须如实上报 ───────────────────────────────────────

class TestSamplingIsReported:
    def test_sampled_n_appears_when_train_exceeds_cap(self):
        # Arrange：1000 行训练集，上限 100
        train = _gaussian(1000, seed=21)

        # Act
        baseline = build_baseline(train, max_reference_rows=100)
        report = detect_drift(baseline, _gaussian(50, seed=22))

        # Assert
        assert baseline.sampled_n == 100
        assert baseline.n_train_rows == 1000
        assert baseline.is_sampled is True
        assert report.sampled_n == 100
        assert report.n_train_rows == 1000
        assert report.is_sampled is True
        assert "100" in report.to_dict()["sampling_note"]
        assert "1000" in report.to_dict()["sampling_note"]

    def test_no_sampling_below_cap_is_stated_explicitly(self):
        baseline = build_baseline(_gaussian(80, seed=23), max_reference_rows=1000)

        report = detect_drift(baseline, _gaussian(20, seed=24))

        assert report.is_sampled is False
        assert report.sampled_n == report.n_train_rows == 80
        assert "未抽样" in report.to_dict()["sampling_note"]

    def test_sampling_is_deterministic_for_same_seed(self):
        """同一份训练集两次建 baseline，抽到的参考集必须一致 —— 否则结论会来回跳。"""
        train = _gaussian(500, seed=25)

        first = build_baseline(train, max_reference_rows=50)
        second = build_baseline(train, max_reference_rows=50)

        np.testing.assert_array_equal(first.reference, second.reference)
        assert first.scale == second.scale


# ── 5. 数值边界 ───────────────────────────────────────────────────

class TestNumericEdgeCases:
    def test_constant_features_produce_no_nan(self):
        """特征全常数（std=0）：不得出现 NaN / 除零。"""
        train = pd.DataFrame({"f1": [1.0] * 50, "f2": [2.0] * 50, "f3": [3.0] * 50})

        baseline = build_baseline(train)
        report = detect_drift(baseline, train.head(10))

        assert baseline.degenerate is True
        assert np.isfinite(report.di_mean)
        assert np.isfinite(report.di_p95)
        assert report.outlier_ratio == 0.0
        assert report.is_drifting is False

    def test_constant_train_still_detects_a_shifted_new_batch(self):
        """常数训练集 + 平移过的新数据：退化尺度下仍要检得出来。"""
        train = pd.DataFrame({"f1": [1.0] * 50, "f2": [2.0] * 50, "f3": [3.0] * 50})
        baseline = build_baseline(train)

        report = detect_drift(baseline, train.head(10) + 10.0)

        assert report.is_drifting is True
        assert np.isfinite(report.di_mean)

    def test_one_constant_column_among_normal_ones(self):
        train = _gaussian(200, seed=31)
        train["f3"] = 5.0

        baseline = build_baseline(train)
        report = detect_drift(baseline, _gaussian(100, seed=32).assign(f3=5.0))

        assert baseline.degenerate is False
        assert np.isfinite(report.di_mean)
        assert report.is_drifting is False

    def test_new_rows_with_nan_are_dropped_and_counted(self):
        baseline = build_baseline(_gaussian(200, seed=33))
        new = _gaussian(50, seed=34)
        new.iloc[:5, 0] = np.nan

        report = detect_drift(baseline, new)

        assert report.n_dropped_rows == 5
        assert report.n_samples == 45


# ── 输入校验 ──────────────────────────────────────────────────────

class TestValidation:
    def test_missing_column_raises_drift_error(self):
        baseline = build_baseline(_gaussian(100, seed=41))

        with pytest.raises(DriftError, match="特征列"):
            detect_drift(baseline, _gaussian(20, seed=42).drop(columns=["f2"]))

    def test_all_nan_new_data_raises(self):
        baseline = build_baseline(_gaussian(100, seed=43))
        new = _gaussian(20, seed=44)
        new.iloc[:, 0] = np.nan

        with pytest.raises(DriftError, match="清洗后为空"):
            detect_drift(baseline, new)

    def test_train_with_single_row_raises(self):
        with pytest.raises(DriftError, match="不足 2 行"):
            build_baseline(_gaussian(1, seed=45))

    def test_non_numeric_column_raises(self):
        train = _gaussian(50, seed=46)
        train["f2"] = "abc"

        with pytest.raises(DriftError, match="非数值"):
            build_baseline(train)

    def test_illegal_thresholds_rejected(self):
        baseline = build_baseline(_gaussian(50, seed=47))

        with pytest.raises(DriftError):
            detect_drift(baseline, _gaussian(10, seed=48), di_threshold=0)
        with pytest.raises(DriftError):
            detect_drift(baseline, _gaussian(10, seed=48), outlier_ratio_threshold=1.5)


# ── 分布快照随模型持久化 ──────────────────────────────────────────

def _labeled(n: int, seed: int, shift: float = 0.0, scale: float = 1.0) -> pd.DataFrame:
    frame = _gaussian(n, seed=seed, shift=shift, scale=scale)
    rng = np.random.default_rng(seed + 500)
    frame["label"] = frame["f1"] * 0.5 + rng.normal(scale=0.1, size=n)
    return frame


class TestDriftAwareModel:
    def test_fit_builds_baseline_automatically(self):
        """调用方没有机会忘记存训练集统计量 —— fit 就把它建好了。"""
        model = DriftAwareAlphaModel(LassoAlphaModel())

        model.fit(_labeled(200, seed=51))

        assert model.baseline.n_train_rows == 200
        assert model.baseline.feature_names == tuple(FEATURES)

    def test_baseline_before_fit_raises(self):
        model = DriftAwareAlphaModel(LassoAlphaModel())

        with pytest.raises(ModelNotFittedError):
            _ = model.baseline

    def test_predict_matches_inner_model_value_by_value(self):
        """包装不改变预测 —— 否则「加个漂移检测」就变成了偷偷换模型。"""
        train = _labeled(200, seed=52)
        features = train.drop(columns=["label"])

        wrapped = DriftAwareAlphaModel(LassoAlphaModel())
        wrapped.fit(train)
        bare = LassoAlphaModel()
        bare.fit(train)

        pd.testing.assert_series_equal(
            wrapped.predict(features), bare.predict(features), check_names=False
        )

    def test_survives_pickle_round_trip_with_baseline(self):
        """产物库用 pickle 存模型：分布快照必须跟着一起活下来。"""
        import pickle

        model = DriftAwareAlphaModel(LassoAlphaModel())
        model.fit(_labeled(200, seed=53))

        restored = pickle.loads(pickle.dumps(model))

        assert restored.baseline.n_train_rows == model.baseline.n_train_rows
        np.testing.assert_allclose(restored.baseline.mean, model.baseline.mean)
        np.testing.assert_array_equal(restored.baseline.reference, model.baseline.reference)

    def test_check_drift_uses_this_models_training_distribution(self):
        """
        契约 §2.2.2：判漂移用的必须是**那个模型训练时**的分布。

        两个模型分别在两段不同分布上训练；同一批新数据对它们的结论应当不同。
        """
        near = DriftAwareAlphaModel(LassoAlphaModel())
        near.fit(_labeled(300, seed=54, shift=0.0))
        far = DriftAwareAlphaModel(LassoAlphaModel())
        far.fit(_labeled(300, seed=55, shift=20.0))

        new_features = _gaussian(100, seed=56, shift=0.0)

        assert near.check_drift(new_features).is_drifting is False
        assert far.check_drift(new_features).is_drifting is True

    def test_detail_exposes_inner_detail_and_baseline(self):
        model = DriftAwareAlphaModel(LassoAlphaModel())
        model.fit(_labeled(200, seed=57))

        detail = model.detail()

        assert detail["model"] == "drift_aware:lasso"
        assert "feature_importance" in detail["inner"]
        assert detail["drift_baseline"]["n_train_rows"] == 200
        assert "sampling_note" in detail["drift_baseline"]

    def test_rejects_non_template_inner_model(self):
        with pytest.raises(TypeError):
            DriftAwareAlphaModel(object())  # type: ignore[arg-type]
