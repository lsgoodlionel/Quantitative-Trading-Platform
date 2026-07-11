"""
前视 / 递归偏差检测（C3）

在不修改策略代码的前提下，通过「重跑对比」检测两类隐蔽偏差：

1. 前视偏差 (Look-ahead bias)
   策略是否偷看了未来数据。做法：把数据在某个时间点截断，重跑回测，
   比较「截断点之前」本应完全一致的成交记录是否发生变化。若变化 →
   说明历史决策依赖了截断掉的未来 bar，即前视偏差。
   参考: refs/freqtrade/freqtrade/optimize/analysis/lookahead.py

2. 递归偏差 / 起点敏感性 (Recursive bias)
   指标/信号是否随「可见历史长度」变化而漂移。做法：在数据前端裁掉不同数量的
   startup bar，重跑回测，比较重叠尾部的成交是否一致。若不一致 → 结果受历史起点
   影响，可能源于未收敛的递归指标（如 EMA/ATR 等），也可能源于回测起始日导致的
   持仓路径差异——两者都是实盘一致性的真实风险。
   参考: refs/freqtrade/freqtrade/optimize/analysis/recursive.py

适配说明：本引擎无独立的 indicator DataFrame，故以「成交序列」作为策略决策的可观测
代理进行差异比对（成交由指标信号驱动，等价反映决策变化）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

from app.data.models import Bar

logger = logging.getLogger(__name__)

# 默认递归检测使用的 startup bar 裁剪数量
DEFAULT_STARTUP_CANDLES = [50, 100, 200]
# 前视检测默认截断比例（保留前 x 的数据）
_LOOKAHEAD_CUT_RATIO = 0.7
# 成交指纹保留的小数位（价格）
_PRICE_PRECISION = 4

# bars -> fills（成交记录列表，每条含 filled_at/side/qty/price）
RunFillsFn = Callable[[list[Bar]], list[dict]]


@dataclass
class SignalDiff:
    checked_signals: int   # 重叠区间内被比对的成交数
    changed_signals: int   # 发生变化的成交数
    detail: str


@dataclass
class RecursiveDiff:
    startup_candle: int
    checked_signals: int
    changed_signals: int


@dataclass
class BiasCheckOutcome:
    has_lookahead_bias: bool
    has_recursive_bias: bool
    total_signals: int
    lookahead: SignalDiff
    recursive: list[RecursiveDiff] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _fingerprint(fill: dict) -> tuple:
    """成交指纹：时间 + 方向 + 数量 + 价格（用于逐笔比对）。"""
    return (
        str(fill.get("filled_at")),
        str(fill.get("side")).upper(),
        int(fill.get("qty", 0)),
        round(float(fill.get("price", 0.0)), _PRICE_PRECISION),
    )


def _fills_before(fills: list[dict], cutoff_iso: str | None) -> list[tuple]:
    """返回 filled_at < cutoff 的成交指纹列表（cutoff=None 则全部）。"""
    out: list[tuple] = []
    for f in fills:
        ts = f.get("filled_at")
        if cutoff_iso is not None and ts is not None and str(ts) >= cutoff_iso:
            continue
        out.append(_fingerprint(f))
    return out


def _count_changed(baseline: list[tuple], candidate: list[tuple]) -> tuple[int, int]:
    """比对两组成交指纹，返回 (checked, changed)。"""
    from collections import Counter

    base_c = Counter(baseline)
    cand_c = Counter(candidate)
    checked = sum(base_c.values())
    # 对称差：任一侧多出的成交都算变化
    changed = sum((base_c - cand_c).values()) + sum((cand_c - base_c).values())
    return checked, changed


def detect_lookahead(
    run_fills: RunFillsFn, bars: Sequence[Bar], cut_ratio: float = _LOOKAHEAD_CUT_RATIO,
) -> SignalDiff:
    """截断未来数据后重跑，比对截断点之前的成交是否变化。"""
    bars = list(bars)
    n = len(bars)
    cut_idx = max(2, int(n * cut_ratio))
    cut_idx = min(cut_idx, n - 1)
    cutoff_iso = _bar_time(bars[cut_idx])

    full_fills = run_fills(bars)
    cut_fills = run_fills(bars[:cut_idx])

    base = _fills_before(full_fills, cutoff_iso)
    cand = _fills_before(cut_fills, cutoff_iso)
    checked, changed = _count_changed(base, cand)

    detail = (
        f"截断点 {cutoff_iso}（保留前 {cut_idx}/{n} 根）之前，"
        f"完整数据与截断数据的成交对比：{changed} 处不一致"
    )
    return SignalDiff(checked_signals=checked, changed_signals=changed, detail=detail)


def detect_recursive(
    run_fills: RunFillsFn, bars: Sequence[Bar], startup_candles: list[int],
) -> list[RecursiveDiff]:
    """前端裁剪不同 startup bar 后重跑，比对重叠尾部成交一致性。"""
    bars = list(bars)
    n = len(bars)
    valid = sorted({c for c in startup_candles if 0 < c < n - _MIN_TAIL_BARS})
    if not valid:
        return []

    full_fills = run_fills(bars)
    max_cut = max(valid)
    # 重叠区间：从最大裁剪点之后开始（所有变体都可见的尾部）
    overlap_start_iso = _bar_time(bars[max_cut])
    base_tail = [f for f in _fills_after(full_fills, overlap_start_iso)]

    diffs: list[RecursiveDiff] = []
    for cut in valid:
        variant_fills = run_fills(bars[cut:])
        cand_tail = _fills_after(variant_fills, overlap_start_iso)
        checked, changed = _count_changed(base_tail, cand_tail)
        diffs.append(RecursiveDiff(startup_candle=cut, checked_signals=checked, changed_signals=changed))
    return diffs


_MIN_TAIL_BARS = 10


def _fills_after(fills: list[dict], start_iso: str) -> list[tuple]:
    out: list[tuple] = []
    for f in fills:
        ts = f.get("filled_at")
        if ts is not None and str(ts) >= start_iso:
            out.append(_fingerprint(f))
    return out


def run_bias_check(
    run_fills: RunFillsFn,
    bars: Sequence[Bar],
    startup_candles: list[int] | None = None,
    cut_ratio: float = _LOOKAHEAD_CUT_RATIO,
) -> BiasCheckOutcome:
    """执行前视 + 递归偏差检测，汇总结论。"""
    bars = list(bars)
    startup_candles = startup_candles or DEFAULT_STARTUP_CANDLES

    baseline_fills = run_fills(bars)
    total_signals = len(baseline_fills)

    lookahead = detect_lookahead(run_fills, bars, cut_ratio)
    recursive = detect_recursive(run_fills, bars, startup_candles)

    has_lookahead = lookahead.changed_signals > 0
    has_recursive = any(r.changed_signals > 0 for r in recursive)

    notes: list[str] = []
    if has_lookahead:
        notes.append("⚠️ 检测到前视偏差：策略决策依赖了截断掉的未来数据，回测结果不可信。")
    else:
        notes.append("✅ 未发现前视偏差：截断未来数据后历史成交保持一致。")
    if has_recursive:
        notes.append(
            "⚠️ 检测到递归偏差/起点敏感：不同历史起点下成交发生变化，"
            "可能源于未收敛的递归指标或回测起始日的持仓路径差异，实盘信号可能与回测不一致。"
        )
    elif recursive:
        notes.append("✅ 未发现递归偏差：不同起点长度下重叠区间成交保持一致。")
    else:
        notes.append("ℹ️ 数据长度不足，跳过递归偏差检测。")

    return BiasCheckOutcome(
        has_lookahead_bias=has_lookahead,
        has_recursive_bias=has_recursive,
        total_signals=total_signals,
        lookahead=lookahead,
        recursive=recursive,
        notes=notes,
    )


def _bar_time(bar: Bar) -> str:
    t = bar.time
    return t.isoformat() if hasattr(t, "isoformat") else str(t)
