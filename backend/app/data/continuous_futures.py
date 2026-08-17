"""
连续合约拼接（V4 Wave F-b / O5）

期货合约有到期日。一个品种的「历史价格序列」其实是一串首尾相接的不同合约，
换月处会留下跳空 —— 那不是行情，是换了个标的。直接拿原始拼接序列跑策略，
每次换月都会触发一次假信号。

本模块提供两种口径，**不能混用**：

┌──────────────┬──────────────────────────┬──────────────────────────────────┐
│ method       │ 保证什么                  │ 代价                              │
├──────────────┼──────────────────────────┼──────────────────────────────────┤
│ "raw"        │ 每根 bar 都是**当时真实   │ 换月处有跳空，趋势/均线类策略会   │
│              │ 的成交价**                │ 被假信号打穿                      │
├──────────────┼──────────────────────────┼──────────────────────────────────┤
│ "back_adjust"│ 换月处**价差连续**，最新  │ 历史价格**不是当时的真实成交价**  │
│              │ 合约保留真实价格           │ （整段被平移），且可能为负        │
└──────────────┴──────────────────────────┴──────────────────────────────────┘

⚠️ **这与 `data/adjustments.py` 的股票复权是两套东西，口径不同，不要混用，
   也不要画在同一张图上。**
   - 股票复权是**乘法**的（拆股 1:4 → 历史价 ×0.25），比例不变、收益率不变；
   - 期货回填是**加法**的（Panama 法），价差不变、**收益率被扭曲**。
   两者唯一的共同点是「以最新价为基准调整历史价」这条基准选择
   （见 `adjustments.py` 模块说明：最新价 = 真实价）。

⚠️ **本模块不做主力合约自动判定。** 成交量/持仓量的换月规则各交易所不同
   （中金所看持仓量、CME 看成交量、还有强制换月日），判错一次整条价格序列都是错的。
   合约顺序由调用方给出。

### 为什么加法而不是乘法

契约把 back_adjust 描述成「保证价差连续（可用于计算收益）」。这两句在期货语境下
是**互斥**的，只能选一个：
  - 加法回填 → 绝对价差连续（跨月价差、点数止损、期现基差都对），但百分比收益率失真；
  - 乘法回填 → 百分比收益率连续，但绝对价差失真，且换月比例为负时无定义。
验收条款明确要求「衔接处**无跳空**」，那是**价差**口径，故本实现取加法。
**用回填序列算百分比收益率是错的**，需要收益率时请用 `raw` 分段计算再拼接。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from app.data.models import Bar
from app.data.models.contract import ContractSpec

logger = logging.getLogger(__name__)

StitchMethod = Literal["raw", "back_adjust"]

VALID_METHODS: frozenset[str] = frozenset({"raw", "back_adjust"})

#: 偏移量小于该阈值视作 0，不重建 Bar（浮点噪声不值得产生新对象）
_OFFSET_EPS = 1e-12

ContractSegment = tuple[ContractSpec, list[Bar]]


def stitch_continuous(
    contracts: Sequence[tuple[ContractSpec, list[Bar]]],
    method: StitchMethod = "back_adjust",
) -> list[Bar]:
    """
    把一串按时间先后排好的月份合约拼成一条连续序列。

    参数
    ----
    contracts : 有序的 (合约元数据, 该合约的 bar 列表) 序列。
        **顺序即拼接顺序，由调用方负责**（见模块说明：本模块不做主力判定）。
        每段内部的 bar 会按时间升序排序；段与段之间不做时间重叠检查
        —— 真实换月常有重叠交易日，是否重叠由调用方的换月规则决定。
    method : "raw" 原样拼接 / "back_adjust" 后向回填（默认）。

    返回
    ----
    新的 Bar 列表（`Bar` 是 frozen dataclass，绝不就地修改入参）。

    语义差异（**这是本函数最重要的一段，改动前请先读模块说明**）
    ----
    - ``raw``：逐段原样相接。每根 bar 的 OHLC 都是**当时真实的成交价**，
      因此换月处**必然有跳空**，这是特性不是 bug。
    - ``back_adjust``：最后一段（最新合约）**保持原值**，
      其前每一段整体加上一个常数偏移，使
      ``调整后[段k].最后一根.close == 调整后[段k+1].第一根.open``。
      于是**换月处无跳空、价差连续**，但**历史价格不再是当时的真实成交价**，
      而且长历史 + 大幅升水时**调整后价格可能为负**（Panama 法的已知代价，
      发生时会记 warning）。

    不做的事
    ----
    - 不改 ``symbol``：每根 bar 仍带原合约代码，方便回溯它来自哪个月份。
      需要统一代号的调用方自行 ``dataclasses.replace(bar, symbol=...)``。
    - 不改 ``asset_class``：单合约输入必须原样返回（验收条款），
      因此不能拿 spec 的 asset_class 去覆写 bar 的标签。
    - 不改 ``volume`` / ``trade_count``：换月不改变成交手数。
    - 不改 ``turnover``：成交额 = 价 × 量，加法平移下它无法自洽。
      **回填序列的 turnover 与 OHLC 不在同一口径**，不要用它反推均价。
      （``vwap`` 是纯价格，会跟随平移。）
    """
    if method not in VALID_METHODS:
        raise ValueError(f"未知拼接口径 {method!r}，可选 {sorted(VALID_METHODS)}")

    segments = _normalized_segments(contracts)
    if not segments:
        return []
    if method == "raw" or len(segments) == 1:
        return [bar for _, bars in segments for bar in bars]

    return _back_adjust(segments)


# ── 内部 ──────────────────────────────────────────────────────

def _normalized_segments(
    contracts: Sequence[tuple[ContractSpec, list[Bar]]],
) -> list[ContractSegment]:
    """校验入参形状，丢掉空段，段内按时间升序。"""
    segments: list[ContractSegment] = []
    for index, item in enumerate(contracts):
        spec, bars = _unpack(index, item)
        if not bars:
            logger.debug("合约 %s 没有 bar，跳过", spec.symbol)
            continue
        segments.append((spec, sorted(bars, key=lambda b: b.time)))
    return segments


def _unpack(index: int, item: object) -> tuple[ContractSpec, list[Bar]]:
    if not isinstance(item, tuple) or len(item) != 2:
        raise TypeError(f"contracts[{index}] 必须是 (ContractSpec, list[Bar]) 二元组，收到 {item!r}")
    spec, bars = item
    if not isinstance(spec, ContractSpec):
        raise TypeError(f"contracts[{index}][0] 必须是 ContractSpec，收到 {type(spec).__name__}")
    if not isinstance(bars, (list, tuple)):
        raise TypeError(f"contracts[{index}][1] 必须是 bar 列表，收到 {type(bars).__name__}")
    return spec, list(bars)


def _back_adjust(segments: list[ContractSegment]) -> list[Bar]:
    """
    从最新一段往回累加偏移。

    段 n（最新）偏移 0；对每个换月点 k → k+1：
        gap_k   = 段(k+1).首根.open - 段k.末根.close
        offset_k = offset_(k+1) + gap_k
    代入即得 段k.末根.close + offset_k == 段(k+1).首根.open + offset_(k+1)，
    也就是「衔接处无跳空」。
    """
    offsets = _cumulative_offsets(segments)
    return [
        _shift(bar, offset)
        for (_, bars), offset in zip(segments, offsets, strict=True)
        for bar in bars
    ]


def _cumulative_offsets(segments: list[ContractSegment]) -> list[float]:
    offsets = [0.0] * len(segments)
    for k in range(len(segments) - 2, -1, -1):
        gap = segments[k + 1][1][0].open - segments[k][1][-1].close
        offsets[k] = offsets[k + 1] + gap
    _warn_if_negative(segments, offsets)
    return offsets


def _warn_if_negative(segments: list[ContractSegment], offsets: list[float]) -> None:
    """Panama 法的已知代价：长历史 + 持续升水会把早期价格推到 0 以下。"""
    for (spec, bars), offset in zip(segments, offsets, strict=True):
        lowest = min(bar.low for bar in bars) + offset
        if lowest <= 0:
            logger.warning(
                "连续合约回填后 %s 的最低价为 %.4f（≤0）：这是加法回填的已知代价，"
                "该段价格不可用于百分比收益或对数计算",
                spec.symbol, lowest,
            )


def _shift(bar: Bar, offset: float) -> Bar:
    """整根 bar 平移一个常数。加法平移不改变 high/low 的相对次序，故不会破坏 Bar 校验。"""
    if abs(offset) <= _OFFSET_EPS:
        return bar
    return replace(
        bar,
        open=bar.open + offset,
        high=bar.high + offset,
        low=bar.low + offset,
        close=bar.close + offset,
        vwap=bar.vwap + offset if bar.vwap is not None else None,
        # volume / trade_count / turnover 刻意不动，理由见 stitch_continuous 文档
    )
