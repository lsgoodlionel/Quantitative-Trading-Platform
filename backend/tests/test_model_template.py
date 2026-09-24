"""
统一 ML 模型模板测试（V4 · M5）

覆盖契约 §3.3 的三条验收：
1. **三个适配器包装后的 predict 与直接调用原实现逐值相等**
   —— 这是「没有重写算法」的证据，也是本文件最重要的一组断言。
2. 新增的 Lasso / GradientBoosting 实现能 fit + predict + detail
3. 训练 → 存 Lab → 重新加载 → predict 一致（端到端，M4 × M5 衔接）

torch 未安装时，SequenceAdapter 的端到端等值测试自动跳过；
其数据准备与标准化口径的等值断言不依赖 torch，照常执行。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.double_ensemble import DoubleEnsembleConfig, train_double_ensemble
from app.quant.ml_strategy import FEATURE_NAMES, train_ml_strategy
from app.quant.models.boosting import GradientBoostingAlphaModel
from app.quant.models.legacy_adapters import (
    DoubleEnsembleAdapter,
    MLStrategyAdapter,
    SequenceAdapter,
    _apply_scaler,
    _fit_scaler,
    _training_frame,
)
from app.quant.models.linear import LassoAlphaModel
from app.quant.models.sequence import (
    SequenceConfig,
    _build_sequences,
    _standardize,
    torch_available,
)
from app.quant.models.template import (
    AlphaModelTemplate,
    ModelNotFittedError,
    align_features,
    evaluate_ic,
    split_features_label,
)
from tests.test_lab_store import InMemoryMetaStore

# ── 公用数据 ──────────────────────────────────────────────────────


def _make_ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """合成 OHLCV DataFrame（DatetimeIndex），与 test_double_ensemble 同构。"""
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


def _make_wide_table(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """特征列 + label 列的宽表，供 Lasso / GBDT 使用。前两列携带信号。"""
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(n, 5))
    label = 1.5 * features[:, 0] - 0.8 * features[:, 1] + rng.normal(scale=0.3, size=n)
    frame = pd.DataFrame(features, columns=[f"f{i}" for i in range(5)])
    frame["label"] = label
    frame.index = pd.date_range("2020-01-01", periods=n, freq="D")
    return frame


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    return _make_ohlcv()


@pytest.fixture(scope="module")
def wide_table() -> pd.DataFrame:
    return _make_wide_table()


def _legacy_probabilities(result) -> dict[str, float]:
    """把原实现返回的 predictions 摊平成 {时间: 概率}。"""
    return {p["time"]: p["probability"] for p in result.predictions}


# ── 1. 适配器与原实现逐值相等 ──────────────────────────────────────


class TestAdapterParity:
    """包装后的 predict 必须与直接调用原实现逐值相等 —— 没有重写算法的证据。"""

    @pytest.mark.parametrize(
        "model_type", ["logistic_regression", "random_forest", "gradient_boosting"]
    )
    def test_ml_strategy_adapter_matches_legacy(self, ohlcv, model_type) -> None:
        # Arrange: 直接调原实现
        legacy = train_ml_strategy(ohlcv, model_type=model_type, forward_days=5, test_size=0.2)

        # Act: 走适配器
        adapter = MLStrategyAdapter(model_type=model_type, forward_days=5, test_size=0.2)
        adapter.fit(ohlcv)
        predicted = adapter.predict(ohlcv)

        # Assert: 原实现给出的每一根 K 线概率都能在适配器输出里逐值对上
        expected = _legacy_probabilities(legacy)
        assert len(expected) == 30
        for time_key, prob in expected.items():
            assert round(float(predicted.loc[pd.Timestamp(time_key)]), 4) == prob

        # 原实现用于生成最新信号的那一根（清洗后最后一行）概率同样相等
        assert round(float(predicted.iloc[-1]), 4) == legacy.recent_prob

    def test_double_ensemble_adapter_matches_legacy(self, ohlcv) -> None:
        # Arrange
        config = DoubleEnsembleConfig(num_models=2)
        legacy = train_double_ensemble(ohlcv, forward_days=5, test_size=0.2, config=config)

        # Act
        adapter = DoubleEnsembleAdapter(config=config, forward_days=5, test_size=0.2)
        adapter.fit(ohlcv)
        predicted = adapter.predict(ohlcv)

        # Assert
        expected = _legacy_probabilities(legacy)
        assert expected
        for time_key, prob in expected.items():
            assert round(float(predicted.loc[pd.Timestamp(time_key)]), 4) == prob
        assert adapter.detail()["feature_importance"] == legacy.feature_importance

    def test_sequence_adapter_prep_matches_legacy(self, ohlcv) -> None:
        """torch 无关的那一半：序列构造与标准化口径必须与原实现逐值相等。"""
        # Arrange
        config = SequenceConfig(seq_len=10, epochs=1)
        windows, _, _ = _build_sequences(ohlcv, config.forward_days, config.seq_len)
        n_test = max(int(len(windows) * config.test_size), 30)
        n_train = len(windows) - n_test

        # Act
        legacy_train, legacy_test = _standardize(windows[:n_train], windows[n_train:])
        mean, std = _fit_scaler(windows[:n_train])

        # Assert
        np.testing.assert_array_equal(_apply_scaler(windows[:n_train], mean, std), legacy_train)
        np.testing.assert_array_equal(_apply_scaler(windows[n_train:], mean, std), legacy_test)

    @pytest.mark.skipif(not torch_available(), reason="torch 未安装，序列模型不可训练")
    def test_sequence_adapter_matches_legacy(self, ohlcv) -> None:
        from app.quant.models.sequence import train_sequence_model

        config = SequenceConfig(seq_len=10, epochs=3)
        legacy = train_sequence_model(ohlcv, config)

        adapter = SequenceAdapter(config)
        adapter.fit(ohlcv)
        predicted = adapter.predict(ohlcv)

        expected = _legacy_probabilities(legacy)
        assert expected
        for time_key, prob in expected.items():
            assert round(float(predicted.loc[time_key]), 4) == prob


class TestAdapterBehaviour:
    def test_adapters_are_alpha_model_templates(self) -> None:
        for adapter in (MLStrategyAdapter(), DoubleEnsembleAdapter(), SequenceAdapter()):
            assert isinstance(adapter, AlphaModelTemplate)
            assert adapter.name

    def test_predict_before_fit_raises(self, ohlcv) -> None:
        for adapter in (MLStrategyAdapter(), DoubleEnsembleAdapter(), SequenceAdapter()):
            with pytest.raises(ModelNotFittedError):
                adapter.predict(ohlcv)
            with pytest.raises(ModelNotFittedError):
                adapter.detail()

    def test_detail_reports_split_and_importance(self, ohlcv) -> None:
        adapter = MLStrategyAdapter(model_type="logistic_regression")
        adapter.fit(ohlcv)

        detail = adapter.detail()

        assert detail["model"] == "ml_strategy"
        assert detail["n_train"] + detail["n_test"] == len(_training_frame(ohlcv, 5))
        assert {d["name"] for d in detail["feature_importance"]} == set(FEATURE_NAMES)

    def test_valid_set_produces_metrics_without_changing_training(self, ohlcv) -> None:
        # Arrange
        train_df, valid_df = ohlcv.iloc[:300], ohlcv.iloc[250:]

        # Act
        without_valid = MLStrategyAdapter(model_type="logistic_regression")
        without_valid.fit(train_df)
        with_valid = MLStrategyAdapter(model_type="logistic_regression")
        with_valid.fit(train_df, valid=valid_df)

        # Assert：valid 只影响指标，不影响训练结果
        pd.testing.assert_series_equal(
            without_valid.predict(train_df), with_valid.predict(train_df)
        )
        assert with_valid.detail()["valid_metrics"]["n_samples"] > 0
        assert without_valid.detail()["valid_metrics"] == {}

    def test_too_few_samples_raises(self) -> None:
        adapter = MLStrategyAdapter()

        with pytest.raises(ValueError, match="样本不足"):
            adapter.fit(_make_ohlcv(n=80))

    def test_sequence_torch_ready_flag_matches_module(self) -> None:
        assert SequenceAdapter.torch_ready() is torch_available()

    @pytest.mark.skipif(torch_available(), reason="torch 已安装")
    def test_sequence_fit_without_torch_raises(self, ohlcv) -> None:
        with pytest.raises(RuntimeError):
            SequenceAdapter(SequenceConfig(seq_len=10, epochs=1)).fit(ohlcv)


# ── 2. 新增的 Lasso / GradientBoosting 实现 ────────────────────────


class TestNewAlphaModels:
    @pytest.mark.parametrize(
        "factory", [LassoAlphaModel, GradientBoostingAlphaModel], ids=["lasso", "gbdt"]
    )
    def test_fit_predict_detail(self, wide_table, factory) -> None:
        # Arrange
        train, valid = wide_table.iloc[:240], wide_table.iloc[240:]
        model = factory()

        # Act
        model.fit(train, valid=valid)
        prediction = model.predict(valid.drop(columns=["label"]))
        detail = model.detail()

        # Assert
        assert isinstance(model, AlphaModelTemplate)
        assert isinstance(prediction, pd.Series)
        assert list(prediction.index) == list(valid.index)
        assert prediction.notna().all()
        assert detail["hyperparams"]
        assert [d["name"] for d in detail["feature_importance"]][:2] == ["f0", "f1"]
        assert detail["valid_metrics"]["ic"] > 0.5    # 合成数据信号很强，IC 应显著为正

    def test_lasso_zeroes_out_noise_features(self, wide_table) -> None:
        model = LassoAlphaModel(alpha=0.05)
        model.fit(wide_table)

        detail = model.detail()

        assert "f0" in detail["selected_features"]
        assert len(detail["selected_features"]) < len(wide_table.columns) - 1
        assert len(detail["coefficients"]) == 5

    def test_gbdt_reports_backend_substitution(self, wide_table) -> None:
        """lightgbm 不在依赖清单里，替身身份必须在 detail() 里明示。"""
        model = GradientBoostingAlphaModel(max_iter=20)
        model.fit(wide_table)

        detail = model.detail()

        assert detail["backend"] == "sklearn.HistGradientBoostingRegressor"
        assert "lightgbm" in detail["backend_note"]
        assert detail["n_iter"] == 20
        # 默认关闭早停 → sklearn 不记录逐轮分数，必须明示而不是给一个空数组了事
        assert detail["train_loss_curve"] == []
        assert detail["train_curve_available"] is False
        assert detail["train_curve_note"]

    def test_gbdt_train_curve_with_early_stopping(self, wide_table) -> None:
        model = GradientBoostingAlphaModel(max_iter=15, early_stopping=True)
        model.fit(wide_table)

        detail = model.detail()

        assert detail["train_curve_available"] is True
        assert len(detail["train_loss_curve"]) > 1
        # 损失曲线应单调下降（梯度提升逐轮拟合残差）
        assert detail["train_loss_curve"][0] > detail["train_loss_curve"][-1]

    @pytest.mark.parametrize(
        "factory", [LassoAlphaModel, GradientBoostingAlphaModel], ids=["lasso", "gbdt"]
    )
    def test_predict_before_fit_raises(self, wide_table, factory) -> None:
        model = factory()

        with pytest.raises(ModelNotFittedError):
            model.predict(wide_table.drop(columns=["label"]))
        with pytest.raises(ModelNotFittedError):
            model.detail()

    @pytest.mark.parametrize(
        "factory", [LassoAlphaModel, GradientBoostingAlphaModel], ids=["lasso", "gbdt"]
    )
    def test_missing_feature_column_raises(self, wide_table, factory) -> None:
        model = factory()
        model.fit(wide_table)

        with pytest.raises(ValueError, match="缺少训练时使用的列"):
            model.predict(wide_table.drop(columns=["label", "f0"]))

    def test_invalid_hyperparams_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            LassoAlphaModel(alpha=0)
        with pytest.raises(ValueError, match="learning_rate"):
            GradientBoostingAlphaModel(learning_rate=0)
        with pytest.raises(ValueError, match="max_iter"):
            GradientBoostingAlphaModel(max_iter=0)


class TestTemplateHelpers:
    def test_split_features_label(self, wide_table) -> None:
        features, label = split_features_label(wide_table)

        assert "label" not in features.columns
        assert len(features) == len(label) == len(wide_table)

    def test_split_features_label_validates(self, wide_table) -> None:
        with pytest.raises(TypeError):
            split_features_label([1, 2, 3])
        with pytest.raises(ValueError, match="缺少标签列"):
            split_features_label(wide_table.drop(columns=["label"]))
        with pytest.raises(ValueError, match="清洗后为空"):
            split_features_label(wide_table.assign(f0=np.nan))
        with pytest.raises(ValueError, match="没有任何特征列"):
            split_features_label(wide_table[["label"]])

    def test_align_features_validates(self, wide_table) -> None:
        with pytest.raises(TypeError):
            align_features("not a frame", ["f0"])

    def test_evaluate_ic_handles_degenerate_input(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="D")
        constant = pd.Series([1.0] * 5, index=index)

        assert evaluate_ic(constant, constant) == {"ic": 0.0, "rank_ic": 0.0, "n_samples": 5}

    def test_evaluate_ic_perfect_correlation(self) -> None:
        index = pd.date_range("2024-01-01", periods=6, freq="D")
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], index=index)

        result = evaluate_ic(series, series * 2)

        assert result["ic"] == pytest.approx(1.0)
        assert result["rank_ic"] == pytest.approx(1.0)

    def test_default_detail_is_empty(self) -> None:
        class _Noop(AlphaModelTemplate):
            def fit(self, train, valid=None) -> None: ...

            def predict(self, features):
                return pd.Series(dtype=float)

        assert _Noop().detail() == {}


# ── 3. 端到端：训练 → 存 Lab → 重新加载 → predict 一致 ──────────────


class TestLabRoundTrip:
    async def _round_trip(self, tmp_path, model, features) -> pd.Series:
        """存进 Lab，再用一套全新的 store 实例把它读回来并预测。"""
        from app.quant.lab import FileSystemContentStore, LabStore

        meta_store = InMemoryMetaStore()
        root = tmp_path / "lab"
        writer = LabStore(meta_store, FileSystemContentStore(root))
        meta = await writer.save_model(model.name, model, tags={"source": "test"})

        # 新的 store 实例 = 没有任何内存态残留，只能靠落盘的内容还原
        reader = LabStore(meta_store, FileSystemContentStore(root))
        reloaded = await reader.load_model(meta.artifact_id)
        assert reloaded is not model
        return reloaded.predict(features)

    async def test_lasso_survives_lab_round_trip(self, tmp_path, wide_table) -> None:
        model = LassoAlphaModel()
        model.fit(wide_table)
        features = wide_table.drop(columns=["label"])

        reloaded_prediction = await self._round_trip(tmp_path, model, features)

        pd.testing.assert_series_equal(reloaded_prediction, model.predict(features))

    async def test_gbdt_survives_lab_round_trip(self, tmp_path, wide_table) -> None:
        model = GradientBoostingAlphaModel(max_iter=20)
        model.fit(wide_table)
        features = wide_table.drop(columns=["label"])

        reloaded_prediction = await self._round_trip(tmp_path, model, features)

        pd.testing.assert_series_equal(reloaded_prediction, model.predict(features))

    async def test_legacy_adapter_survives_lab_round_trip(self, tmp_path, ohlcv) -> None:
        adapter = MLStrategyAdapter(model_type="logistic_regression")
        adapter.fit(ohlcv)

        reloaded_prediction = await self._round_trip(tmp_path, adapter, ohlcv)

        pd.testing.assert_series_equal(reloaded_prediction, adapter.predict(ohlcv))

    async def test_prediction_can_be_stored_as_signal(self, tmp_path, wide_table) -> None:
        """模型产物与信号产物是两类，各自独立存取。"""
        from app.quant.lab import ArtifactKind, FileSystemContentStore, LabStore

        store = LabStore(InMemoryMetaStore(), FileSystemContentStore(tmp_path / "lab"))
        model = LassoAlphaModel()
        model.fit(wide_table)
        prediction = model.predict(wide_table.drop(columns=["label"]))

        model_meta = await store.save_model("lasso", model)
        signal_meta = await store.save_signal("lasso-signal", prediction)
        _, total = await store.list(kind=ArtifactKind.MODEL)

        pd.testing.assert_series_equal(await store.load_signal(signal_meta.artifact_id), prediction)
        assert total == 1
        assert model_meta.artifact_id != signal_meta.artifact_id
