"""
ML 策略训练模块

基于技术指标特征，训练机器学习分类模型预测短期价格方向。

支持模型:
  - logistic_regression (逻辑回归，基线)
  - random_forest       (随机森林)
  - gradient_boosting   (梯度提升)

特征集:
  - RSI(14)
  - MACD 柱状图
  - 布林带位置 (0=下轨, 1=上轨)
  - 5日/20日动量
  - ATR 波动率比
  - 成交量变化率
  - SMA 偏离度 (20日)

目标变量:
  - 1: n日后收益率 > 0 (看涨信号)
  - 0: n日后收益率 <= 0 (看跌/持平信号)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

ModelType = Literal["logistic_regression", "random_forest", "gradient_boosting"]


# ── 特征工程 ──────────────────────────────────────────────────────

FEATURE_NAMES = [
    "rsi_14",
    "macd_hist",
    "bb_position",
    "momentum_5",
    "momentum_20",
    "atr_ratio",
    "volume_change",
    "price_to_sma20",
]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """从 OHLCV DataFrame 构建特征矩阵。"""
    from app.quant.indicators import (
        rsi, macd, bollinger_bands, atr, sma, ema,
    )

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    vol    = df["volume"]

    features = pd.DataFrame(index=df.index)

    # RSI
    features["rsi_14"] = rsi(df, 14)

    # MACD 柱
    _, _, hist = macd(df)
    features["macd_hist"] = hist

    # 布林带位置
    upper, _, lower = bollinger_bands(df, 20)
    band_width = (upper - lower).replace(0, np.nan)
    features["bb_position"] = (close - lower) / band_width

    # 动量
    features["momentum_5"]  = close.pct_change(5)
    features["momentum_20"] = close.pct_change(20)

    # ATR 波动率比
    atr_val = atr(df, 14)
    features["atr_ratio"] = atr_val / close.replace(0, np.nan)

    # 成交量变化率
    avg_vol = vol.rolling(20).mean()
    features["volume_change"] = vol / avg_vol.replace(0, np.nan) - 1

    # SMA 偏离度
    ma20 = sma(df, 20)
    features["price_to_sma20"] = close / ma20.replace(0, np.nan) - 1

    return features


# ── 结果类 ────────────────────────────────────────────────────────

@dataclass
class MLTrainResult:
    model_type:          str
    forward_days:        int
    n_samples:           int
    n_features:          int
    feature_names:       list[str]

    # Performance metrics
    train_accuracy:      float
    test_accuracy:       float
    precision:           float
    recall:              float
    f1_score:            float
    auc_roc:             float

    # Feature importance (sorted descending)
    feature_importance:  list[dict]  # [{name, importance}]

    # Confusion matrix [[TN, FP], [FN, TP]]
    confusion_matrix:    list[list[int]]

    # Recent predictions (last 30 bars)
    predictions:         list[dict]  # [{time, actual, predicted, probability}]

    # Signal summary
    recent_signal:       str   # "BUY" | "SELL" | "NEUTRAL"
    recent_prob:         float

    # Cross-val score
    cv_mean:             float
    cv_std:              float


# ── Private helpers ───────────────────────────────────────────────

def _build_pipeline(model_type: ModelType):
    """返回 StandardScaler + 分类器的 sklearn Pipeline。"""
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    classifiers = {
        "logistic_regression": LogisticRegression(max_iter=500, random_state=42, C=1.0),
        "random_forest": RandomForestClassifier(
            n_estimators=100, max_depth=5, min_samples_leaf=5,
            random_state=42, n_jobs=-1,
        ),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42,
        ),
    }
    if model_type not in classifiers:
        raise ValueError(f"Unknown model type: {model_type}")

    return Pipeline([("scaler", StandardScaler()), ("model", classifiers[model_type])])


def _evaluate_clf(clf, X_train, y_train, X_test, y_test) -> dict:
    """计算全套评估指标，返回字典。"""
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, roc_auc_score, confusion_matrix,
    )

    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    return {
        "train_accuracy": round(float(accuracy_score(y_train, clf.predict(X_train))), 4),
        "test_accuracy":  round(float(accuracy_score(y_test, y_pred)), 4),
        "precision":      round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":         round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1_score":       round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "auc_roc":        round(float(roc_auc_score(y_test, y_prob)) if len(np.unique(y_test)) > 1 else 0.5, 4),
        "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        "y_pred": y_pred,
        "y_prob": y_prob,
    }


def _extract_feature_importance(clf) -> list[dict]:
    """从 Pipeline 的最后一步提取特征重要度，按降序排列。"""
    model_step = clf.named_steps["model"]
    if hasattr(model_step, "feature_importances_"):
        raw = model_step.feature_importances_.tolist()
    elif hasattr(model_step, "coef_"):
        raw = np.abs(model_step.coef_[0]).tolist()
    else:
        raw = [1.0 / len(FEATURE_NAMES)] * len(FEATURE_NAMES)

    return [
        {"name": name, "importance": round(float(imp), 6)}
        for name, imp in sorted(zip(FEATURE_NAMES, raw), key=lambda x: x[1], reverse=True)
    ]


def _latest_signal(clf, X_clean) -> tuple[str, float]:
    """对最新一根 K 线生成 BUY / SELL / NEUTRAL 信号。"""
    prob = float(clf.predict_proba(X_clean[-1:][0, None])[0, 1])
    if prob > 0.6:
        return "BUY", prob
    if prob < 0.4:
        return "SELL", prob
    return "NEUTRAL", prob


# ── 训练函数 ──────────────────────────────────────────────────────

def train_ml_strategy(
    df: pd.DataFrame,
    model_type: ModelType = "random_forest",
    forward_days: int = 5,
    test_size: float = 0.2,
) -> MLTrainResult:
    """
    训练 ML 策略模型。

    Parameters
    ----------
    df           : OHLCV DataFrame，index 为时间字符串
    model_type   : 模型类型
    forward_days : 前瞻期（预测 n 日后收益率方向）
    test_size    : 测试集比例（0.1~0.4）

    Returns
    -------
    MLTrainResult 包含评估指标和近期预测信号
    """
    from sklearn.model_selection import cross_val_score, TimeSeriesSplit

    # 1. Build features and target
    X = _build_features(df)
    close   = df["close"]
    fwd_ret = close.pct_change(forward_days).shift(-forward_days)
    y       = (fwd_ret > 0).astype(int)

    # 2. Align, drop NaNs
    combined = pd.concat([X, y.rename("target")], axis=1).dropna()
    if len(combined) < 100:
        raise ValueError(f"Not enough clean samples: {len(combined)} (need >= 100)")

    X_clean = combined[FEATURE_NAMES].values
    y_clean = combined["target"].values
    times   = combined.index.tolist()

    # 3. Time-series split (no shuffle)
    n_test  = max(int(len(X_clean) * test_size), 30)
    n_train = len(X_clean) - n_test
    X_train, X_test = X_clean[:n_train], X_clean[n_train:]
    y_train, y_test = y_clean[:n_train], y_clean[n_train:]

    # 4. Build, train
    clf = _build_pipeline(model_type)
    clf.fit(X_train, y_train)

    # 5. Evaluate
    metrics = _evaluate_clf(clf, X_train, y_train, X_test, y_test)

    # 6. Feature importance
    feature_importance = _extract_feature_importance(clf)

    # 7. Time-series cross-validation
    tscv      = TimeSeriesSplit(n_splits=5)
    cv_scores = cross_val_score(clf, X_train, y_train, cv=tscv, scoring="accuracy")

    # 8. Recent predictions (last 30 test bars)
    n_recent     = min(30, len(X_test))
    recent_pred  = clf.predict(X_test[-n_recent:])
    recent_prob  = clf.predict_proba(X_test[-n_recent:])[:, 1]
    recent_times = times[n_train:][-n_recent:]
    recent_y     = y_test[-n_recent:]

    predictions = [
        {"time": str(t), "actual": int(a), "predicted": int(p), "probability": round(float(pr), 4)}
        for t, a, p, pr in zip(recent_times, recent_y, recent_pred, recent_prob)
    ]

    # 9. Latest signal
    signal, latest_prob = _latest_signal(clf, X_clean)

    return MLTrainResult(
        model_type=model_type,
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
        feature_importance=feature_importance,
        confusion_matrix=metrics["confusion_matrix"],
        predictions=predictions,
        recent_signal=signal,
        recent_prob=round(latest_prob, 4),
        cv_mean=round(float(np.mean(cv_scores)), 4),
        cv_std=round(float(np.std(cv_scores)), 4),
    )
