"""序列模型训练/预测流水线（B8）。

复用 ml_strategy 的 8 个技术指标特征，构造滑动窗口序列样本，
用 PyTorch 训练 LSTM / GRU / ALSTM 二分类模型，预测 n 日后价格方向。

torch 为可选依赖：所有 torch 相关调用均在函数内部延迟导入。
未安装 torch 时 torch_available() 返回 False，端点据此返回 501。

CPU 即可运行（默认轮次/隐藏维度均较小）。输出结构为 MLTrainResult 的超集
（额外附带训练损失曲线与序列超参），与前端 ML 面板兼容。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.models.networks import (
    SEQUENCE_MODEL_META,
    VALID_MODEL_TYPES,
    SequenceModelType,
    build_network,
)
from app.quant.ml_strategy import FEATURE_NAMES, _build_features

# ── 常量 ──────────────────────────────────────────────────────────

MIN_SAMPLES = 120            # 训练所需最少干净窗口样本
RANDOM_SEED = 42
BUY_THRESHOLD = 0.6          # 最新信号：概率 > 0.6 → BUY
SELL_THRESHOLD = 0.4         # 概率 < 0.4 → SELL
N_RECENT_PRED = 30           # 返回近期预测的样本数


# ── 结果类 ────────────────────────────────────────────────────────

@dataclass
class SequenceTrainResult:
    model_type:      str
    forward_days:    int
    seq_len:         int
    epochs:          int
    hidden_size:     int
    num_layers:      int
    n_samples:       int
    n_features:      int
    feature_names:   list[str]

    train_accuracy:  float
    test_accuracy:   float
    precision:       float
    recall:          float
    f1_score:        float
    auc_roc:         float

    feature_importance: list[dict]        # 排列重要度，降序 [{name, importance}]
    confusion_matrix:   list[list[int]]   # [[TN, FP], [FN, TP]]
    predictions:        list[dict]        # [{time, actual, predicted, probability}]

    recent_signal:   str                  # BUY | SELL | NEUTRAL
    recent_prob:     float

    train_loss_curve: list[float]         # 每轮训练集损失
    val_loss_curve:   list[float]         # 每轮测试集损失


# ── torch 可用性 ──────────────────────────────────────────────────

def torch_available() -> bool:
    """检测 torch 是否已安装（不触发实际 import）。"""
    return importlib.util.find_spec("torch") is not None


TORCH_INSTALL_HINT = "需 pip install torch 启用序列模型（LSTM/GRU/ALSTM）"


def sequence_models_meta() -> dict:
    """返回可用模型列表 + torch 就绪状态（供 GET 端点）。"""
    return {
        "torch_ready": torch_available(),
        "install_hint": TORCH_INSTALL_HINT,
        "models": SEQUENCE_MODEL_META,
    }


# ── 序列构造 ──────────────────────────────────────────────────────

def _build_sequences(
    df: pd.DataFrame, forward_days: int, seq_len: int,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """构造滑动窗口序列样本。

    Returns (X, y, times)：
      X     shape=(N, seq_len, n_features)
      y     shape=(N,)  1=n日后收益>0
      times 每个样本窗口末端的时间字符串
    """
    features = _build_features(df)
    close = df["close"]
    fwd_ret = close.pct_change(forward_days).shift(-forward_days)
    target = (fwd_ret > 0).astype(float)

    combined = pd.concat([features, target.rename("target")], axis=1).dropna()
    if len(combined) < seq_len + 1:
        raise ValueError(f"清洗后样本不足以构造序列: {len(combined)}")

    feat_mat = combined[FEATURE_NAMES].values.astype(np.float32)
    y_all = combined["target"].values.astype(np.float32)
    idx = combined.index.tolist()

    windows, labels, times = [], [], []
    for end in range(seq_len - 1, len(feat_mat)):
        windows.append(feat_mat[end - seq_len + 1: end + 1])
        labels.append(y_all[end])
        times.append(str(idx[end]))

    return np.asarray(windows), np.asarray(labels), times


def _standardize(
    x_train: np.ndarray, x_test: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """按训练集统计量对特征维标准化（仅用训练集拟合，防泄漏）。"""
    flat = x_train.reshape(-1, x_train.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0)
    std[std == 0] = 1.0
    return (x_train - mean) / std, (x_test - mean) / std


# ── 指标计算 ──────────────────────────────────────────────────────

def _compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray) -> dict:
    """计算准确率/精确率/召回/F1/AUC/混淆矩阵。"""
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, roc_auc_score, confusion_matrix,
    )

    auc = 0.5
    if len(np.unique(y_true)) > 1:
        auc = float(roc_auc_score(y_true, y_prob))

    return {
        "test_accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1_score": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "auc_roc": round(auc, 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=[0, 1]).tolist(),
    }


def _latest_signal(prob: float) -> str:
    """根据最新窗口概率生成 BUY / SELL / NEUTRAL。"""
    if prob > BUY_THRESHOLD:
        return "BUY"
    if prob < SELL_THRESHOLD:
        return "SELL"
    return "NEUTRAL"


# ── 训练主流程 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class SequenceConfig:
    model_type:    SequenceModelType = "lstm"
    forward_days:  int = 5
    seq_len:       int = 20
    epochs:        int = 30
    hidden_size:   int = 32
    num_layers:    int = 2
    learning_rate: float = 1e-3
    dropout:       float = 0.2
    test_size:     float = 0.2

    def __post_init__(self) -> None:
        if self.model_type not in VALID_MODEL_TYPES:
            raise ValueError(f"未知序列模型类型: {self.model_type}")


def train_sequence_model(df: pd.DataFrame, config: SequenceConfig) -> SequenceTrainResult:
    """训练序列模型并返回评估结果。调用前须确认 torch_available()。"""
    if not torch_available():
        raise RuntimeError(TORCH_INSTALL_HINT)

    X, y, times = _build_sequences(df, config.forward_days, config.seq_len)
    if len(X) < MIN_SAMPLES:
        raise ValueError(f"序列样本不足: {len(X)}（需 >= {MIN_SAMPLES}）")

    n_test = max(int(len(X) * config.test_size), 30)
    n_train = len(X) - n_test
    x_train, x_test = _standardize(X[:n_train], X[n_train:])
    y_train, y_test = y[:n_train], y[n_train:]

    net, train_curve, val_curve = _fit_network(
        config, x_train, y_train, x_test, y_test,
    )

    prob_train = _predict_proba(net, x_train)
    prob_test = _predict_proba(net, x_test)
    pred_test = (prob_test >= 0.5).astype(int)

    metrics = _compute_metrics(y_test.astype(int), pred_test, prob_test)
    train_acc = round(float(((prob_train >= 0.5).astype(int) == y_train).mean()), 4)
    importance = _permutation_importance(net, x_test, y_test, metrics["test_accuracy"])

    return _assemble_result(
        config, X, times, n_train, y_test, pred_test, prob_test,
        metrics, train_acc, importance, train_curve, val_curve,
    )


def _fit_network(config, x_train, y_train, x_test, y_test):
    """执行训练循环，返回 (net, train_loss_curve, val_loss_curve)。"""
    import torch
    from torch import nn

    torch.manual_seed(RANDOM_SEED)
    net = build_network(
        config.model_type, x_train.shape[-1],
        config.hidden_size, config.num_layers, config.dropout,
    )
    loss_fn = nn.BCEWithLogitsLoss()
    optim = torch.optim.Adam(net.parameters(), lr=config.learning_rate)

    xt = torch.from_numpy(x_train)
    yt = torch.from_numpy(y_train)
    xv = torch.from_numpy(x_test)
    yv = torch.from_numpy(y_test)

    train_curve, val_curve = [], []
    for _ in range(config.epochs):
        net.train()
        optim.zero_grad()
        loss = loss_fn(net(xt), yt)
        loss.backward()
        optim.step()
        train_curve.append(round(float(loss.item()), 5))

        net.eval()
        with torch.no_grad():
            val_curve.append(round(float(loss_fn(net(xv), yv).item()), 5))

    return net, train_curve, val_curve


def _predict_proba(net, x: np.ndarray) -> np.ndarray:
    """前向推理返回正类概率。"""
    import torch

    net.eval()
    with torch.no_grad():
        logits = net(torch.from_numpy(x.astype(np.float32)))
        return torch.sigmoid(logits).cpu().numpy()


def _permutation_importance(
    net, x_test: np.ndarray, y_test: np.ndarray, base_acc: float,
) -> list[dict]:
    """排列重要度：逐特征在时间维打乱，测准确率下降量。"""
    rng = np.random.default_rng(RANDOM_SEED)
    scores = []
    for f in range(x_test.shape[-1]):
        permuted = x_test.copy()
        perm = rng.permutation(permuted.shape[0])
        permuted[:, :, f] = permuted[perm, :, f]
        acc = float(((_predict_proba(net, permuted) >= 0.5).astype(int) == y_test).mean())
        scores.append(max(base_acc - acc, 0.0))

    total = sum(scores) or 1.0
    normed = [s / total for s in scores]
    return [
        {"name": name, "importance": round(float(imp), 6)}
        for name, imp in sorted(
            zip(FEATURE_NAMES, normed), key=lambda kv: kv[1], reverse=True,
        )
    ]


def _assemble_result(
    config, X, times, n_train, y_test, pred_test, prob_test,
    metrics, train_acc, importance, train_curve, val_curve,
) -> SequenceTrainResult:
    """组装 SequenceTrainResult（近期预测 + 最新信号）。"""
    n_recent = min(N_RECENT_PRED, len(pred_test))
    recent_times = times[n_train:][-n_recent:]
    predictions = [
        {
            "time": t, "actual": int(a),
            "predicted": int(p), "probability": round(float(pr), 4),
        }
        for t, a, p, pr in zip(
            recent_times, y_test[-n_recent:],
            pred_test[-n_recent:], prob_test[-n_recent:],
        )
    ]

    # 最后一个测试样本窗口即最新时点，其概率作为当前信号
    latest_prob = round(float(prob_test[-1]), 4)
    signal = _latest_signal(latest_prob)

    return SequenceTrainResult(
        model_type=config.model_type,
        forward_days=config.forward_days,
        seq_len=config.seq_len,
        epochs=config.epochs,
        hidden_size=config.hidden_size,
        num_layers=config.num_layers,
        n_samples=len(X),
        n_features=len(FEATURE_NAMES),
        feature_names=FEATURE_NAMES,
        train_accuracy=train_acc,
        test_accuracy=metrics["test_accuracy"],
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1_score=metrics["f1_score"],
        auc_roc=metrics["auc_roc"],
        feature_importance=importance,
        confusion_matrix=metrics["confusion_matrix"],
        predictions=predictions,
        recent_signal=signal,
        recent_prob=latest_prob,
        train_loss_curve=train_curve,
        val_loss_curve=val_curve,
    )
