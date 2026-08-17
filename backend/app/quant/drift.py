"""
特征分布漂移检测（V4 · M6）

纯计算模块，**不做任何 IO** —— 取数、入库、发通知都在 `app/quant/retrain.py`。

指标：DI（Dissimilarity Index）
------------------------------------------------------------------
思路一句话：先在训练集内部量出「样本之间通常隔多远」，再看新样本离训练集
最近的那一个有多远。远得离谱就是离群。

    scale     = mean(pairwise_distance(reference))      ← 训练集内部的尺度
    DI(x)     = min_j ||x - reference_j|| / scale       ← 新样本的相对距离
    outlier   = DI(x) > di_threshold
    is_drifting = outlier_ratio > outlier_ratio_threshold

⚠️ 许可证：本实现**独立编写**，只借用了「用训练集内部距离做尺度」这一公开思路。
未阅读、未移植 freqtrade（GPL-3.0）的任何代码。若日后有人要对照，
请对照本文件的公式，不要去粘贴那边的实现。

五条必须做对的（契约 §2.2）
------------------------------------------------------------------
1. **标准化用训练集的 mean/std**，绝不用新数据的。`FeatureBaseline` 里存的
   就是训练时的 mean/std，`detect_drift()` 全程只读它 —— 新数据只提供数值本身，
   一个统计量都不贡献。用新数据的统计量标准化，等于先把分布拉回标准正态、
   再问它有没有偏离标准正态，永远答「没有」。
2. **统计量随模型一起持久化**：`DriftAwareAlphaModel` 在 `fit()` 里自动建 baseline
   并作为自身属性，进 `LabStore` 时一并 pickle。调用方**没有机会忘记存**。
3. **两两距离 O(n²)**：参考集超过 `max_reference_rows` 时随机抽样（固定种子），
   `n_train_rows / sampled_n / is_sampled` 三个字段一律进报告 —— 抽了就说抽了。
4. **`is_drifting` 是启发式**：两个阈值与 `outlier_ratio` 原始值全都进报告，
   用户可以不同意这个判断，但必须看得见判断依据。
5. **本模块不触发任何动作**。它只回答「像不像漂了」，重不重训是人的决定。

数值边界
------------------------------------------------------------------
- 常数特征（std=0）：标准化时分母替换为 1.0，该列对距离的贡献恒为 0，不产生 NaN。
- 参考集完全退化（所有行相同 → scale=0）：`degenerate=True`，尺度回落到 1.0，
  此时 DI 就是标准化后的原始欧氏距离 —— 仍然能把「整体平移过的新数据」判出来。
- 新数据含 NaN 的行直接丢弃，并在报告里以 `n_dropped_rows` 计数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.quant.models.template import (
    DEFAULT_LABEL_COLUMN,
    AlphaModelTemplate,
    ModelNotFittedError,
    align_features,
    split_features_label,
)

# ── 默认阈值 ──────────────────────────────────────────────────────

#: 单样本判离群的 DI 阈值。「离训练集最近的那个点，比训练集内部平均间距还远一倍」
#: 才算离群 —— 取 1.0 会把训练集自身近一半的点判成离群（均值不是中位数）。
DEFAULT_DI_THRESHOLD = 2.0

#: 离群占比超过它才把 `is_drifting` 置 True。10% 是启发式，不是真理，
#: 所以它和 `outlier_ratio` 原始值都要出现在报告里。
DEFAULT_OUTLIER_RATIO_THRESHOLD = 0.10

#: 参与两两距离的参考样本上限。n=2000 时距离矩阵约 2000²×8B ≈ 32MB，
#: 分块计算后峰值远低于此。再大只是让 O(n²) 更慢，对尺度估计没有增量信息。
MAX_REFERENCE_ROWS = 2000

#: 抽样随机种子。固定值 → 同一份训练集永远抽到同一批参考行，
#: 否则「同样的数据两次判漂移结论不同」会把人逼疯。
DEFAULT_SAMPLE_SEED = 42

#: 分块大小：距离矩阵按行切块，峰值内存 ≈ CHUNK × n_reference × n_features × 8B
#: （128 × 2000 × 8 列 ≈ 16MB），与总样本量无关。
_DISTANCE_CHUNK_ROWS = 128

#: 小于它的尺度视为 0（退化）
_SCALE_EPS = 1e-12


class DriftError(ValueError):
    """漂移检测的输入不合法（列对不上、样本太少、全是 NaN）。"""


# ── 训练集分布快照 ────────────────────────────────────────────────

@dataclass(frozen=True)
class FeatureBaseline:
    """
    训练集特征分布快照 —— 判漂移时的**唯一**统计量来源。

    随模型一起进产物库（pickle）。下次判漂移用的必须是「那个模型训练时」
    的分布，而不是最近一次见到的数据。
    """

    feature_names: tuple[str, ...]
    #: 逐列均值 / 标准差（来自训练集**全量**行，不是抽样行）
    mean: tuple[float, ...]
    std: tuple[float, ...]
    #: 标准化后的参考矩阵（可能是抽样结果），形状 (sampled_n, n_features)。
    #: `compare=False`：ndarray 参与 dataclass 的 `__eq__` 会返回数组再炸在 bool() 上。
    reference: np.ndarray = field(repr=False, compare=False)
    #: 训练集清洗后的全量行数
    n_train_rows: int
    #: 实际参与两两距离计算的行数
    sampled_n: int
    #: 参考集内部的平均两两距离 —— DI 的分母
    scale: float
    #: 参考集完全退化（所有行相同）时为 True，此时 scale 回落到 1.0
    degenerate: bool
    sample_seed: int

    @property
    def is_sampled(self) -> bool:
        return self.sampled_n < self.n_train_rows

    def summary(self) -> dict[str, Any]:
        """产物 `detail()` 里展示的分布摘要（不含参考矩阵本身）。"""
        return {
            "feature_names": list(self.feature_names),
            "mean": [round(float(v), 8) for v in self.mean],
            "std": [round(float(v), 8) for v in self.std],
            "n_train_rows": self.n_train_rows,
            "sampled_n": self.sampled_n,
            "is_sampled": self.is_sampled,
            "sampling_note": (
                f"两两距离基于随机抽样的 {self.sampled_n} 行"
                f"（训练集共 {self.n_train_rows} 行，seed={self.sample_seed}）"
                if self.is_sampled
                else "两两距离基于训练集全量行，未抽样"
            ),
            "scale": round(float(self.scale), 8),
            "degenerate": self.degenerate,
        }


def build_baseline(
    train_features: pd.DataFrame,
    *,
    max_reference_rows: int = MAX_REFERENCE_ROWS,
    sample_seed: int = DEFAULT_SAMPLE_SEED,
) -> FeatureBaseline:
    """
    从训练集特征表建立分布快照。

    Parameters
    ----------
    train_features     : 只含特征列的训练集（不要带标签列）
    max_reference_rows : 参与两两距离的行数上限，超出则随机抽样
    sample_seed        : 抽样种子（固定 → 结果可复现）

    Raises
    ------
    DriftError : 输入不是 DataFrame、无特征列、清洗后不足 2 行
    """
    if not isinstance(train_features, pd.DataFrame):
        raise DriftError("train_features 必须是 pandas.DataFrame")
    if max_reference_rows < 2:
        raise DriftError(f"max_reference_rows 至少为 2，实得 {max_reference_rows}")

    numeric = _numeric_frame(train_features, "训练集")
    cleaned = numeric.dropna()
    if len(cleaned) < 2:
        raise DriftError(f"训练集清洗后不足 2 行（实得 {len(cleaned)}），无法估计分布尺度")

    values = cleaned.to_numpy(dtype=float)
    mean = values.mean(axis=0)
    std = values.std(axis=0, ddof=0)
    standardized = (values - mean) / _safe_std(std)

    reference, sampled_n = _subsample(standardized, max_reference_rows, sample_seed)
    raw_scale = _mean_pairwise_distance(reference)
    degenerate = raw_scale <= _SCALE_EPS

    return FeatureBaseline(
        feature_names=tuple(str(c) for c in cleaned.columns),
        mean=tuple(float(v) for v in mean),
        std=tuple(float(v) for v in std),
        reference=reference,
        n_train_rows=int(len(cleaned)),
        sampled_n=int(sampled_n),
        # 退化时用 1.0 而不是 0：分母为 0 会让整份报告变成 NaN，
        # 而 1.0 让 DI 退化为「标准化后的原始距离」，平移仍然检得出来。
        scale=1.0 if degenerate else float(raw_scale),
        degenerate=bool(degenerate),
        sample_seed=int(sample_seed),
    )


# ── 漂移报告 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class DriftReport:
    """
    一次漂移检测的完整结论 + 全部判断依据。

    契约 §2.1 列的字段是最小集；这里额外带上抽样口径（`sampled_n` 等，
    契约 §4.2 的验收项要求它出现在报告里）、离群占比阈值与退化标记 ——
    少了它们，`is_drifting` 就成了一个无法复核的黑箱结论。
    """

    n_samples: int
    n_outliers: int
    outlier_ratio: float
    di_mean: float
    di_p95: float
    di_max: float
    #: 单样本判离群的 DI 阈值
    threshold: float
    is_drifting: bool
    #: 判 `is_drifting` 用的离群占比阈值
    outlier_ratio_threshold: float
    #: 训练集全量行数 / 实际参与两两距离的行数
    n_train_rows: int
    sampled_n: int
    is_sampled: bool
    #: 新数据里因含 NaN 被丢弃的行数
    n_dropped_rows: int
    #: 参考集退化（所有训练行相同），DI 退化为原始欧氏距离
    degenerate: bool
    feature_names: tuple[str, ...]

    @property
    def sampling_note(self) -> str:
        if not self.is_sampled:
            return f"两两距离基于训练集全量 {self.n_train_rows} 行，未抽样"
        return (
            f"两两距离基于随机抽样的 {self.sampled_n} 行"
            f"（训练集共 {self.n_train_rows} 行）—— 尺度是估计值"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "n_outliers": self.n_outliers,
            "outlier_ratio": round(self.outlier_ratio, 6),
            "di_mean": round(self.di_mean, 6),
            "di_p95": round(self.di_p95, 6),
            "di_max": round(self.di_max, 6),
            "threshold": round(self.threshold, 6),
            "outlier_ratio_threshold": round(self.outlier_ratio_threshold, 6),
            "is_drifting": self.is_drifting,
            "n_train_rows": self.n_train_rows,
            "sampled_n": self.sampled_n,
            "is_sampled": self.is_sampled,
            "sampling_note": self.sampling_note,
            "n_dropped_rows": self.n_dropped_rows,
            "degenerate": self.degenerate,
            "feature_names": list(self.feature_names),
            "verdict_note": (
                "is_drifting 是启发式判断，不是判决：它只表示离群占比越过了阈值。"
                "请对照 outlier_ratio 与两个阈值自行判断是否需要处置。"
            ),
        }


def detect_drift(
    baseline: FeatureBaseline,
    new_features: pd.DataFrame,
    *,
    di_threshold: float = DEFAULT_DI_THRESHOLD,
    outlier_ratio_threshold: float = DEFAULT_OUTLIER_RATIO_THRESHOLD,
) -> DriftReport:
    """
    用 `baseline`（训练时的分布）判断 `new_features` 是否已经漂了。

    ⚠️ 标准化全程只用 `baseline.mean / baseline.std`。这里**不允许**出现任何
    从 `new_features` 计算的统计量 —— 那会把要检测的漂移本身抹掉。

    Raises
    ------
    DriftError : 列对不上、清洗后没有任何可用行、阈值非法
    """
    if di_threshold <= 0:
        raise DriftError(f"di_threshold 必须 > 0，实得 {di_threshold}")
    if not 0 < outlier_ratio_threshold <= 1:
        raise DriftError(f"outlier_ratio_threshold 必须落在 (0, 1]，实得 {outlier_ratio_threshold}")
    if not isinstance(new_features, pd.DataFrame):
        raise DriftError("new_features 必须是 pandas.DataFrame")

    try:
        aligned = align_features(new_features, list(baseline.feature_names))
    except (TypeError, ValueError) as exc:
        # 统一成 DriftError：调用方只需要 catch 一种异常，
        # 且「新数据缺了训练时的列」本质上就是一次输入不合法。
        raise DriftError(f"新数据与训练时的特征列对不上: {exc}") from exc
    numeric = _numeric_frame(aligned, "新数据")
    cleaned = numeric.dropna()
    n_dropped = int(len(numeric) - len(cleaned))
    if cleaned.empty:
        raise DriftError("新数据清洗后为空（全部行含 NaN），无法判断漂移")

    # ↓↓↓ 训练集的 mean/std，不是 cleaned 的 ↓↓↓
    mean = np.asarray(baseline.mean, dtype=float)
    std = _safe_std(np.asarray(baseline.std, dtype=float))
    standardized = (cleaned.to_numpy(dtype=float) - mean) / std

    nearest = _nearest_distances(standardized, baseline.reference)
    di = nearest / baseline.scale

    n_outliers = int(np.count_nonzero(di > di_threshold))
    outlier_ratio = float(n_outliers / len(di))

    return DriftReport(
        n_samples=int(len(di)),
        n_outliers=n_outliers,
        outlier_ratio=outlier_ratio,
        di_mean=float(np.mean(di)),
        di_p95=float(np.percentile(di, 95)),
        di_max=float(np.max(di)),
        threshold=float(di_threshold),
        is_drifting=bool(outlier_ratio > outlier_ratio_threshold),
        outlier_ratio_threshold=float(outlier_ratio_threshold),
        n_train_rows=baseline.n_train_rows,
        sampled_n=baseline.sampled_n,
        is_sampled=baseline.is_sampled,
        n_dropped_rows=n_dropped,
        degenerate=baseline.degenerate,
        feature_names=baseline.feature_names,
    )


# ── 带分布快照的模型包装 ──────────────────────────────────────────

class DriftAwareAlphaModel(AlphaModelTemplate):
    """
    给任意 `AlphaModelTemplate` 挂上训练集分布快照的包装。

    存在的理由只有一个：**让「忘记持久化训练集统计量」变成不可能**。
    `fit()` 里建 baseline 是包装自己的事，调用方绕不过去；实例进 `LabStore`
    时 baseline 跟着 pickle，加载回来就能直接 `check_drift()`。

    `predict()` 原样转发给内层模型，逐值等同于直接调用它 —— 包装不改变预测。
    """

    def __init__(
        self,
        model: AlphaModelTemplate,
        *,
        max_reference_rows: int = MAX_REFERENCE_ROWS,
        sample_seed: int = DEFAULT_SAMPLE_SEED,
    ) -> None:
        if not isinstance(model, AlphaModelTemplate):
            raise TypeError("model 必须是 AlphaModelTemplate 的子类实例")
        self._model = model
        self._max_reference_rows = int(max_reference_rows)
        self._sample_seed = int(sample_seed)
        self._baseline: FeatureBaseline | None = None
        self.label_column = getattr(model, "label_column", DEFAULT_LABEL_COLUMN)

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"drift_aware:{self._model.name}"

    @property
    def inner(self) -> AlphaModelTemplate:
        """被包装的模型 —— 需要原始 `detail()` 或类型判断时用。"""
        return self._model

    @property
    def baseline(self) -> FeatureBaseline:
        if self._baseline is None:
            raise ModelNotFittedError("模型尚未训练，还没有训练集分布快照")
        return self._baseline

    # ── 三件套 ────────────────────────────────────────────────────

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None:
        """先训内层模型，再用**同一份**训练特征建分布快照。"""
        self._model.fit(train, valid)
        features, _ = split_features_label(train, self.label_column)
        self._baseline = build_baseline(
            features,
            max_reference_rows=self._max_reference_rows,
            sample_seed=self._sample_seed,
        )

    def predict(self, features: pd.DataFrame) -> pd.Series:
        return self._model.predict(features)

    def detail(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "inner": self._model.detail(),
            "drift_baseline": self.baseline.summary(),
        }

    # ── 漂移 ──────────────────────────────────────────────────────

    def check_drift(
        self,
        new_features: pd.DataFrame,
        *,
        di_threshold: float = DEFAULT_DI_THRESHOLD,
        outlier_ratio_threshold: float = DEFAULT_OUTLIER_RATIO_THRESHOLD,
    ) -> DriftReport:
        """
        用**本模型训练时**的分布判断新特征是否漂了。

        ⚠️ 只回答「像不像漂了」。**不重训、不下线、不改任何状态** —— 见模块 docstring。
        """
        return detect_drift(
            self.baseline,
            new_features,
            di_threshold=di_threshold,
            outlier_ratio_threshold=outlier_ratio_threshold,
        )


# ── 内部数值工具 ──────────────────────────────────────────────────

def _numeric_frame(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """校验并返回数值型特征表。非数值列直接报错，而不是静默变成 NaN 全行丢弃。"""
    if frame.shape[1] == 0:
        raise DriftError(f"{label}没有任何特征列")
    non_numeric = [
        str(c) for c in frame.columns if not pd.api.types.is_numeric_dtype(frame[c])
    ]
    if non_numeric:
        raise DriftError(f"{label}含非数值特征列，无法计算距离: {non_numeric}")
    return frame


def _safe_std(std: np.ndarray) -> np.ndarray:
    """
    std=0 的常数列把分母换成 1.0。

    该列标准化后恒为 (x - mean)，训练集内恒为 0；新数据若在该列上偏移了，
    偏移量会原样进入距离 —— 「常数特征突然变了」正该被判成漂移。
    """
    safe = np.asarray(std, dtype=float).copy()
    safe[~np.isfinite(safe) | (safe <= _SCALE_EPS)] = 1.0
    return safe


def _subsample(values: np.ndarray, max_rows: int, seed: int) -> tuple[np.ndarray, int]:
    """行数超上限时无放回随机抽样。返回 (参考矩阵, 实际行数)。"""
    n_rows = int(values.shape[0])
    if n_rows <= max_rows:
        return values, n_rows
    rng = np.random.default_rng(seed)
    picked = np.sort(rng.choice(n_rows, size=max_rows, replace=False))
    return values[picked], max_rows


def _mean_pairwise_distance(reference: np.ndarray) -> float:
    """
    参考集内部的平均两两欧氏距离（上三角，不含对角线）。

    分块计算：一次只物化 `_DISTANCE_CHUNK_ROWS × n` 的距离块，
    峰值内存与 n 线性相关而非平方 —— 上万行的参考集直接算会炸内存。
    """
    n_rows = int(reference.shape[0])
    if n_rows < 2:
        return 0.0

    total = 0.0
    count = 0
    for start in range(0, n_rows, _DISTANCE_CHUNK_ROWS):
        stop = min(start + _DISTANCE_CHUNK_ROWS, n_rows)
        # 只与「自己之后」的行配对，天然得到上三角，不做重复计算
        block = _pairwise_block(reference[start:stop], reference[start:])
        rows = stop - start
        upper = np.triu_indices(rows, k=1)
        total += float(block[:, :rows][upper].sum())
        count += len(upper[0])
        if stop < n_rows:
            tail = block[:, rows:]
            total += float(tail.sum())
            count += tail.size
    return total / count if count else 0.0


def _nearest_distances(query: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """每个 query 行到 reference 的最近欧氏距离。同样分块，内存与 query 行数无关。"""
    if reference.shape[0] == 0:
        raise DriftError("分布快照的参考集为空，无法计算距离")
    out = np.empty(query.shape[0], dtype=float)
    for start in range(0, query.shape[0], _DISTANCE_CHUNK_ROWS):
        stop = min(start + _DISTANCE_CHUNK_ROWS, query.shape[0])
        out[start:stop] = _pairwise_block(query[start:stop], reference).min(axis=1)
    return out


def _pairwise_block(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    (m, d) × (n, d) → (m, n) 欧氏距离块。

    刻意用广播差值而不是 `||a||² + ||b||² - 2ab` 展开式：后者省一点内存，
    但两个大数相减会在距离接近 0 时丢掉全部有效位，甚至给出负数再开根出 NaN。
    这里的 m 已经被分块限制住，广播的额外内存是可控的。
    """
    diff = left[:, None, :] - right[None, :, :]
    return np.sqrt(np.einsum("mnd,mnd->mn", diff, diff))
