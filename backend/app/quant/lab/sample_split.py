"""
样本内 / 样本外切分（V3 · I2 自动因子研发循环）

**这个模块是整个自动循环唯一的正确性支点。**

遗传搜索会过拟合它评估过的那段数据 —— 评估两千个表达式再挑最好的五个，
它们的 IC 一定好看，这是多重检验下的必然。若最终报告用的还是同一段样本，
产出的数字度量的是搜索强度，不是预测能力。

因此切分不是「最后再算一遍样本外」，而是**物理隔离**：

  - 样本内的 OHLCV 帧被**截断**在 `is_end`（连着行一起丢掉，不是打标记）。
    搜索器拿到的 `dict[str, DataFrame]` 里根本不存在 `is_end` 之后的行，
    于是「不小心用到未来数据」在类型层面就无法发生。
  - 样本内的前瞻收益面板再多丢 `forward_period` 根 bar（禁运期 / embargo）。
    `label[t] = close[t+p]/close[t]-1` 会偷看 `t+p` 的价格；不丢这几根，
    「样本内截止于 is_end」就是一句自欺欺人的话。
  - 样本外那份 `SamplePanels` 由本模块**在切分时一并算好**，但搜索函数的签名
    只接受一个 `SamplePanels`。调用方要想把样本外喂进搜索，得显式写出
    `split.out_of_sample` —— 这是一个改不动的、看得见的动作，
    而不是一个可以顺手忽略的约定。

样本外那份的 OHLCV 刻意保留**全量历史**：`ZSCORE` 之类的算子要 rolling(60)
才能给出第一个有效值，砍掉热身段会让样本外前 60 根全是 NaN。用过去的数据算
今天的因子不是泄漏；打分时由 `factor_eval` 把因子面板限制回样本外日期即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SamplePanels:
    """一段样本：因子求值所需的 OHLCV，以及**这一段**的前瞻收益 / 流动性面板。"""

    ohlcv_by_symbol: dict[str, pd.DataFrame]
    forward_return_panel: pd.DataFrame
    liquidity_panel: pd.DataFrame | None

    @property
    def is_empty(self) -> bool:
        return self.forward_return_panel.empty or not self.ohlcv_by_symbol

    @property
    def label_end(self) -> date | None:
        """本段最后一个**有标签**的日期（前瞻收益面板的末日期）。"""
        return _max_date(self.forward_return_panel.index.get_level_values("datetime"))

    @property
    def feature_end(self) -> date | None:
        """本段 OHLCV 的末日期 —— 搜索期用它断言「样本外确实不可见」。"""
        ends = [
            _max_date(frame.index)
            for frame in self.ohlcv_by_symbol.values()
            if len(frame) > 0
        ]
        present = [d for d in ends if d is not None]
        return max(present) if present else None


@dataclass(frozen=True)
class SampleSplit:
    """一次切分的完整结果。样本外可能为 None —— `is_end` 之后没有数据是正常情况。"""

    in_sample: SamplePanels
    out_of_sample: SamplePanels | None
    is_end: date
    embargo_bars: int

    @property
    def has_out_of_sample(self) -> bool:
        return self.out_of_sample is not None and not self.out_of_sample.is_empty


class SampleSplitError(ValueError):
    """切分后样本内为空 —— `is_end` 太早或数据太少，没法搜索。"""


def split_by_is_end(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    forward_return_panel: pd.DataFrame,
    liquidity_panel: pd.DataFrame | None,
    is_end: date,
    forward_period: int,
) -> SampleSplit:
    """
    按 `is_end` 把面板切成样本内 / 样本外两份。

    Parameters
    ----------
    ohlcv_by_symbol      : 每标的 OHLCV（index 为 ISO 时间字符串）
    forward_return_panel : `(datetime, instrument)` 前瞻收益单列面板（全量）
    liquidity_panel      : 流动性面板（可选，全量）
    is_end               : 样本内截止日（含当日）
    forward_period       : 前瞻期，决定禁运期长度

    Raises
    ------
    SampleSplitError : 样本内没有任何可用数据
    """
    if forward_period < 0:
        raise SampleSplitError(f"forward_period 不能为负：{forward_period}")

    in_ohlcv = {
        symbol: frame.loc[_date_mask(frame.index, is_end, keep_after=False)]
        for symbol, frame in ohlcv_by_symbol.items()
    }
    in_ohlcv = {symbol: frame for symbol, frame in in_ohlcv.items() if len(frame) > 0}

    in_fwd = _embargo(_slice_panel(forward_return_panel, is_end, False), forward_period)
    in_liq = _slice_panel(liquidity_panel, is_end, False) if liquidity_panel is not None else None

    if not in_ohlcv or in_fwd.empty:
        raise SampleSplitError(
            f"样本内为空：is_end={is_end.isoformat()} 之前没有足够数据"
            f"（前瞻期 {forward_period} 根 bar 需作为禁运期丢弃）"
        )

    out_fwd = _slice_panel(forward_return_panel, is_end, True)
    out_liq = (
        _slice_panel(liquidity_panel, is_end, True) if liquidity_panel is not None else None
    )
    # 样本外保留全量 OHLCV：滚动算子需要 is_end 之前的历史做热身。
    # 打分时由 factor_eval 把因子面板限制回 out_fwd 的日期集合。
    out_sample = (
        None
        if out_fwd.empty
        else SamplePanels(dict(ohlcv_by_symbol), out_fwd, out_liq)
    )

    return SampleSplit(
        in_sample=SamplePanels(in_ohlcv, in_fwd, in_liq),
        out_of_sample=out_sample,
        is_end=is_end,
        embargo_bars=forward_period,
    )


# ── 内部 ──────────────────────────────────────────────────────────

def _to_dates(index) -> np.ndarray:
    """任意时间索引（ISO 字符串 / Timestamp）→ `datetime.date` 数组。"""
    return pd.to_datetime(pd.Index(index), errors="coerce").date


def _date_mask(index, is_end: date, keep_after: bool) -> np.ndarray:
    dates = _to_dates(index)
    if keep_after:
        return np.array([d is not None and d > is_end for d in dates], dtype=bool)
    return np.array([d is not None and d <= is_end for d in dates], dtype=bool)


def _slice_panel(panel: pd.DataFrame, is_end: date, keep_after: bool) -> pd.DataFrame:
    mask = _date_mask(panel.index.get_level_values("datetime"), is_end, keep_after)
    return panel.loc[mask]


def _embargo(panel: pd.DataFrame, forward_period: int) -> pd.DataFrame:
    """逐标的丢掉末尾 `forward_period` 行 —— 它们的标签会偷看 `is_end` 之后的价格。"""
    if forward_period <= 0 or panel.empty:
        return panel
    kept = [
        group.iloc[:-forward_period]
        for _, group in panel.sort_index().groupby(level="instrument")
        if len(group) > forward_period
    ]
    if not kept:
        return panel.iloc[:0]
    return pd.concat(kept).sort_index()


def _max_date(index) -> date | None:
    dates = [d for d in _to_dates(index) if d is not None]
    return max(dates) if dates else None
