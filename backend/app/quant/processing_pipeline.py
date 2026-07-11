"""
处理流水线（Processing Pipeline）

编排 infer_processors + learn_processors，实现 qlib 式的
「learn 处理器仅在训练窗口 fit / 全样本 apply」防泄漏流程，并提供
处理器注册表 + 工厂（build_processor / PROCESSOR_META）。

不变量：
  - 每个 is_stateful 的处理器必须位于 learn 列表，不得位于 infer 列表
  - 每个 is_for_infer()==False 的处理器必须位于 learn 列表
  - fit 仅切片 [fit_start, fit_end] 训练窗口，逐个 learn 处理器按顺序拟合，
    并将部分处理后的训练切片向后传递（thread）
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.quant.processors import (
    CSRankNorm,
    CSZScoreNorm,
    DropnaLabel,
    Fillna,
    Processor,
    ProcessorError,
    RobustZScoreNorm,
)


@dataclass(frozen=True)
class ProcessorConfig:
    name: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineResult:
    panel: pd.DataFrame
    fitted_learn: list[str]
    n_rows_in: int
    n_rows_out: int
    dropped_rows: int


# ── 注册表 / 工厂 ─────────────────────────────────────────────────

_REGISTRY: dict[str, type[Processor]] = {
    "CSRankNorm": CSRankNorm,
    "CSZScoreNorm": CSZScoreNorm,
    "Fillna": Fillna,
    "RobustZScoreNorm": RobustZScoreNorm,
    "DropnaLabel": DropnaLabel,
}


def build_processor(cfg: ProcessorConfig) -> Processor:
    """注册表工厂：未知 name → ProcessorError。"""
    cls = _REGISTRY.get(cfg.name)
    if cls is None:
        raise ProcessorError(
            f"未知处理器: {cfg.name}（可用: {', '.join(_REGISTRY)}）"
        )
    try:
        return cls(**cfg.params)
    except ProcessorError:
        raise
    except TypeError as e:
        raise ProcessorError(f"处理器 {cfg.name} 参数错误: {e}") from e


def _slice_datetime(panel: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按 datetime 层切片 [start, end]（比较日期前缀，兼容含时分秒的 ISO 串）。"""
    lvl = panel.index.get_level_values("datetime")
    d10 = pd.Index([str(v)[:10] for v in lvl])
    mask = (d10 >= start[:10]) & (d10 <= end[:10])
    return panel[np.asarray(mask)]


class ProcessingPipeline:
    def __init__(
        self,
        infer_processors: list[Processor],
        learn_processors: list[Processor],
    ) -> None:
        for p in infer_processors:
            if p.is_stateful:
                raise ProcessorError(
                    f"有状态处理器 {p.name} 不得置于 infer_processors（应为 learn）"
                )
            if not p.is_for_infer():
                raise ProcessorError(
                    f"{p.name} 的 is_for_infer=False，不得置于 infer_processors"
                )
        self.infer_processors = list(infer_processors)
        self.learn_processors = list(learn_processors)

    @classmethod
    def from_configs(
        cls,
        infer: list[ProcessorConfig],
        learn: list[ProcessorConfig],
    ) -> "ProcessingPipeline":
        return cls(
            infer_processors=[build_processor(c) for c in infer],
            learn_processors=[build_processor(c) for c in learn],
        )

    def fit(self, panel: pd.DataFrame, fit_start: str, fit_end: str) -> "ProcessingPipeline":
        """在 [fit_start, fit_end] 训练切片上拟合 learn 处理器，返回新流水线。"""
        train = _slice_datetime(panel, fit_start, fit_end)
        # 先施加 infer 处理器（无状态、安全），使 learn 处理器看到与 apply 期一致的表示
        for p in self.infer_processors:
            train = p(train)

        fitted: list[Processor] = []
        for p in self.learn_processors:
            if p.is_stateful:
                fp = p.fit(train)
            else:
                fp = p
            fitted.append(fp)
            # 向后传递部分处理后的训练切片，供下一个处理器拟合
            train = fp(train)

        return ProcessingPipeline(
            infer_processors=self.infer_processors,
            learn_processors=fitted,
        )

    def process(self, panel: pd.DataFrame, *, for_infer: bool = False) -> PipelineResult:
        """按顺序施加 infer → learn 处理器于全面板。for_infer=True 时跳过
        is_for_infer()==False 的处理器（如 DropnaLabel）。"""
        n_in = len(panel)
        out = panel.copy()
        for p in self.infer_processors:
            out = p(out)

        fitted_names: list[str] = []
        for p in self.learn_processors:
            if for_infer and not p.is_for_infer():
                continue
            if p.is_stateful:
                fitted_names.append(p.name)
            out = p(out)

        n_out = len(out)
        return PipelineResult(
            panel=out,
            fitted_learn=fitted_names,
            n_rows_in=n_in,
            n_rows_out=n_out,
            dropped_rows=n_in - n_out,
        )


# ── 前端构建器元数据 ──────────────────────────────────────────────

PROCESSOR_META: list[dict] = [
    {
        "name": "CSRankNorm",
        "label": "截面排名标准化",
        "kind": "infer",
        "is_for_infer": True,
        "params": [
            {
                "name": "fields",
                "type": "list[str]",
                "default": None,
                "description": "作用列（留空=全部数值特征列）",
            },
        ],
    },
    {
        "name": "CSZScoreNorm",
        "label": "截面 Z 值标准化",
        "kind": "infer",
        "is_for_infer": True,
        "params": [
            {
                "name": "fields",
                "type": "list[str]",
                "default": None,
                "description": "作用列（留空=全部数值特征列）",
            },
            {
                "name": "method",
                "type": "str",
                "default": "zscore",
                "description": "zscore=均值/标准差；robust=中位数/MAD",
            },
        ],
    },
    {
        "name": "Fillna",
        "label": "缺失值填充",
        "kind": "infer",
        "is_for_infer": True,
        "params": [
            {
                "name": "fields",
                "type": "list[str]",
                "default": None,
                "description": "作用列（留空=全部数值特征列）",
            },
            {
                "name": "fill_value",
                "type": "float",
                "default": 0.0,
                "description": "填充值",
            },
        ],
    },
    {
        "name": "RobustZScoreNorm",
        "label": "鲁棒 Z 值标准化（训练窗拟合）",
        "kind": "learn",
        "is_for_infer": True,
        "params": [
            {
                "name": "fields",
                "type": "list[str]",
                "default": None,
                "description": "作用列（留空=全部数值特征列）",
            },
            {
                "name": "clip_outlier",
                "type": "bool",
                "default": True,
                "description": "是否裁剪极端值",
            },
            {
                "name": "clip_bound",
                "type": "float",
                "default": 3.0,
                "description": "裁剪边界 ±clip_bound",
            },
        ],
    },
    {
        "name": "DropnaLabel",
        "label": "丢弃无标签行（仅训练）",
        "kind": "learn",
        "is_for_infer": False,
        "params": [
            {
                "name": "label_field",
                "type": "str",
                "default": "label",
                "description": "标签列名",
            },
        ],
    },
]
