"""
梯度提升因子模型（V4 · M5）

契约点名的是 `LightGBMAlphaModel`，但 **lightgbm 不在 `requirements.txt` 里**，
而本 Wave 的红线是「不新增依赖」。这里用 sklearn 的
`HistGradientBoostingRegressor` 顶上 —— 它就是 sklearn 参照 LightGBM 实现的
直方图式梯度提升（分箱建直方图 + leaf-wise 增益寻找 + 原生缺失值处理），
超参语义与 LightGBM 一一对应：

    learning_rate / max_iter(n_estimators) / max_leaf_nodes(num_leaves) /
    min_samples_leaf(min_data_in_leaf) / l2_regularization(lambda_l2)

真要接 lightgbm 时，只需换掉 `_build_regressor()` 一处，
`fit / predict / detail` 与调用方都不用动。

特征重要性：`HistGradientBoostingRegressor` 没有 `feature_importances_`
（直方图实现不维护分裂增益累计），因此在 `fit()` 里用置换重要度算一次并存下来 ——
放到 `detail()` 里再算就得把训练集一起 pickle 进产物库，那是更糟的取舍。

训练曲线：sklearn **只在开启 early stopping 时**才记录 `train_score_`。
默认关闭（它会随机切 10% 做验证集，对时间序列是泄漏），
所以 `detail()["train_loss_curve"]` 默认为空；需要曲线就显式传
`early_stopping=True`，同时 `detail()` 会标出 `train_curve_available`。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.quant.models.template import (
    DEFAULT_LABEL_COLUMN,
    AlphaModelTemplate,
    ModelNotFittedError,
    align_features,
    evaluate_ic,
    importance_pairs,
    split_features_label,
)

DEFAULT_LEARNING_RATE = 0.05
DEFAULT_MAX_ITER = 200
DEFAULT_MAX_LEAF_NODES = 31          # 对齐 LightGBM 的 num_leaves 默认值
DEFAULT_MIN_SAMPLES_LEAF = 20
DEFAULT_L2_REGULARIZATION = 0.0
DEFAULT_RANDOM_STATE = 42

# 置换重要度的重复次数：因子表列数不多，3 次足够稳定又不至于把 fit 拖慢
PERMUTATION_REPEATS = 3


class GradientBoostingAlphaModel(AlphaModelTemplate):
    """直方图式梯度提升回归因子模型（LightGBM 的无依赖替身）。"""

    name = "gradient_boosting"

    def __init__(
        self,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        max_iter: int = DEFAULT_MAX_ITER,
        max_leaf_nodes: int = DEFAULT_MAX_LEAF_NODES,
        min_samples_leaf: int = DEFAULT_MIN_SAMPLES_LEAF,
        l2_regularization: float = DEFAULT_L2_REGULARIZATION,
        random_state: int = DEFAULT_RANDOM_STATE,
        early_stopping: bool = False,
        label_column: str = DEFAULT_LABEL_COLUMN,
    ) -> None:
        if learning_rate <= 0:
            raise ValueError("learning_rate 必须 > 0")
        if max_iter < 1:
            raise ValueError("max_iter 必须 >= 1")
        self.learning_rate = float(learning_rate)
        self.max_iter = int(max_iter)
        self.max_leaf_nodes = int(max_leaf_nodes)
        self.min_samples_leaf = int(min_samples_leaf)
        self.l2_regularization = float(l2_regularization)
        self.random_state = int(random_state)
        self.early_stopping = bool(early_stopping)
        self.label_column = label_column
        self._model: Any = None
        self._feature_names: list[str] = []
        self._importance: list[dict[str, Any]] = []
        self._valid_metrics: dict[str, float] = {}

    def _build_regressor(self) -> Any:
        """唯一与具体 GBDT 实现耦合的地方 —— 换 lightgbm 只改这里。"""
        from sklearn.ensemble import HistGradientBoostingRegressor

        return HistGradientBoostingRegressor(
            learning_rate=self.learning_rate,
            max_iter=self.max_iter,
            max_leaf_nodes=self.max_leaf_nodes,
            min_samples_leaf=self.min_samples_leaf,
            l2_regularization=self.l2_regularization,
            random_state=self.random_state,
            early_stopping=self.early_stopping,
        )

    # ── 训练 ──────────────────────────────────────────────────────

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        features, label = split_features_label(train, self.label_column)
        self._feature_names = list(features.columns)
        self._model = self._build_regressor()
        self._model.fit(features.to_numpy(dtype=float), label.to_numpy(dtype=float))
        self._importance = self._permutation_importance(features, label)
        self._valid_metrics = self._score_valid(valid)

    def _permutation_importance(
        self, features: pd.DataFrame, label: pd.Series
    ) -> list[dict[str, Any]]:
        """置换重要度（训练集上算一次，结果随模型一起存进产物库）。"""
        from sklearn.inspection import permutation_importance

        result = permutation_importance(
            self._model,
            features.to_numpy(dtype=float),
            label.to_numpy(dtype=float),
            n_repeats=PERMUTATION_REPEATS,
            random_state=self.random_state,
        )
        return importance_pairs(self._feature_names, result.importances_mean)

    def _score_valid(self, valid: pd.DataFrame | None) -> dict[str, float]:
        if valid is None:
            return {}
        valid_features, valid_label = split_features_label(valid, self.label_column)
        return evaluate_ic(self.predict(valid_features), valid_label)

    # ── 推理 ──────────────────────────────────────────────────────

    def predict(self, features: pd.DataFrame) -> pd.Series:
        if self._model is None:
            raise ModelNotFittedError("GradientBoostingAlphaModel 尚未训练，先调用 fit()")
        aligned = align_features(features, self._feature_names)
        values = self._model.predict(aligned.to_numpy(dtype=float))
        return pd.Series(values, index=aligned.index, name=self.name)

    # ── 诊断 ──────────────────────────────────────────────────────

    def detail(self) -> dict[str, Any]:
        """置换重要度、逐轮训练损失曲线、超参与验证指标。"""
        if self._model is None:
            raise ModelNotFittedError("GradientBoostingAlphaModel 尚未训练，先调用 fit()")
        return {
            "model": self.name,
            "backend": "sklearn.HistGradientBoostingRegressor",
            "backend_note": "lightgbm 不在依赖清单，改用 sklearn 的直方图式 GBDT 实现",
            "hyperparams": {
                "learning_rate": self.learning_rate,
                "max_iter": self.max_iter,
                "max_leaf_nodes": self.max_leaf_nodes,
                "min_samples_leaf": self.min_samples_leaf,
                "l2_regularization": self.l2_regularization,
                "random_state": self.random_state,
                "early_stopping": self.early_stopping,
            },
            "feature_importance": list(self._importance),
            "train_loss_curve": self._train_loss_curve(),
            "train_curve_available": self.early_stopping,
            "train_curve_note": (
                "" if self.early_stopping else "sklearn 仅在 early_stopping=True 时记录逐轮训练分数"
            ),
            "n_iter": int(getattr(self._model, "n_iter_", 0)),
            "valid_metrics": dict(self._valid_metrics),
        }

    def _train_loss_curve(self) -> list[float]:
        """`train_score_` 是每轮的负损失，取反还原成损失曲线（关闭早停时为空）。"""
        scores = getattr(self._model, "train_score_", None)
        if scores is None or len(scores) == 0:
            return []
        return [round(-float(s), 6) for s in scores]
