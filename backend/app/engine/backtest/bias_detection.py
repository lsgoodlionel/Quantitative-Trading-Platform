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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.data.models import Bar

logger = logging.getLogger(__name__)

# 默认递归检测使用的 startup bar 裁剪数量
DEFAULT_STARTUP_CANDLES = [50, 100, 200]
# 前视检测默认截断比例（保留前 x 的数据）
_LOOKAHEAD_CUT_RATIO = 0.7
#: 未来扰动的缩放倍数。两个方向各来一次 —— 只朝一个方向缩放时，
#: 某些阈值型策略可能恰好不翻转，看起来就像「没有偏差」。
_PERTURB_SCALES: tuple[float, ...] = (0.5, 2.0)
#: 决策到成交隔几根。本引擎是 next-bar 撮合（1）；同 bar 成交的调用方须显式传 0。
_DEFAULT_FILL_DELAY = 1
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


def _decision_fingerprint(fill: dict) -> tuple:
    """**决策**指纹：时间 + 方向 + 数量，**不含价格**。

    扰动法专用。扰动未来 bar 会改变那些 bar 上的成交**价**，
    但不该改变任何**决策** —— 拿含价格的指纹去比，干净策略也会红。
    """
    return (
        str(fill.get("filled_at")),
        str(fill.get("side")).upper(),
        int(fill.get("qty", 0)),
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


def _decisions_upto(fills: list[dict], cutoff_iso: str) -> list[tuple]:
    """返回 filled_at **<=** cutoff 的决策指纹。

    与 `_fills_before` 的严格小于刻意不同 —— 那个边界正是截断法漏掉
    「偷看 1 根」的原因（见 `detect_lookahead` 文档）。
    """
    return [
        _decision_fingerprint(f)
        for f in fills
        if f.get("filled_at") is None or str(f["filled_at"]) <= cutoff_iso
    ]


def _perturb_from(bars: list[Bar], start: int, scale: float) -> list[Bar]:
    """把 `start` 及其之后每根 bar 的价格整体缩放，前面的一根不动。

    只动价格：volume / turnover / trade_count 保持原值 ——
    扰动要模拟的是「未来价格本可以是别的样子」，不是「换了一段行情」。
    """
    from dataclasses import replace

    out = list(bars[:start])
    for bar in bars[start:]:
        out.append(
            replace(
                bar,
                open=bar.open * scale,
                high=bar.high * scale,
                low=bar.low * scale,
                close=bar.close * scale,
                vwap=None if bar.vwap is None else bar.vwap * scale,
            )
        )
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
    run_fills: RunFillsFn,
    bars: Sequence[Bar],
    cut_ratio: float = _LOOKAHEAD_CUT_RATIO,
    decision_to_fill_bars: int = _DEFAULT_FILL_DELAY,
) -> SignalDiff:
    """两种探测叠加：**截断**（数据不存在）+ **扰动**（数据是别的值）。

    ## 为什么光靠截断不够 —— 一个实测出来的盲区

    截断法把数据切到 `cut_idx` 重跑，再比对**严格早于**截断时刻的成交。
    对「偷看 1 根」的策略它**测不出来**：

        t = cut_idx-1 处策略偷看 bar[cut_idx] → 截断后那根不存在 → 决策不同
        但 next-bar 撮合让这笔成交落在 t = cut_idx，
        恰好被 `_fills_before` 的严格小于排除掉。

    实测（400 根，偷看 H 根）：H=1 漏报、H=2 仅 1/97 个信号变化、H=3/5 均漏报。
    灵敏度约等于 H/N —— 单个切点只能看到切点附近那几根。

    ## 扰动法怎么补上

    把 `cut_idx` **及其之后**的价格整体缩放（数据还在，只是变成别的值），
    再比对 `<= cut_idx` 时刻的**决策**（时间/方向/数量，**不含价格**）：

    - 干净策略：`t <= cut_idx-1` 的决策只用到 `bar <= cut_idx-1`（未扰动），
      决策不变。落在 `cut_idx` 的成交价会变，但价格不参与比对。
    - 偷看 1 根：`t = cut_idx-1` 看到的 `bar[cut_idx]` 被扰动了 → 决策变 →
      在 `<= cut_idx` 的窗口里就能看见。

    两个方向各扰动一次（×0.5 / ×2.0）：只朝一个方向缩放时，
    某些阈值型策略可能恰好不翻转。

    ## `decision_to_fill_bars` 为什么必须显式给

    比对窗口的右边界取决于**决策到成交隔几根**，这个不能猜：

    - **1（默认，本引擎）**：next-bar 撮合。窗口取 `<= time[cut_idx]` ——
      落在 `cut_idx` 的那笔成交，其方向与数量是在 `cut_idx-1` 用未扰动数据
      定下的，所以干净策略不会红；而偷看 1 根的策略正好在这里露馅。
    - **0**：同 bar 成交（部分测试桩、以及某些自定义 `run_fills`）。
      窗口必须收到 `<= time[cut_idx-1]` —— 否则 `cut_idx` 那根自己的价格被扰动了，
      干净策略在那一根上的决策**本来就该变**，会被误判成前视。

    猜错这个参数的后果是**静默的**：要么漏报，要么把干净策略判成有偏差。

    ⚠️ **仍然是启发式。** 它抓的是「决策依赖了未来值」，
    抓不到「决策依赖了未来的存在性但数值无关」这类。
    框架层的物理裁剪（`strategy/precompute.py` 的 `IndicatorView`）
    才是防线，这里只是兜底。
    """
    bars = list(bars)
    n = len(bars)
    cut_idx = max(2, int(n * cut_ratio))
    cut_idx = min(cut_idx, n - 1)
    cutoff_iso = _bar_time(bars[cut_idx])

    full_fills = run_fills(bars)

    # ── 截断：数据不存在 ──
    cut_fills = run_fills(bars[:cut_idx])
    trunc_checked, trunc_changed = _count_changed(
        _fills_before(full_fills, cutoff_iso), _fills_before(cut_fills, cutoff_iso)
    )

    # ── 扰动：数据存在但是别的值 ──
    # 右边界随成交延迟平移，理由见函数文档；夹到 [0, cut_idx] 内。
    window_idx = min(max(cut_idx + decision_to_fill_bars - 1, 0), cut_idx)
    window_iso = _bar_time(bars[window_idx])

    base_decisions = _decisions_upto(full_fills, window_iso)
    perturb_checked = 0
    perturb_changed = 0
    parts: list[str] = []
    for scale in _PERTURB_SCALES:
        moved = run_fills(_perturb_from(bars, cut_idx, scale))
        checked, changed = _count_changed(
            base_decisions, _decisions_upto(moved, window_iso)
        )
        perturb_checked += checked
        perturb_changed += changed
        parts.append(f"未来扰动(×{scale}) {changed} 处")

    detail = (
        f"截断点 {cutoff_iso}（保留前 {cut_idx}/{n} 根）："
        f"截断重跑 {trunc_changed} 处不一致（比对 {trunc_checked} 笔）；"
        + "；".join(parts)
        + f" —— 合计 {trunc_changed + perturb_changed} 处不一致"
    )
    return SignalDiff(
        checked_signals=trunc_checked + perturb_checked,
        changed_signals=trunc_changed + perturb_changed,
        detail=detail,
    )


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
    base_tail = list(_fills_after(full_fills, overlap_start_iso))

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
    decision_to_fill_bars: int = _DEFAULT_FILL_DELAY,
) -> BiasCheckOutcome:
    """执行前视 + 递归偏差检测，汇总结论。

    `decision_to_fill_bars` 见 `detect_lookahead` —— **同 bar 成交的
    `run_fills` 必须显式传 0**，否则干净策略会被误判成有前视偏差。
    """
    bars = list(bars)
    startup_candles = startup_candles or DEFAULT_STARTUP_CANDLES

    baseline_fills = run_fills(bars)
    total_signals = len(baseline_fills)

    lookahead = detect_lookahead(run_fills, bars, cut_ratio, decision_to_fill_bars)
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
