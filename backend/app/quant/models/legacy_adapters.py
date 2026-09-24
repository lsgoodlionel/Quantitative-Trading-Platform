"""
既有三套 ML 实现的适配层（V4 · M5）

把 `ml_strategy.py` / `double_ensemble.py` / `models/sequence.py` 三套接口各异的
实现收编到 `AlphaModelTemplate` 下，**不重写它们的算法**：
特征工程、分类器构造、集成内核、网络训练循环全部是 import 过来直接调的，
本文件只负责「按原样的顺序把它们串起来」。

理由与 Wave K-d 的 `LegacyStrategyAlphaAdapter` 相同 ——
既有实现有测试背书、有用户在用，重写等于把已验证的东西推倒重来。

**验收标准：包装后 `predict()` 的输出与直接调用原实现逐值相等。**
由 `tests/test_model_template.py::TestAdapterParity` 守住。本文件里「镜像」了
原实现公式的地方只有两处，都很短且都有等值断言看着 —— 上游一改口径就立刻变红：

  1. `_training_frame` / `_split_point`（约 10 行）
     —— 对应 `ml_strategy.train_ml_strategy` 的特征/标签构建与时序切分。
  2. `_fit_scaler` / `_apply_scaler`（约 6 行）
     —— 对应 `sequence._standardize`。之所以不能直接调它：它的签名是
     `(train, test) -> (train, test)`，统计量算完就丢，而适配器必须把
     训练段的 mean/std 留到 `predict()` 时复用。

接口约定的偏差（刻意为之）
------------------------------------------------------------------
`AlphaModelTemplate` 默认约定 `fit(train)` 收一张「特征列 + label 列」的宽表，
但这三套实现**自带特征工程**：它们要的是原始 OHLCV。因此这三个适配器的
`fit` / `predict` 收 OHLCV DataFrame，返回的仍是与模板一致的 `pd.Series`。
硬把 OHLCV 塞进宽表格式反而会绕过原实现的特征工程 —— 那就成重写了。

`valid` 参数只用于额外算一份验证指标（落在 `detail()["valid_metrics"]`），
**不参与训练**：训练集切分必须与原实现一致，否则等值验收无从谈起。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.quant.double_ensemble import DoubleEnsembleClassifier, DoubleEnsembleConfig
from app.quant.ml_strategy import (
    FEATURE_NAMES,
    ModelType,
    _build_features,
    _build_pipeline,
    _extract_feature_importance,
)
from app.quant.models.sequence import (
    MIN_SAMPLES as SEQ_MIN_SAMPLES,
)
from app.quant.models.sequence import (
    TORCH_INSTALL_HINT,
    SequenceConfig,
    _build_sequences,
    _fit_network,
    _predict_proba,
    torch_available,
)
from app.quant.models.template import (
    AlphaModelTemplate,
    ModelNotFittedError,
    evaluate_ic,
)

# 与 ml_strategy / double_ensemble 一致的训练门槛与切分下限
MIN_SAMPLES = 100
MIN_TEST_ROWS = 30
LABEL_COLUMN = "target"


# ── 共用数据准备（镜像原实现的口径，等值测试守住漂移） ──────────────

def _training_frame(df: pd.DataFrame, forward_days: int) -> pd.DataFrame:
    """
    特征 + 「n 日后收益为正」标签，整体 dropna —— 与原实现同口径。

    末尾 `forward_days` 根 bar 的未来收益是 NaN。曾经这里写的是
    `(fwd_ret > 0).astype(int)`，会把 NaN 判成 False（标签 0）——
    「未来未知」被标成「跌」，且 dropna 剔不掉（标签已是 0 而非 NaN）。
    该缺陷在三处原实现与本镜像中已一并修正：用 `.where(notna)` 保住 NaN，
    交给 dropna 剔除。**四处必须同步**，否则等值测试会红 —— 那正是提醒机制。
    """
    features = _build_features(df)
    fwd_ret = df["close"].pct_change(forward_days).shift(-forward_days)
    target = (fwd_ret > 0).astype(float).where(fwd_ret.notna())
    return pd.concat([features, target.rename(LABEL_COLUMN)], axis=1).dropna()


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """只要特征的清洗结果。行集是 `_training_frame` 的超集（多出末尾无标签的几根）。"""
    return _build_features(df).dropna()


def _split_point(n_rows: int, test_size: float) -> int:
    """训练/测试切分位（不打乱，时序切分）—— 与原实现同公式。"""
    n_test = max(int(n_rows * test_size), MIN_TEST_ROWS)
    return n_rows - n_test


def _require_min_samples(n_rows: int, minimum: int = MIN_SAMPLES) -> None:
    if n_rows < minimum:
        raise ValueError(f"清洗后样本不足: {n_rows}（需 >= {minimum}）")


class _LegacyOhlcvAdapter(AlphaModelTemplate):
    """三个适配器的公共骨架：OHLCV 进、概率 Series 出。"""

    def __init__(self, forward_days: int, test_size: float) -> None:
        self.forward_days = int(forward_days)
        self.test_size = float(test_size)
        self._valid_metrics: dict[str, float] = {}
        self._n_train = 0
        self._n_test = 0

    def _record_valid(self, valid: pd.DataFrame | None) -> None:
        """在独立验证集上算一份 IC —— 只做评估，不回头影响训练。"""
        if valid is None:
            self._valid_metrics = {}
            return
        frame = _training_frame(valid, self.forward_days)
        if frame.empty:
            self._valid_metrics = {}
            return
        self._valid_metrics = evaluate_ic(
            self.predict(valid).reindex(frame.index), frame[LABEL_COLUMN]
        )

    def _base_detail(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "forward_days": self.forward_days,
            "test_size": self.test_size,
            "feature_names": list(FEATURE_NAMES),
            "n_train": self._n_train,
            "n_test": self._n_test,
            "valid_metrics": dict(self._valid_metrics),
        }


# ── ml_strategy.py ────────────────────────────────────────────────

class MLStrategyAdapter(_LegacyOhlcvAdapter):
    """包装 `app/quant/ml_strategy.py`：StandardScaler + 三选一分类器。"""

    name = "ml_strategy"

    def __init__(
        self,
        model_type: ModelType = "random_forest",
        forward_days: int = 5,
        test_size: float = 0.2,
    ) -> None:
        super().__init__(forward_days, test_size)
        self.model_type = model_type
        self._pipeline: Any = None

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        frame = _training_frame(train, self.forward_days)
        _require_min_samples(len(frame))
        n_train = _split_point(len(frame), self.test_size)

        # 分类器由原实现的 `_build_pipeline` 构造，超参与随机种子原样沿用
        self._pipeline = _build_pipeline(self.model_type)
        self._pipeline.fit(
            frame[FEATURE_NAMES].values[:n_train], frame[LABEL_COLUMN].values[:n_train]
        )
        self._n_train, self._n_test = n_train, len(frame) - n_train
        self._record_valid(valid)

    def predict(self, features: pd.DataFrame) -> pd.Series:
        """返回正类（n 日后收益为正）概率，index 与清洗后的特征行对齐。"""
        if self._pipeline is None:
            raise ModelNotFittedError("MLStrategyAdapter 尚未训练，先调用 fit()")
        frame = _feature_frame(features)
        prob = self._pipeline.predict_proba(frame[FEATURE_NAMES].values)[:, 1]
        return pd.Series(prob, index=frame.index, name=self.name)

    def detail(self) -> dict[str, Any]:
        if self._pipeline is None:
            raise ModelNotFittedError("MLStrategyAdapter 尚未训练，先调用 fit()")
        return {
            **self._base_detail(),
            "model_type": self.model_type,
            # 重要度提取同样复用原实现的函数，避免两套口径
            "feature_importance": _extract_feature_importance(self._pipeline),
        }


# ── double_ensemble.py ────────────────────────────────────────────

class DoubleEnsembleAdapter(_LegacyOhlcvAdapter):
    """包装 `app/quant/double_ensemble.py`：样本重加权 + 特征筛选的集成分类器。"""

    name = "double_ensemble"

    def __init__(
        self,
        config: DoubleEnsembleConfig | None = None,
        forward_days: int = 5,
        test_size: float = 0.2,
    ) -> None:
        super().__init__(forward_days, test_size)
        self.config = config or DoubleEnsembleConfig()
        self._classifier: DoubleEnsembleClassifier | None = None

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        frame = _training_frame(train, self.forward_days)
        _require_min_samples(len(frame))
        n_train = _split_point(len(frame), self.test_size)

        self._classifier = DoubleEnsembleClassifier(self.config).fit(
            frame[FEATURE_NAMES].values[:n_train],
            frame[LABEL_COLUMN].values[:n_train],
            FEATURE_NAMES,
        )
        self._n_train, self._n_test = n_train, len(frame) - n_train
        self._record_valid(valid)

    def predict(self, features: pd.DataFrame) -> pd.Series:
        if self._classifier is None:
            raise ModelNotFittedError("DoubleEnsembleAdapter 尚未训练，先调用 fit()")
        frame = _feature_frame(features)
        prob = self._classifier.predict_proba(frame[FEATURE_NAMES].values)[:, 1]
        return pd.Series(prob, index=frame.index, name=self.name)

    def detail(self) -> dict[str, Any]:
        if self._classifier is None:
            raise ModelNotFittedError("DoubleEnsembleAdapter 尚未训练，先调用 fit()")
        cfg = self.config
        return {
            **self._base_detail(),
            "feature_importance": self._classifier.aggregated_importance(),
            "feature_usage": self._classifier.feature_usage(),
            "sub_feature_counts": [int(len(f)) for f in self._classifier.sub_features],
            "hyperparams": {
                "num_models": cfg.num_models,
                "enable_sr": cfg.enable_sr,
                "enable_fs": cfg.enable_fs,
                "n_estimators": cfg.n_estimators,
                "max_depth": cfg.max_depth,
                "learning_rate": cfg.learning_rate,
            },
        }


# ── models/sequence.py ────────────────────────────────────────────

class SequenceAdapter(_LegacyOhlcvAdapter):
    """
    包装 `app/quant/models/sequence.py`：LSTM / GRU / ALSTM。

    torch 是可选依赖，未安装时 `fit()` 抛 `RuntimeError`（与原实现一致），
    `torch_ready()` 供调用方先行探测。
    """

    name = "sequence"

    def __init__(self, config: SequenceConfig | None = None) -> None:
        cfg = config or SequenceConfig()
        super().__init__(cfg.forward_days, cfg.test_size)
        self.config = cfg
        self._net: Any = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None
        self._train_curve: list[float] = []
        self._val_curve: list[float] = []

    @staticmethod
    def torch_ready() -> bool:
        return torch_available()

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        if not torch_available():
            raise RuntimeError(TORCH_INSTALL_HINT)
        cfg = self.config
        windows, labels, _ = _build_sequences(train, cfg.forward_days, cfg.seq_len)
        _require_min_samples(len(windows), minimum=SEQ_MIN_SAMPLES)
        n_train = _split_point(len(windows), cfg.test_size)

        # 标准化统计量只用训练段拟合（防泄漏），公式与 sequence._standardize 一致
        self._mean, self._std = _fit_scaler(windows[:n_train])
        x_train = _apply_scaler(windows[:n_train], self._mean, self._std)
        x_test = _apply_scaler(windows[n_train:], self._mean, self._std)

        self._net, self._train_curve, self._val_curve = _fit_network(
            cfg, x_train, labels[:n_train], x_test, labels[n_train:]
        )
        self._n_train, self._n_test = n_train, len(windows) - n_train
        self._record_valid(valid)

    def predict(self, features: pd.DataFrame) -> pd.Series:
        """按滑动窗口逐点推理；index 为每个窗口末端的时间字符串。"""
        if self._net is None:
            raise ModelNotFittedError("SequenceAdapter 尚未训练，先调用 fit()")
        windows, _, times = _build_sequences(
            features, self.config.forward_days, self.config.seq_len
        )
        scaled = _apply_scaler(windows, self._mean, self._std)
        return pd.Series(_predict_proba(self._net, scaled), index=times, name=self.name)

    def _record_valid(self, valid: pd.DataFrame | None) -> None:
        """序列模型的样本粒度是窗口，验证集要按窗口重建后再对齐。"""
        if valid is None:
            self._valid_metrics = {}
            return
        _, labels, times = _build_sequences(
            valid, self.config.forward_days, self.config.seq_len
        )
        self._valid_metrics = evaluate_ic(
            self.predict(valid), pd.Series(labels, index=times)
        )

    def detail(self) -> dict[str, Any]:
        if self._net is None:
            raise ModelNotFittedError("SequenceAdapter 尚未训练，先调用 fit()")
        cfg = self.config
        return {
            **self._base_detail(),
            "model_type": cfg.model_type,
            "train_loss_curve": list(self._train_curve),
            "val_loss_curve": list(self._val_curve),
            "hyperparams": {
                "seq_len": cfg.seq_len,
                "epochs": cfg.epochs,
                "hidden_size": cfg.hidden_size,
                "num_layers": cfg.num_layers,
                "learning_rate": cfg.learning_rate,
                "dropout": cfg.dropout,
            },
        }


def _fit_scaler(x_train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按特征维求均值/标准差（零方差列置 1）—— 与 sequence._standardize 同式。"""
    flat = x_train.reshape(-1, x_train.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0)
    std[std == 0] = 1.0
    return mean, std


def _apply_scaler(x: np.ndarray, mean: np.ndarray | None, std: np.ndarray | None) -> np.ndarray:
    if mean is None or std is None:
        raise ModelNotFittedError("标准化统计量缺失，先调用 fit()")
    return (x - mean) / std
