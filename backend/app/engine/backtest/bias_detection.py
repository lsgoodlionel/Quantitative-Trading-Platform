"""
前视 / 递归偏差检测（C3）

在不修改策略代码的前提下，通过「重跑对比」检测两类隐蔽偏差：

1. 前视偏差 (Look-ahead bias)
   策略是否偷看了未来数据。用**两类探针**交叉验证（见 `detect_lookahead`）：
   - 截断探针：把数据在某个时间点截断后重跑，比较截断点之前的成交。
     参考: refs/freqtrade/freqtrade/optimize/analysis/lookahead.py
   - 未来扰动探针：bar 根数不变，只把截断点及之后的**价格改写成极端常数**后重跑。
     它补的是截断探针结构上够不到的那一格 —— 只偷看 1 根的策略（见下）。

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
from dataclasses import dataclass, field, replace

from app.data.models import Bar

logger = logging.getLogger(__name__)

# 默认递归检测使用的 startup bar 裁剪数量
DEFAULT_STARTUP_CANDLES = [50, 100, 200]
# 前视检测默认截断比例（保留前 x 的数据）
_LOOKAHEAD_CUT_RATIO = 0.7
# 成交指纹保留的小数位（价格）
_PRICE_PRECISION = 4
#: 未来扰动探针的改写倍数（相对截断点前一根的 close）。
#:
#: 必须**上下各来一次**：扰动后的未来是一个常数，策略对它的反应是单向的 ——
#: 只往下砸，一个「知道要涨才买」的策略在扰动运行里全程空仓，而它在原始数据上
#: 本来也可能空仓，两边一样就观察不到差异（实测 peek=5 被漏掉）；只往上抬则
#: 漏掉反向的那一半。两个方向各跑一次，两边的盲区互补。
_PERTURB_FACTORS = (0.5, 2.0)
#: 扰动 bar 的高低点相对改写价的外扩比例（保证 high ≥ close ≥ low 仍成立）。
_PERTURB_RANGE_PCT = 0.01

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
    """
    在截断点上做两类探针，任一探针报差异即判定前视偏差。

    **为什么光靠截断探针不够**（V3 Wave E-a 实测）：引擎是 next-bar 撮合，
    `filled_at == bars[k].time` 的成交来自第 `k-1` 根上的决策。截断探针只能比
    `filled_at < bars[cut].time` 的成交 —— 边界上最后一笔可比成交，其决策发生在
    第 `cut-2` 根。而截断运行里第 `cut-2` 根**照样看得见**第 `cut-1` 根，所以
    「只偷看 1 根」的策略在两次运行里给出完全相同的成交序列，检测不到。
    偷看 2/3/5 根能被抓到，恰好 1 根抓不到 —— 最隐蔽的那一档正好落在盲区里。

    边界往后挪一根（改成 `<=`）不能修：截断运行里根本没有第 `cut` 根，
    完整运行在那一根上的成交无从对应，**干净策略也会被判成有偏差**
    （实测：每根 bar 都换手的干净策略稳定误报 1 处）。多试几个截断比例同样无效 ——
    盲区在每个比例上都成立：任何一次运行的最后一根决策都永远等不到它的成交。

    未来扰动探针换一条路：**bar 根数一根不减**，只把截断点及之后的价格改写成
    极端常数。于是第 `cut-1` 根的决策（偷看到第 `cut` 根）会变，而它的成交落在
    第 `cut` 根上、用的是**未被改写的开盘价**，两次运行都观察得到 —— 盲区补上。
    干净策略在窗口内只读得到截断点之前的原始数据，一定不会被误报。

    残留盲区（已知、可接受）：只偷看下一根的 `open` / `volume`。这两个字段在
    第 `cut` 根上被撮合本身使用，改写它们会连带改掉边界成交价，反而制造误报；
    偷看 ≥2 根的同类写法仍由截断探针兜住。
    """
    bars = list(bars)
    n = len(bars)
    cut_idx = max(2, int(n * cut_ratio))
    cut_idx = min(cut_idx, n - 1)

    full_fills = run_fills(bars)

    probes = [_truncation_probe(run_fills, bars, cut_idx, full_fills)]
    probes += [
        _perturbation_probe(run_fills, bars, cut_idx, full_fills, factor)
        for factor in _PERTURB_FACTORS
    ]

    checked = sum(p.checked for p in probes)
    changed = sum(p.changed for p in probes)
    detail = (
        f"截断点 {_bar_time(bars[cut_idx])}（保留前 {cut_idx}/{n} 根）："
        + "；".join(p.summary for p in probes)
        + f" —— 合计 {changed} 处不一致"
    )
    return SignalDiff(checked_signals=checked, changed_signals=changed, detail=detail)


@dataclass
class _ProbeResult:
    checked: int
    changed: int
    summary: str


def _truncation_probe(
    run_fills: RunFillsFn, bars: list[Bar], cut_idx: int, full_fills: list[dict],
) -> _ProbeResult:
    """截断未来数据后重跑，比对截断点**之前**的成交。异常按原样上抛。"""
    cutoff_iso = _bar_time(bars[cut_idx])
    base = _fills_before(full_fills, cutoff_iso)
    cand = _fills_before(run_fills(bars[:cut_idx]), cutoff_iso)
    checked, changed = _count_changed(base, cand)
    return _ProbeResult(checked, changed, f"截断重跑 {changed} 处不一致（比对 {checked} 笔）")


def _perturbation_probe(
    run_fills: RunFillsFn,
    bars: list[Bar],
    cut_idx: int,
    full_fills: list[dict],
    factor: float,
) -> _ProbeResult:
    """
    改写未来价格后重跑，比对**含截断点那根在内**的成交。

    比对窗口比截断探针多一根，正是这一根让「只偷看 1 根」现形。
    """
    # cut_idx 之后没有 bar 时不设上界：此时唯一被改写的就是最后一根，
    # 它之后不可能再有成交，全量比对与设界等价。
    cutoff_iso = _bar_time(bars[cut_idx + 1]) if cut_idx + 1 < len(bars) else None
    label = f"未来扰动(×{factor})"
    try:
        cand_fills = run_fills(_perturb_future(bars, cut_idx, factor))
    except Exception as exc:  # noqa: BLE001 — 探针失败不能把整份检测拖垮
        logger.warning("未来扰动探针(×%s)执行失败，本次前视检测降级为仅截断探针", factor,
                       exc_info=True)
        return _ProbeResult(0, 0, f"{label} 执行失败（{type(exc).__name__}: {exc}），结论不可用")

    base = _fills_before(full_fills, cutoff_iso)
    cand = _fills_before(cand_fills, cutoff_iso)
    checked, changed = _count_changed(base, cand)
    return _ProbeResult(checked, changed, f"{label} {changed} 处不一致（比对 {checked} 笔）")


def _perturb_future(bars: list[Bar], cut_idx: int, factor: float) -> list[Bar]:
    """
    返回新序列：前 `cut_idx` 根原样，其后价格改写为 `前一根 close × factor`。

    刻意**保留第 `cut_idx` 根的 open 与全部 volume**：next-bar 撮合就是拿这两个
    字段成交的，动了它们边界那笔成交价会跟着变，干净策略也会被判有偏差。
    改写成常数（而非等比缩放）是为了同时打掉「未来两根之间的涨跌方向」——
    等比缩放会原样保留未来 bar 之间的相对大小，`close[i+2] > close[i+1]`
    这类写法就会溜过去。
    """
    anchor = bars[cut_idx - 1].close * factor
    high = anchor * (1 + _PERTURB_RANGE_PCT)
    low = anchor * (1 - _PERTURB_RANGE_PCT)

    perturbed: list[Bar] = list(bars[:cut_idx])
    for offset, bar in enumerate(bars[cut_idx:]):
        perturbed.append(
            replace(
                bar,
                # 第 cut_idx 根（offset == 0）的 open 留给撮合，之后的一并改写，
                # 好让偷看 ≥2 根 open 的写法也现形。
                open=bar.open if offset == 0 else anchor,
                high=max(bar.high, high),
                low=min(bar.low, low),
                close=anchor,
                vwap=anchor if bar.vwap is not None else None,
            )
        )
    return perturbed


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
        notes.append("✅ 未发现前视偏差：截断未来数据、改写未来价格后历史成交均保持一致。")
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
