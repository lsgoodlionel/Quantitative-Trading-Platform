"""
统一 ML 模型模板（V4 · M5）

接口对齐 vnpy `alpha/model/template.py`（MIT）：**fit / predict / detail 三件套**。
在此之前项目里有三套各自为政的 ML 实现（`ml_strategy.py` / `double_ensemble.py` /
`models/sequence.py`），接口不一致导致无法横向对比、新增模型要重写一遍胶水。

约定
------------------------------------------------------------------
- `fit(train, valid=None)`：`train` 是一张宽表，含特征列与一列标签
  （列名由 `label_column` 指定，默认 `"label"`）。`valid` 同构，可选，
  给需要早停 / 验证指标的实现用。
- `predict(features)`：只含特征列的 DataFrame，返回与其 index 对齐的 `pd.Series`。
- `detail()`：特征重要性 / 训练曲线 / 超参，供前端展示，默认空字典。

三个既有实现由 `legacy_adapters.py` 的适配层收编，**不重写它们的算法**
（理由同 Wave K-d 的 `LegacyStrategyAlphaAdapter`）。适配层的验收标准是
「包装后的预测值与直接调用原实现逐值相等」，由 `tests/test_model_template.py` 守住。

模型实例可直接存进投研产物库（`app/quant/lab`，`kind=MODEL`），
加载回来即可 `predict` —— 端到端链路见同一测试文件。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

# 标签列的默认列名
DEFAULT_LABEL_COLUMN = "label"


class ModelNotFittedError(RuntimeError):
    """在 `fit()` 之前调用 `predict()` / `detail()`。"""


class AlphaModelTemplate(ABC):
    """统一的因子模型接口。fit / predict / detail 三件套。"""

    #: 模型标识，落产物库时作为默认标签
    name: str = "alpha_model"
    #: 训练集里标签列的列名
    label_column: str = DEFAULT_LABEL_COLUMN

    @abstractmethod
    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        """在 `train` 上训练；`valid` 供早停或验证指标使用，可为 None。"""

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> pd.Series:
        """对 `features` 逐行打分，返回与其 index 对齐的预测值。"""

    def detail(self) -> dict[str, Any]:
        """特征重要性 / 训练曲线 / 超参 —— 供前端展示，默认返回空。"""
        return {}


# ── 通用工具 ──────────────────────────────────────────────────────

def split_features_label(
    frame: pd.DataFrame, label_column: str = DEFAULT_LABEL_COLUMN
) -> tuple[pd.DataFrame, pd.Series]:
    """
    把宽表拆成 (特征, 标签)，顺带做边界校验。

    在系统边界快速失败，比让 sklearn 抛一句看不懂的形状错误强。
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("训练集必须是 pandas.DataFrame")
    if label_column not in frame.columns:
        raise ValueError(f"训练集缺少标签列 '{label_column}'")

    cleaned = frame.dropna()
    if cleaned.empty:
        raise ValueError("训练集清洗后为空（全部行含 NaN）")

    features = cleaned.drop(columns=[label_column])
    if features.shape[1] == 0:
        raise ValueError("训练集除标签列外没有任何特征列")
    return features, cleaned[label_column]


def align_features(features: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    """按训练时的列顺序取特征列；缺列直接报错，避免静默错位。"""
    if not isinstance(features, pd.DataFrame):
        raise TypeError("特征必须是 pandas.DataFrame")
    missing = [c for c in feature_names if c not in features.columns]
    if missing:
        raise ValueError(f"特征缺少训练时使用的列: {missing}")
    return features[feature_names]


def evaluate_ic(prediction: pd.Series, label: pd.Series) -> dict[str, float]:
    """
    验证集打分：Pearson IC 与 Spearman RankIC。

    因子模型的验证指标看 IC 比看 R² 有意义 —— 预测值的绝对尺度不重要，
    与未来收益的排序相关性才是。样本不足或方差为 0 时返回 0.0 而非 NaN，
    避免把 NaN 一路带进 JSON 响应。
    """
    aligned = pd.concat([prediction.rename("pred"), label.rename("label")], axis=1).dropna()
    if len(aligned) < 2 or aligned["pred"].std() == 0 or aligned["label"].std() == 0:
        return {"ic": 0.0, "rank_ic": 0.0, "n_samples": int(len(aligned))}
    return {
        "ic": round(float(aligned["pred"].corr(aligned["label"])), 6),
        "rank_ic": round(float(aligned["pred"].corr(aligned["label"], method="spearman")), 6),
        "n_samples": int(len(aligned)),
    }


def importance_pairs(names: list[str], values: Any) -> list[dict[str, Any]]:
    """把 (列名, 重要度) 组装成降序的 `[{name, importance}]`，对齐既有三套的结构。"""
    pairs = sorted(
        zip(names, (float(v) for v in values), strict=True),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return [{"name": n, "importance": round(v, 6)} for n, v in pairs]
