"""
面板适配器（Panel Adapters）

在平台的「单标的 bar 序列」与横截面处理所需的
(datetime, instrument) 长表面板之间做转换。

面板约定：
  index = MultiIndex(names=["datetime", "instrument"])
  columns = OHLCV + 可选特征列 + 可选 label 列
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from app.data.models import Bar

# 单标的最少 bar 数（低于则从 universe 中剔除，见端点）
MIN_BARS_PER_SYMBOL: int = 60
# 序列化时的默认最大行数
DEFAULT_MAX_ROWS: int = 2000


def _bars_to_ohlcv(bars: list["Bar"]) -> pd.DataFrame:
    """单标的 bar 列表 → 以 ISO 时间字符串为 index 的 OHLCV DataFrame。"""
    df = pd.DataFrame(
        [
            {
                "time": b.time.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": float(b.volume),
            }
            for b in bars
        ]
    )
    return df.set_index("time").sort_index()


def bars_to_panel(
    bars_by_symbol: dict[str, list["Bar"]],
    feature_fn: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """由每标的 bar 序列构建 (datetime, instrument) 面板。

    始终保留 OHLCV 列以便后续计算 label / 流动性；若给定 feature_fn，则其输出
    的特征列会**追加/覆盖**到面板上（feature_fn: 单标的 OHLCV 帧 → 特征帧/序列）。
    """
    frames: list[pd.DataFrame] = []
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        ohlcv = _bars_to_ohlcv(bars)
        cols = ohlcv.copy()
        if feature_fn is not None:
            feats = feature_fn(ohlcv)
            if isinstance(feats, pd.Series):
                feats = feats.to_frame("factor")
            for c in feats.columns:
                cols[c] = feats[c].astype(float)
        cols.index.name = "datetime"
        cols["instrument"] = symbol
        frames.append(cols.set_index("instrument", append=True))

    if not frames:
        raise ValueError("bars_to_panel: 无可用标的数据")

    panel = pd.concat(frames).sort_index()
    return panel


def attach_forward_label(
    panel: pd.DataFrame,
    forward_period: int,
    label_field: str = "label",
) -> pd.DataFrame:
    """按标的追加前瞻收益标签：close.pct_change(p).shift(-p)。不可变。"""
    if forward_period < 1:
        raise ValueError(f"forward_period 必须 ≥ 1: {forward_period}")
    if "close" not in panel.columns:
        raise ValueError("attach_forward_label: 面板缺少 close 列")

    out = panel.copy()

    def _fwd(s: pd.Series) -> pd.Series:
        return s.pct_change(forward_period).shift(-forward_period)

    out[label_field] = out.groupby(level="instrument")["close"].transform(_fwd)
    out[label_field] = out[label_field].replace([np.inf, -np.inf], np.nan)
    return out


def _json_safe(value: float) -> float | None:
    if value is None:
        return None
    v = float(value)
    if np.isnan(v) or np.isinf(v):
        return None
    return round(v, 6)


def panel_to_records(panel: pd.DataFrame, max_rows: int = DEFAULT_MAX_ROWS) -> list[dict]:
    """把面板尾部序列化为 JSON 安全记录（NaN→null）。"""
    tail = panel.tail(max_rows)
    records: list[dict] = []
    for idx, row in tail.iterrows():
        dt, inst = idx
        rec: dict = {"time": str(dt), "instrument": str(inst)}
        for col in panel.columns:
            rec[col] = _json_safe(row[col])
        records.append(rec)
    return records


def column_cells(panel: pd.DataFrame, column: str, max_rows: int = 500) -> list[dict]:
    """把面板某一列尾部序列化为 [{time, instrument, value}] 单元格列表。"""
    if column not in panel.columns:
        return []
    tail = panel[column].tail(max_rows)
    cells: list[dict] = []
    for idx, value in tail.items():
        dt, inst = idx
        cells.append(
            {"time": str(dt), "instrument": str(inst), "value": _json_safe(value)}
        )
    return cells


def column_stats(panel: pd.DataFrame, column: str) -> dict:
    """计算某列的分布统计（供 preview 的 raw/processed 对比）。"""
    if column not in panel.columns:
        return _empty_stats()
    s = panel[column]
    total = int(len(s))
    valid = s.dropna()
    n = int(len(valid))
    if n == 0:
        stats = _empty_stats()
        stats["nan_rate"] = round(1.0, 6) if total else 0.0
        return stats
    arr = valid.to_numpy(dtype=float)
    return {
        "count": n,
        "mean": _num(np.mean(arr)),
        "std": _num(np.std(arr)),
        "min": _num(np.min(arr)),
        "p25": _num(np.percentile(arr, 25)),
        "median": _num(np.median(arr)),
        "p75": _num(np.percentile(arr, 75)),
        "max": _num(np.max(arr)),
        "nan_rate": round((total - n) / total, 6) if total else 0.0,
    }


def _num(v: float) -> float:
    f = float(v)
    if np.isnan(f) or np.isinf(f):
        return 0.0
    return round(f, 6)


def _empty_stats() -> dict:
    return {
        "count": 0,
        "mean": 0.0,
        "std": 0.0,
        "min": 0.0,
        "p25": 0.0,
        "median": 0.0,
        "p75": 0.0,
        "max": 0.0,
        "nan_rate": 0.0,
    }
