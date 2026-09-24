"""拒绝信号归类与汇总（Wave N-a / N4.2）

设计动机取自 freqtrade 的 `generate_rejected_signals`（**GPL-3.0：只读设计、
不复制代码**），实现为本项目独立编写。

**要解决的问题**：策略想下单但被拒（现金不足、持仓不足、A股 T+1、交易控制器
拦截、风险闸门否决）目前只写进 `Order.reject_reason`，没有任何汇总。用户看到的
是「策略怎么没开仓」，却不知道是被什么拦的。

**为什么必须归一化**：`reject_reason` 是带具体数值的自由文本，例如::

    A股T+1限制: 可卖 0 股，请求卖 100 股
    A股T+1限制: 可卖 300 股，请求卖 1200 股

按原文分组会得到几百个只出现一次的类别，等于没分组。这里采用
「**登记表优先 + 通用兜底**」两级归一化：

1. `_PATTERNS` 是一张有序登记表，把项目内已知的拒单来源映射到稳定的
   `reject_code`（`INSUFFICIENT_CASH` 等），并保留有意义的子标签
   （如触发的控制器名）。
2. 未登记的自由文本（策略自定义闸门、第三方控制器）走 `_generic_label`：
   剥掉数字、日期、时间、引号内容后再分组，同类不同数值仍能合并。

登记表随 broker 的拒单文案演进：新增拒单点时在这里补一条即可，
`REJECT_OTHER` 的兜底保证漏登记也不会炸，只是分类粒度变粗。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

#: 归一化后的类别码（对外常量，前端与测试直接引用，不要写字面量）
REJECT_INVALID_SPEC = "INVALID_ORDER_SPEC"
REJECT_T_PLUS_1 = "T_PLUS_1"
REJECT_INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
REJECT_INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
REJECT_PRICE_LIMIT = "PRICE_LIMIT"
REJECT_VOLUME_LIMIT = "VOLUME_LIMIT"
REJECT_TRADING_CONTROL = "TRADING_CONTROL"
REJECT_MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
REJECT_ORDER_TIMEOUT = "ORDER_TIMEOUT"
REJECT_ORDER_EXPIRED = "ORDER_EXPIRED"
REJECT_OTHER = "OTHER"

#: 单个类别最多列出多少个涉及标的（避免宽基组合把返回体撑爆）
_MAX_SYMBOLS_LISTED = 10

#: 通用兜底标签的最大长度
_MAX_LABEL_LEN = 80


@dataclass(frozen=True)
class RejectCategory:
    """一条拒单文案归一化后的类别。"""

    code: str
    label: str


class _RejectedOrder(Protocol):
    """`rejected_signal_summary` 只依赖这几个字段，便于测试与后续复用。"""

    symbol: str
    reject_reason: str | None


# ── 登记表 ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Pattern:
    code: str
    label: str
    regex: re.Pattern[str]
    #: 命中后从正则捕获组里取子标签（如控制器名），None 表示用固定 label
    detail_group: str | None = None


def _p(code: str, label: str, pattern: str, detail_group: str | None = None) -> _Pattern:
    return _Pattern(code, label, re.compile(pattern), detail_group)


#: **有序**登记表：先命中者胜。顺序上把更具体的写在更泛的前面。
_PATTERNS: tuple[_Pattern, ...] = (
    # 交易控制器统一走 `[控制器名] 说明`（controls/base.py::as_reject_reason）
    _p(REJECT_TRADING_CONTROL, "交易控制器拦截", r"^\[(?P<name>[^\]]+)\]", "name"),
    _p(REJECT_T_PLUS_1, "A股 T+1 限制", r"T\+1"),
    _p(REJECT_MAX_OPEN_POSITIONS, "同时持仓数量上限", r"同时持仓上限"),
    _p(REJECT_INSUFFICIENT_CASH, "现金不足", r"insufficient cash|现金不足"),
    _p(
        REJECT_INSUFFICIENT_POSITION,
        "持仓不足",
        r"insufficient position|持仓不足",
    ),
    _p(REJECT_PRICE_LIMIT, "涨跌停无法成交", r"涨停无法成交|跌停无法成交"),
    _p(REJECT_VOLUME_LIMIT, "成交量约束", r"成交量约束"),
    _p(REJECT_ORDER_TIMEOUT, "挂单超时撤单", r"挂单超时"),
    _p(REJECT_ORDER_EXPIRED, "订单过期撤单", r"订单过期未成交"),
    _p(
        REJECT_INVALID_SPEC,
        "订单参数非法",
        r"qty must be positive|必须提供|必须为正数|必须落在",
    ),
)

#: 通用兜底的剥离规则：数值 / 日期 / 时间 / 引号内容 → 占位符
_SCRUB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?"), "<时间>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<日期>"),
    (re.compile(r"[-+]?\d[\d,]*\.?\d*%?"), "<数值>"),
    (re.compile(r"['\"「」《》]"), ""),
    (re.compile(r"\s+"), " "),
)


def classify_reject_reason(reason: str | None) -> RejectCategory:
    """把一条自由文本拒单原因归一化成稳定类别。"""
    text = (reason or "").strip()
    if not text:
        return RejectCategory(REJECT_OTHER, "未说明原因")

    for pattern in _PATTERNS:
        match = pattern.regex.search(text)
        if match is None:
            continue
        if pattern.detail_group is None:
            return RejectCategory(pattern.code, pattern.label)
        detail = match.group(pattern.detail_group)
        return RejectCategory(pattern.code, f"{pattern.label}: {detail}")

    return RejectCategory(REJECT_OTHER, _generic_label(text))


def _generic_label(text: str) -> str:
    """未登记文案的兜底归一化：剥掉数值/日期/时间后作为标签。"""
    scrubbed = text
    for regex, replacement in _SCRUB_RULES:
        scrubbed = regex.sub(replacement, scrubbed)
    scrubbed = scrubbed.strip()
    if len(scrubbed) > _MAX_LABEL_LEN:
        return scrubbed[:_MAX_LABEL_LEN] + "…"
    return scrubbed or "未说明原因"


# ── 汇总 ─────────────────────────────────────────────────────────

@dataclass
class _Bucket:
    """一个类别的累计状态（内部可变，出口冻结成 dict）。"""

    code: str
    label: str
    count: int = 0
    symbols: set[str] | None = None
    first_time: datetime | None = None
    last_time: datetime | None = None
    sample_reason: str = ""


def rejected_signal_summary(orders: Sequence[_RejectedOrder]) -> list[dict]:
    """按归一化类别汇总拒绝信号：次数、涉及标的数、首末时间。

    只统计 `reject_reason` 非空的订单；按次数降序排列。
    """
    buckets: dict[str, _Bucket] = {}
    for order in orders:
        reason = getattr(order, "reject_reason", None)
        if not reason:
            continue
        category = classify_reject_reason(reason)
        _accumulate(buckets, category, order, reason)

    total = sum(b.count for b in buckets.values())
    rows = [_bucket_row(b, total) for b in buckets.values()]
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def _accumulate(
    buckets: dict[str, _Bucket],
    category: RejectCategory,
    order: _RejectedOrder,
    reason: str,
) -> None:
    key = f"{category.code}|{category.label}"
    bucket = buckets.get(key)
    if bucket is None:
        bucket = _Bucket(
            code=category.code, label=category.label, symbols=set(), sample_reason=reason
        )
        buckets[key] = bucket

    bucket.count += 1
    symbol = getattr(order, "symbol", "") or ""
    if symbol and bucket.symbols is not None:
        bucket.symbols.add(symbol)

    at = _order_time(order)
    if at is not None:
        if bucket.first_time is None or at < bucket.first_time:
            bucket.first_time = at
        if bucket.last_time is None or at > bucket.last_time:
            bucket.last_time = at


def _order_time(order: _RejectedOrder) -> datetime | None:
    """优先用模拟时钟 `rejected_at`；它是 broker 在拒单时刻写入的 bar 时间。

    退回 `created_at` 只是为了兼容手工构造的订单 —— 那是**墙钟**，
    在回测里没有意义，所以不作为首选。
    """
    at = getattr(order, "rejected_at", None)
    if isinstance(at, datetime):
        return at
    created = getattr(order, "created_at", None)
    return created if isinstance(created, datetime) else None


def _bucket_row(bucket: _Bucket, total: int) -> dict:
    symbols = sorted(bucket.symbols or set())
    return {
        "code": bucket.code,
        "label": bucket.label,
        "count": bucket.count,
        "share_pct": round(bucket.count / total * 100, 4) if total else 0.0,
        "symbol_count": len(symbols),
        "symbols": symbols[:_MAX_SYMBOLS_LISTED],
        "symbols_truncated": len(symbols) > _MAX_SYMBOLS_LISTED,
        "first_time": _fmt(bucket.first_time),
        "last_time": _fmt(bucket.last_time),
        "sample_reason": bucket.sample_reason,
    }


def _fmt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_rejected_signal_section(
    orders: Sequence[_RejectedOrder], *, overflow: int = 0
) -> dict | None:
    """组装 N4.2 的可空 section；没有任何拒单时返回 None。

    `overflow` 是 broker 因台账上限而**未留存**的拒单数 —— 必须如实透出，
    否则用户会把一个被截断的样本当成全量。
    """
    rows = rejected_signal_summary(orders)
    if not rows and overflow <= 0:
        return None
    return {
        "total_rejected": sum(r["count"] for r in rows) + overflow,
        "recorded": sum(r["count"] for r in rows),
        "truncated": overflow > 0,
        "dropped": overflow,
        "by_reason": rows,
    }


def reject_category_codes() -> Iterable[str]:
    """全部登记在案的类别码（前端做筛选器用；不含兜底 OTHER）。"""
    return tuple(dict.fromkeys(p.code for p in _PATTERNS))
