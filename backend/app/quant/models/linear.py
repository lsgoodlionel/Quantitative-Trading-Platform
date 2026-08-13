"""
Lasso 因子模型（V4 · M5）

`AlphaModelTemplate` 的线性实现：标准化 + L1 正则回归。
L1 的价值在因子场景很直接 —— 它会把无效因子的系数压到 0，
`detail()` 里的 `selected_features` 就是一份自动筛出来的因子清单。

依赖：sklearn（`requirements.txt` 已有 `scikit-learn==1.8.0`），未新增依赖。
"""

from __future__ import annotations

from typing import Any

import numpy as np
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

# 默认 L1 强度：因子截面数据的量纲经标准化后，1e-3 量级既能稀疏化又不至于全压成 0
DEFAULT_ALPHA = 1e-3
DEFAULT_MAX_ITER = 5000
DEFAULT_RANDOM_STATE = 42
# 系数绝对值低于此阈值视为「被 L1 压掉」
ZERO_COEF_EPS = 1e-12


class LassoAlphaModel(AlphaModelTemplate):
    """L1 正则线性因子模型。"""

    name = "lasso"

    def __init__(
        self,
        alpha: float = DEFAULT_ALPHA,
        max_iter: int = DEFAULT_MAX_ITER,
        random_state: int = DEFAULT_RANDOM_STATE,
        label_column: str = DEFAULT_LABEL_COLUMN,
    ) -> None:
        if alpha <= 0:
            raise ValueError("alpha 必须 > 0（等于 0 就退化成普通最小二乘）")
        self.alpha = float(alpha)
        self.max_iter = int(max_iter)
        self.random_state = int(random_state)
        self.label_column = label_column
        self._pipeline: Any = None
        self._feature_names: list[str] = []
        self._valid_metrics: dict[str, float] = {}

    # ── 训练 ──────────────────────────────────────────────────────

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        from sklearn.linear_model import Lasso
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        features, label = split_features_label(train, self.label_column)
        self._feature_names = list(features.columns)
        self._pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "model",
                    Lasso(
                        alpha=self.alpha,
                        max_iter=self.max_iter,
                        random_state=self.random_state,
                    ),
                ),
            ]
        )
        self._pipeline.fit(features.to_numpy(dtype=float), label.to_numpy(dtype=float))
        self._valid_metrics = self._score_valid(valid)

    def _score_valid(self, valid: pd.DataFrame | None) -> dict[str, float]:
        """验证集 IC / RankIC；无验证集时返回空字典。"""
        if valid is None:
            return {}
        valid_features, valid_label = split_features_label(valid, self.label_column)
        return evaluate_ic(self.predict(valid_features), valid_label)

    # ── 推理 ──────────────────────────────────────────────────────

    def predict(self, features: pd.DataFrame) -> pd.Series:
        if self._pipeline is None:
            raise ModelNotFittedError("LassoAlphaModel 尚未训练，先调用 fit()")
        aligned = align_features(features, self._feature_names)
        values = self._pipeline.predict(aligned.to_numpy(dtype=float))
        return pd.Series(values, index=aligned.index, name=self.name)

    # ── 诊断 ──────────────────────────────────────────────────────

    def detail(self) -> dict[str, Any]:
        """系数（按 |coef| 降序）、被保留的因子清单、超参与验证指标。"""
        if self._pipeline is None:
            raise ModelNotFittedError("LassoAlphaModel 尚未训练，先调用 fit()")
        coefs = np.asarray(self._pipeline.named_steps["model"].coef_, dtype=float)
        return {
            "model": self.name,
            "hyperparams": {
                "alpha": self.alpha,
                "max_iter": self.max_iter,
                "random_state": self.random_state,
            },
            "feature_importance": importance_pairs(self._feature_names, np.abs(coefs)),
            "coefficients": [
                {"name": n, "coef": round(float(c), 8)}
                for n, c in zip(self._feature_names, coefs, strict=True)
            ],
            "selected_features": [
                n
                for n, c in zip(self._feature_names, coefs, strict=True)
                if abs(float(c)) > ZERO_COEF_EPS
            ],
            "intercept": round(float(self._pipeline.named_steps["model"].intercept_), 8),
            "valid_metrics": dict(self._valid_metrics),
        }
