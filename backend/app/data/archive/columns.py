"""
Bar ↔ 列式字典 的互转（M1）

归档文件是列式的：每个字段一列。这里集中管理「哪些列、什么顺序、怎么还原」，
让 parquet / npz 两种后端共用同一套序列化口径。

时间列刻意存 **ISO-8601 字符串**而非 epoch 数值：
带时区的 datetime 走 epoch 会丢失原始 tzinfo，回读后与写入前不再逐字段相等，
而验收要求「写入 → 读回，bar 逐笔一致」。字符串往返是无损的。

`asset_class`（V4 Wave F-b）同样是**字符串列**，只能追加在列尾：
既有归档文件里没有这一列，读回时 `_load` 会直接跳过它，
`_build_bar` 便回落到默认的 EQUITY —— 与「不写 asset_class 就是股票」的语义一致。
往中间插列会让老文件的列错位。
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.data.archive.base import ArchiveError, ArchiveKey
from app.data.models import Bar
from app.data.models.asset_class import DEFAULT_ASSET_CLASS, AssetClass

logger = logging.getLogger(__name__)

# 列顺序即文件内的字段顺序，改动会破坏已有归档的兼容性。
# 新增列**只能追加到末尾**（见模块说明）。
COLUMNS: tuple[str, ...] = (
    "time", "open", "high", "low", "close",
    "volume", "vwap", "turnover", "trade_count",
    "asset_class",
)

# 字符串列：不走 `_cell` 的数值转换，后端也要按字符串数组落盘（见 npz.py）
STRING_COLUMNS = frozenset({"time", "asset_class"})

# 可为空的数值列：读回时 NaN/空串 → None
_NULLABLE = frozenset({"vwap", "turnover", "trade_count"})
_INT_COLUMNS = frozenset({"volume", "trade_count"})


def bars_to_columns(bars: list[Bar]) -> dict[str, list]:
    """把 bar 列表转成列式字典（不含 symbol/market/frequency —— 它们由 key 决定）。"""
    return {
        "time": [b.time.isoformat() for b in bars],
        "open": [float(b.open) for b in bars],
        "high": [float(b.high) for b in bars],
        "low": [float(b.low) for b in bars],
        "close": [float(b.close) for b in bars],
        "volume": [int(b.volume) for b in bars],
        "vwap": [b.vwap for b in bars],
        "turnover": [b.turnover for b in bars],
        "trade_count": [b.trade_count for b in bars],
        "asset_class": [b.asset_class.value for b in bars],
    }


def _cell(value: object, column: str) -> float | int | None:
    """把后端读回的单元格还原为 Python 值；缺失值统一为 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        if not value or value == "nan":
            return None
        value = float(value)
    number = float(value)
    if number != number:  # NaN
        return None if column in _NULLABLE else 0.0
    return int(number) if column in _INT_COLUMNS else number


def columns_to_bars(key: ArchiveKey, data: dict[str, list]) -> list[Bar]:
    """把列式字典还原成 bar 列表（按时间升序）。"""
    times = list(data.get("time", []))
    if not times:
        return []

    _assert_aligned(data, len(times))
    bars = [_build_bar(key, data, i, times[i]) for i in range(len(times))]
    return sorted(bars, key=lambda b: b.time)


def _parse_asset_class(raw: object) -> AssetClass:
    """
    还原资产类别；缺列/空值一律回落到 EQUITY。

    这里刻意**不**对未知取值抛错：归档文件可能由更新版本写入（多出一个枚举值），
    老版本读到它时应该退回默认值继续跑，而不是让整份归档变得不可读。
    """
    if raw is None:
        return DEFAULT_ASSET_CLASS
    text = str(raw).strip()
    if not text or text == "nan":
        return DEFAULT_ASSET_CLASS
    try:
        return AssetClass(text)
    except ValueError:
        logger.warning("归档 asset_class 列含未知取值 %r，按默认 %s 处理",
                       text, DEFAULT_ASSET_CLASS.value)
        return DEFAULT_ASSET_CLASS


def _assert_aligned(data: dict[str, list], length: int) -> None:
    for column in COLUMNS:
        values = data.get(column)
        if values is not None and len(values) != length:
            raise ArchiveError(f"归档列 {column} 长度 {len(values)} 与 time 列 {length} 不一致")


def _build_bar(key: ArchiveKey, data: dict[str, list], i: int, raw_time: object) -> Bar:
    def get(column: str) -> float | int | None:
        values = data.get(column)
        return _cell(values[i], column) if values is not None else None

    def get_raw(column: str) -> object:
        values = data.get(column)
        return values[i] if values is not None else None

    return Bar(
        time=_parse_time(raw_time),
        symbol=key.symbol,
        market=key.market,
        frequency=key.frequency,
        open=float(get("open") or 0.0),
        high=float(get("high") or 0.0),
        low=float(get("low") or 0.0),
        close=float(get("close") or 0.0),
        volume=int(get("volume") or 0),
        vwap=get("vwap"),
        turnover=get("turnover"),
        trade_count=get("trade_count"),
        asset_class=_parse_asset_class(get_raw("asset_class")),
    )


def _parse_time(raw: object) -> datetime:
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ArchiveError(f"归档时间列无法解析: {raw!r}") from exc
