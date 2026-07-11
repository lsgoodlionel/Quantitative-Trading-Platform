"""
DoubleEnsemble 集成模型 (Wave-3 / B6)

在现有 sklearn GradientBoosting 基础上，套一层 qlib DoubleEnsemble 的两大机制：

  1. 样本重加权 (Sample Re-weighting, SR)
     迭代放大"学得慢 / 难拟合"样本的权重 —— 用子模型 boosting 过程的逐轮损失
     曲线（前段 vs 后段）判断哪些样本收敛慢，配合当前集成损失重新分箱加权。

  2. 特征筛选 (Feature Selection, FS)
     跨子模型做置换检验：逐列 shuffle 特征、观测集成损失变化 g-value，按分箱
     采样保留"重要且稳定"的特征，剔除噪声特征，降低子模型相关性。

任务为二分类（预测 n 日后涨跌，与 ml_strategy 对齐），输出结构为
MLTrainResult 的超集（额外附带集成诊断），可作为 ml/train 的一个 model_type。

参考算法（只读，未复制代码）：
  refs/qlib/qlib/contrib/model/double_ensemble.py :: DEnsembleModel
差异：参考用 lightgbm + mse 回归；此处改用 sklearn GradientBoostingClassifier +
Brier 损失 (y - p)^2，用 staged_predict_proba 获取逐轮损失曲线。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.ml_strategy import (
    FEATURE_NAMES,
    _build_features,
    _evaluate_clf,
    _latest_signal,
)

# 每箱采样比例（长度须等于 bins_fs），从"最重要箱"到"最不重要箱"递减
DEFAULT_SAMPLE_RATIOS: tuple[float, ...] = (0.8, 0.7, 0.6, 0.5, 0.4)

# 训练该模型的最少干净样本数
MIN_SAMPLES = 100


# ── 配置 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DoubleEnsembleConfig:
    num_models:     int              = 4
    enable_sr:      bool             = True
    enable_fs:      bool             = True
    alpha1:         float            = 1.0
    alpha2:         float            = 1.0
    bins_sr:        int              = 10
    bins_fs:        int              = 5
    decay:          float            = 0.5
    sample_ratios:  tuple[float, ...] = DEFAULT_SAMPLE_RATIOS
    n_estimators:   int              = 100
    max_depth:      int              = 3
    learning_rate:  float            = 0.1
    random_state:   int              = 42

    def __post_init__(self) -> None:
        if self.num_models < 1:
            raise ValueError("num_models 必须 >= 1")
        if len(self.sample_ratios) != self.bins_fs:
            raise ValueError(
                f"sample_ratios 长度({len(self.sample_ratios)}) 必须等于 bins_fs({self.bins_fs})"
            )


# ── 集成分类器 ────────────────────────────────────────────────────

class DoubleEnsembleClassifier:
    """样本重加权 + 特征筛选包装的 GradientBoosting 集成分类器。"""

    def __init__(self, config: DoubleEnsembleConfig | None = None) -> None:
        self.config: DoubleEnsembleConfig = config or DoubleEnsembleConfig()
        self.scaler = None
        self.submodels: list = []
        self.sub_features: list[np.ndarray] = []   # 每个子模型使用的特征列索引
        self.sub_weights: list[float] = []
        self.feature_names: list[str] = []

    # -- 训练 --------------------------------------------------------

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names=None) -> "DoubleEnsembleClassifier":
        from sklearn.preprocessing import StandardScaler

        cfg = self.config
        self.feature_names = list(feature_names) if feature_names is not None else \
            [f"f{i}" for i in range(X.shape[1])]
        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        n_samples, n_feats = Xs.shape

        weights = np.ones(n_samples)
        features = np.arange(n_feats)
        pred_sub = np.zeros((n_samples, cfg.num_models))

        for k in range(cfg.num_models):
            self.sub_features.append(features.copy())
            self.sub_weights.append(1.0)
            model = self._train_submodel(Xs, y, weights, features)
            self.submodels.append(model)
            if k + 1 == cfg.num_models:
                break

            loss_curve = self._retrieve_loss_curve(model, Xs[:, features], y)
            pred_sub[:, k] = model.predict_proba(Xs[:, features])[:, 1]
            w = np.asarray(self.sub_weights)
            pred_ens = (pred_sub[:, : k + 1] * w).sum(axis=1) / w.sum()
            loss_values = self._loss(y, pred_ens)

            if cfg.enable_sr:
                weights = self._sample_reweight(loss_curve, loss_values, k + 1)
            if cfg.enable_fs:
                features = self._feature_selection(Xs, y, loss_values)
        return self

    def _train_submodel(self, Xs, y, weights, features):
        from sklearn.ensemble import GradientBoostingClassifier

        cfg = self.config
        model = GradientBoostingClassifier(
            n_estimators=cfg.n_estimators,
            max_depth=cfg.max_depth,
            learning_rate=cfg.learning_rate,
            random_state=cfg.random_state,
        )
        model.fit(Xs[:, features], y, sample_weight=weights)
        return model

    # -- SR / FS 内核 ------------------------------------------------

    @staticmethod
    def _loss(y: np.ndarray, prob: np.ndarray) -> np.ndarray:
        """逐样本 Brier 损失 (y - p)^2。"""
        return (y.astype(float) - prob) ** 2

    def _retrieve_loss_curve(self, model, x_sub: np.ndarray, y: np.ndarray) -> pd.DataFrame:
        """用 boosting 逐轮预测还原 N×T 损失曲线。"""
        stages = list(model.staged_predict_proba(x_sub))
        curve = np.empty((len(y), len(stages)))
        for t, proba in enumerate(stages):
            curve[:, t] = self._loss(y, proba[:, 1])
        return pd.DataFrame(curve)

    def _sample_reweight(self, loss_curve: pd.DataFrame, loss_values: np.ndarray, k_th: int) -> np.ndarray:
        cfg = self.config
        loss_curve_norm = loss_curve.rank(axis=0, pct=True)
        loss_values_norm = pd.Series(-loss_values).rank(pct=True).to_numpy()

        n_samples, n_trees = loss_curve.shape
        part = max(int(n_trees * 0.1), 1)
        l_start = loss_curve_norm.iloc[:, :part].mean(axis=1)
        l_end = loss_curve_norm.iloc[:, -part:].mean(axis=1)

        h2 = (l_end / l_start.replace(0, np.nan)).rank(pct=True).to_numpy()
        h_value = cfg.alpha1 * loss_values_norm + cfg.alpha2 * np.nan_to_num(h2)

        h = pd.DataFrame({"h_value": h_value})
        h["bins"] = pd.cut(h["h_value"], cfg.bins_sr)
        h_avg = h.groupby("bins", observed=False)["h_value"].mean()

        weights = np.zeros(n_samples)
        for b in h_avg.index:
            if pd.isna(h_avg[b]):
                continue
            mask = (h["bins"] == b).to_numpy()
            weights[mask] = 1.0 / (cfg.decay ** k_th * float(h_avg[b]) + 0.1)

        positive = weights[weights > 0]
        fallback = positive.mean() if positive.size else 1.0
        weights[weights == 0] = fallback
        return weights

    def _feature_selection(self, Xs: np.ndarray, y: np.ndarray, loss_values: np.ndarray) -> np.ndarray:
        cfg = self.config
        n_samples, n_feats = Xs.shape
        rng = np.random.default_rng(cfg.random_state)
        g = np.zeros(n_feats)

        for i_f in range(n_feats):
            x_tmp = Xs.copy()
            x_tmp[:, i_f] = rng.permutation(Xs[:, i_f])
            pred = self._ensemble_proba(x_tmp)
            diff = self._loss(y, pred) - loss_values
            g[i_f] = diff.mean() / (diff.std() + 1e-7)

        g = np.nan_to_num(g)
        selected = self._sample_features_by_bins(g, rng)
        return selected if selected.size else np.arange(n_feats)

    def _sample_features_by_bins(self, g: np.ndarray, rng) -> np.ndarray:
        cfg = self.config
        if np.ptp(g) < 1e-12:                       # 全部 g 相等 → 保留全部
            return np.arange(len(g))
        bins = pd.cut(pd.Series(g), cfg.bins_fs)
        chosen: list[int] = []
        # 类别默认升序，最重要（g 大）在末尾 → 逆序对齐 sample_ratios
        for i_b, cat in enumerate(reversed(list(bins.cat.categories))):
            idx = np.where((bins == cat).to_numpy())[0]
            if idx.size == 0:
                continue
            ratio = cfg.sample_ratios[min(i_b, len(cfg.sample_ratios) - 1)]
            n_keep = int(np.ceil(ratio * idx.size))
            if n_keep > 0:
                chosen.extend(rng.choice(idx, size=min(n_keep, idx.size), replace=False).tolist())
        return np.array(sorted(set(chosen)), dtype=int)

    # -- 推理 --------------------------------------------------------

    def _ensemble_proba(self, Xs: np.ndarray) -> np.ndarray:
        """在已缩放矩阵上做集成正类概率（内部用）。"""
        pred = np.zeros(Xs.shape[0])
        for i_s, model in enumerate(self.submodels):
            feats = self.sub_features[i_s]
            pred += model.predict_proba(Xs[:, feats])[:, 1] * self.sub_weights[i_s]
        return pred / float(np.sum(self.sub_weights))

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Xs = self.scaler.transform(X)
        p1 = self._ensemble_proba(Xs)
        return np.column_stack([1.0 - p1, p1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    # -- 诊断 --------------------------------------------------------

    def aggregated_importance(self) -> list[dict]:
        """跨子模型加权聚合特征重要度，按降序排列。"""
        total = np.zeros(len(self.feature_names))
        for i_s, model in enumerate(self.submodels):
            feats = self.sub_features[i_s]
            total[feats] += model.feature_importances_ * self.sub_weights[i_s]
        w_sum = float(np.sum(self.sub_weights)) or 1.0
        total = total / w_sum
        pairs = sorted(zip(self.feature_names, total), key=lambda kv: kv[1], reverse=True)
        return [{"name": n, "importance": round(float(v), 6)} for n, v in pairs]

    def feature_usage(self) -> list[dict]:
        """每个特征被多少个子模型采用（FS 稳定性视图）。"""
        counts = np.zeros(len(self.feature_names), dtype=int)
        for feats in self.sub_features:
            counts[feats] += 1
        return [
            {"name": n, "used_by": int(c)}
            for n, c in sorted(
                zip(self.feature_names, counts), key=lambda kv: kv[1], reverse=True
            )
        ]


# ── 结果类 ────────────────────────────────────────────────────────

@dataclass
class DoubleEnsembleResult:
    model_type:         str
    forward_days:       int
    n_samples:          int
    n_features:         int
    feature_names:      list[str]

    train_accuracy:     float
    test_accuracy:      float
    precision:          float
    recall:             float
    f1_score:           float
    auc_roc:            float

    feature_importance: list[dict]
    confusion_matrix:   list[list[int]]
    predictions:        list[dict]

    recent_signal:      str
    recent_prob:        float

    cv_mean:            float
    cv_std:             float

    # DoubleEnsemble 专属诊断
    num_models:         int
    enable_sr:          bool
    enable_fs:          bool
    sub_feature_counts: list[int]        # 每个子模型使用的特征数
    feature_usage:      list[dict]       # [{name, used_by}]


# ── 交叉验证 ──────────────────────────────────────────────────────

def _cross_validate(X: np.ndarray, y: np.ndarray, config: DoubleEnsembleConfig, n_splits: int = 3):
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import accuracy_score

    scores: list[float] = []
    for train_idx, test_idx in TimeSeriesSplit(n_splits=n_splits).split(X):
        clf = DoubleEnsembleClassifier(config).fit(
            X[train_idx], y[train_idx], FEATURE_NAMES
        )
        scores.append(float(accuracy_score(y[test_idx], clf.predict(X[test_idx]))))
    return round(float(np.mean(scores)), 4), round(float(np.std(scores)), 4)


# ── 训练入口 ──────────────────────────────────────────────────────

def train_double_ensemble(
    df: pd.DataFrame,
    forward_days: int = 5,
    test_size: float = 0.2,
    config: DoubleEnsembleConfig | None = None,
) -> DoubleEnsembleResult:
    """
    训练 DoubleEnsemble 集成分类模型。

    与 ml_strategy.train_ml_strategy 完全同构（特征 / 目标 / 时序切分 / 指标），
    区别仅在于分类器换成带样本重加权 + 特征筛选的集成包装。
    """
    cfg = config or DoubleEnsembleConfig()

    X = _build_features(df)
    close = df["close"]
    fwd_ret = close.pct_change(forward_days).shift(-forward_days)
    y = (fwd_ret > 0).astype(int)

    combined = pd.concat([X, y.rename("target")], axis=1).dropna()
    if len(combined) < MIN_SAMPLES:
        raise ValueError(f"Not enough clean samples: {len(combined)} (need >= {MIN_SAMPLES})")

    X_clean = combined[FEATURE_NAMES].values
    y_clean = combined["target"].values
    times = combined.index.tolist()

    n_test = max(int(len(X_clean) * test_size), 30)
    n_train = len(X_clean) - n_test
    X_train, X_test = X_clean[:n_train], X_clean[n_train:]
    y_train, y_test = y_clean[:n_train], y_clean[n_train:]

    clf = DoubleEnsembleClassifier(cfg).fit(X_train, y_train, FEATURE_NAMES)
    metrics = _evaluate_clf(clf, X_train, y_train, X_test, y_test)
    cv_mean, cv_std = _cross_validate(X_train, y_train, cfg)

    predictions = _recent_predictions(clf, X_test, y_test, times, n_train)
    signal, latest_prob = _latest_signal(clf, X_clean)

    return DoubleEnsembleResult(
        model_type="double_ensemble",
        forward_days=forward_days,
        n_samples=len(combined),
        n_features=len(FEATURE_NAMES),
        feature_names=FEATURE_NAMES,
        train_accuracy=metrics["train_accuracy"],
        test_accuracy=metrics["test_accuracy"],
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1_score=metrics["f1_score"],
        auc_roc=metrics["auc_roc"],
        feature_importance=clf.aggregated_importance(),
        confusion_matrix=metrics["confusion_matrix"],
        predictions=predictions,
        recent_signal=signal,
        recent_prob=round(latest_prob, 4),
        cv_mean=cv_mean,
        cv_std=cv_std,
        num_models=cfg.num_models,
        enable_sr=cfg.enable_sr,
        enable_fs=cfg.enable_fs,
        sub_feature_counts=[int(len(f)) for f in clf.sub_features],
        feature_usage=clf.feature_usage(),
    )


def _recent_predictions(clf, X_test, y_test, times, n_train, n_recent: int = 30) -> list[dict]:
    n = min(n_recent, len(X_test))
    if n == 0:
        return []
    pred = clf.predict(X_test[-n:])
    prob = clf.predict_proba(X_test[-n:])[:, 1]
    recent_times = times[n_train:][-n:]
    recent_y = y_test[-n:]
    return [
        {"time": str(t), "actual": int(a), "predicted": int(p), "probability": round(float(pr), 4)}
        for t, a, p, pr in zip(recent_times, recent_y, pred, prob)
    ]
