"""
横截面因子处理器（Cross-Sectional Processors）

移植自 qlib 的 `data/dataset/processor.py` 的「fit / __call__」设计，但改写为
纯 pandas、面板（panel）数据模型，并强制**不可变**（返回新 DataFrame，绝不原地修改，
这一点与 qlib 允许 in-place 的行为明确不同）。

面板约定：
  index = MultiIndex(names=["datetime", "instrument"])
  columns = 扁平的因子/特征列 + 可选的 label 列

防泄漏的核心不变量（见 processing_pipeline.py）：
  - infer 处理器：无状态或 fit 为 no-op，仅使用同一 datetime 的数据 → 天然安全
  - learn 处理器：有状态（均值/中位数/MAD 等），必须只在训练窗口 [fit_start, fit_end] 上 fit
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from app.quant.formula_factor import _EPS

# 共享常量（禁止内联魔数）
EPS: float = _EPS
MAD_SCALE: float = 1.4826          # MAD → 正态标准差的一致性缩放因子
RANK_STD_SCALE: float = 3.46       # rank(pct) 居中后放大到 ≈ 单位标准差
LABEL_DEFAULT: str = "label"


class ProcessorError(ValueError):
    """处理器配置错误或 fit/apply 执行失败。"""


def _resolve_fields(panel: pd.DataFrame, fields: list[str] | None) -> list[str]:
    """解析待处理列：fields=None 表示除 label 外的全部数值特征列。"""
    if fields is not None:
        missing = [f for f in fields if f not in panel.columns]
        if missing:
            raise ProcessorError(f"字段不存在于面板中: {missing}")
        return list(fields)
    return [
        c for c in panel.columns
        if c != LABEL_DEFAULT and is_numeric_dtype(panel[c])
    ]


class Processor:
    """处理器基类。默认无状态；有状态处理器需覆写 fit()。"""

    def fit(self, panel: pd.DataFrame) -> "Processor":
        """在训练窗口切片上学习参数，返回**新的**已拟合实例（不修改 self）。
        无状态处理器为 no-op，直接返回自身。"""
        return self

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        """返回**新的**已变换面板，绝不修改输入。"""
        raise NotImplementedError

    def is_for_infer(self) -> bool:
        """False 表示该处理器不得出现在推理/测试路径（如 DropnaLabel）。"""
        return True

    @property
    def is_stateful(self) -> bool:
        """True 表示必须作为 learn 处理器（拟合时序/面板统计量）。"""
        return False

    @property
    def name(self) -> str:
        return type(self).__name__


# ── infer 处理器（无状态，同一 datetime 内做截面运算）──────────────

class CSRankNorm(Processor):
    """截面排名标准化：每个 datetime 内 rank(pct=True) 后 (r-0.5)*3.46。

    输出 ≈ 均值 0、单位标准差、有界。NaN 保持 NaN（不参与排名）。
    单标的 → rank=0.5 → 输出 0；整截面全 NaN → 全 NaN。天然防泄漏。
    """

    def __init__(self, fields: list[str] | None = None) -> None:
        self.fields = list(fields) if fields is not None else None

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        out = panel.copy()
        for col in _resolve_fields(panel, self.fields):
            grp = out[col].groupby(level="datetime")
            ranked = grp.rank(pct=True)
            normed = (ranked - 0.5) * RANK_STD_SCALE
            # 单标的截面：rank(pct)=1.0 → 输出 0（无横截面信息）
            counts = grp.transform("count")
            normed = normed.mask(counts <= 1, 0.0)
            out[col] = normed
        return out


class CSZScoreNorm(Processor):
    """截面 Z 值标准化：每个 datetime 内标准化。

    method="zscore" → (x-mean)/(std+EPS)
    method="robust" → (x-median)/(MAD*1.4826+EPS)
    常数截面（std/MAD=0）或单标的 → 由 EPS 兜底，输出 0。天然防泄漏。
    """

    def __init__(
        self,
        fields: list[str] | None = None,
        method: Literal["zscore", "robust"] = "zscore",
    ) -> None:
        if method not in ("zscore", "robust"):
            raise ProcessorError(f"method 非法: {method}（应为 zscore/robust）")
        self.fields = list(fields) if fields is not None else None
        self.method = method

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        out = panel.copy()
        for col in _resolve_fields(panel, self.fields):
            grp = out[col].groupby(level="datetime")
            if self.method == "zscore":
                center = grp.transform("mean")
                # 单标的截面 std(ddof=1)=NaN → 用 0 兜底，配合 EPS 输出 0
                scale = grp.transform("std").fillna(0.0)
            else:
                center = grp.transform("median")
                scale = grp.transform(
                    lambda s: (s - s.median()).abs().median() * MAD_SCALE
                ).fillna(0.0)
            out[col] = (out[col] - center) / (scale + EPS)
        return out


class Fillna(Processor):
    """用固定值填充 NaN（默认 0）。无状态。顺序上应置于标准化之后，
    避免填充值污染已拟合统计量。"""

    def __init__(self, fields: list[str] | None = None, fill_value: float = 0.0) -> None:
        try:
            self.fill_value = float(fill_value)
        except (TypeError, ValueError) as e:
            raise ProcessorError(f"fill_value 非法: {fill_value}") from e
        self.fields = list(fields) if fields is not None else None

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        out = panel.copy()
        cols = _resolve_fields(panel, self.fields)
        out[cols] = out[cols].fillna(self.fill_value)
        return out


# ── learn 处理器（有状态，仅在训练窗口 fit）────────────────────────

class RobustZScoreNorm(Processor):
    """鲁棒 Z 值标准化（防泄漏敏感）。

    fit()：在训练窗口内逐列计算 mean_=nanmedian(X)，
            std_=nanmedian(|X-mean_|)*1.4826 + EPS。
    __call__()：对**全**面板应用 (x-mean_)/std_，clip_outlier 时裁剪到
            [-clip_bound, clip_bound]。
    这是典型的前视陷阱：若在全样本上拟合 median/MAD，会把测试期尺度泄漏进训练。
    """

    def __init__(
        self,
        fields: list[str] | None = None,
        clip_outlier: bool = True,
        clip_bound: float = 3.0,
    ) -> None:
        if clip_bound <= 0:
            raise ProcessorError(f"clip_bound 必须为正: {clip_bound}")
        self.fields = list(fields) if fields is not None else None
        self.clip_outlier = bool(clip_outlier)
        self.clip_bound = float(clip_bound)
        self._fitted: bool = False
        self._cols: list[str] = []
        self._mean: dict[str, float] = {}
        self._std: dict[str, float] = {}

    @property
    def is_stateful(self) -> bool:
        return True

    def fit(self, panel: pd.DataFrame) -> "RobustZScoreNorm":
        cols = _resolve_fields(panel, self.fields)
        new = RobustZScoreNorm(
            fields=self.fields,
            clip_outlier=self.clip_outlier,
            clip_bound=self.clip_bound,
        )
        mean: dict[str, float] = {}
        std: dict[str, float] = {}
        for col in cols:
            x = panel[col].to_numpy(dtype=float)
            m = float(np.nanmedian(x)) if np.isfinite(x).any() else 0.0
            mad = float(np.nanmedian(np.abs(x - m))) if np.isfinite(x).any() else 0.0
            mean[col] = m
            std[col] = mad * MAD_SCALE + EPS
        new._fitted = True
        new._cols = cols
        new._mean = mean
        new._std = std
        return new

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise ProcessorError("RobustZScoreNorm 未 fit 即被调用")
        out = panel.copy()
        for col in self._cols:
            if col not in out.columns:
                continue
            out[col] = (out[col] - self._mean[col]) / self._std[col]
            if self.clip_outlier:
                out[col] = out[col].clip(-self.clip_bound, self.clip_bound)
        return out


class DropnaLabel(Processor):
    """丢弃 label 为 NaN 的行（如每个标的末尾 forward_period 根无实现前瞻收益的 bar）。

    label 可用性属训练期概念，故 process(for_infer=True) 时**跳过**该处理器，
    以保留推理/测试路径上全部可交易行。
    """

    def __init__(self, label_field: str = LABEL_DEFAULT) -> None:
        if not label_field:
            raise ProcessorError("label_field 不能为空")
        self.label_field = str(label_field)

    def is_for_infer(self) -> bool:
        return False

    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        if self.label_field not in panel.columns:
            # 没有 label 列则无行可丢，返回原样副本
            return panel.copy()
        return panel[panel[self.label_field].notna()].copy()
