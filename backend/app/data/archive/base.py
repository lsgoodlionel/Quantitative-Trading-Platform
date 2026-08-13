"""
本地数据归档 — 抽象层与路径安全（M1）

归档定位：**回测取数的本地缓存**。反复调参时每次都从在线源重新拉取纯属浪费，
归档把已经拉到的 bar 落到本地列式文件，下次直接读。

⚠️ 安全要点：`symbol` 会成为文件路径的一部分，而 symbol 来自 API 入参。
   这里用**白名单**校验（而非黑名单），任何不在白名单内的字符一律拒绝 ——
   `/`、`\\`、`..`、空字节这些经典的路径穿越载荷因此天然被挡在外面。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date as Date

from app.data.models import Bar, Frequency, Market

# 允许出现在文件名中的 symbol 字符：字母/数字/点/下划线/连字符/脱字符/等号
# （覆盖 AAPL、BRK.B、00700、^GSPC、BF=F 等真实代码形态）
#
# 结尾必须用 `\Z` 而不是 `$`：`$` 在 Python 里会匹配到**结尾换行之前**，
# 于是 "AAPL\n" 能骗过白名单，落到文件名里。这是这类校验最经典的漏网姿势。
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._^=-]{1,32}\Z")

# 至少要有一个字母或数字：纯标点的「代码」（"." / "-" / "..."）不是任何市场的标的
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")

# 明确列出的路径穿越载荷 —— 白名单已能拦下，这里单独判断只为给出更准确的错误信息
_TRAVERSAL_TOKENS = ("..", "/", "\\", "\x00")

# Windows 保留设备名：在该平台上，任何目录下的这些名字都会被解释成设备而非文件。
# 项目当前跑在 macOS/Linux，但归档目录可能被挂到 Windows 上读，成本极低故一并挡掉。
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class ArchiveError(Exception):
    """归档层通用错误。"""


class InvalidSymbolError(ArchiveError, ValueError):
    """symbol 不满足文件名安全约束（可能是路径穿越尝试）。"""


def validate_symbol(symbol: str) -> str:
    """
    校验 symbol 可安全用作文件名，返回原值。

    拒绝：非字符串、空串、含 `..` / `/` / `\\` / 空字节、含白名单外字符、超长。
    """
    if not isinstance(symbol, str):
        raise InvalidSymbolError(f"symbol 必须是字符串，收到 {type(symbol).__name__}")
    if not symbol:
        raise InvalidSymbolError("symbol 不能为空")

    for token in _TRAVERSAL_TOKENS:
        if token in symbol:
            shown = repr(token)
            raise InvalidSymbolError(f"symbol {symbol!r} 含非法路径字符 {shown}，拒绝写入归档")

    if not _SYMBOL_PATTERN.match(symbol):
        raise InvalidSymbolError(f"symbol {symbol!r} 含白名单外字符或长度超限（1-32）")
    if not _HAS_ALNUM.search(symbol):
        raise InvalidSymbolError(f"symbol {symbol!r} 不含任何字母或数字，不是有效标的代码")
    if symbol.split(".")[0].upper() in _RESERVED_NAMES:
        raise InvalidSymbolError(f"symbol {symbol!r} 是系统保留名，拒绝用作文件名")
    return symbol


@dataclass(frozen=True)
class ArchiveKey:
    """归档条目的唯一标识：一个 (标的, 市场, 周期) 对应一个文件。"""

    symbol: str
    market: Market
    frequency: Frequency

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if not isinstance(self.market, Market):
            raise ArchiveError(f"market 必须是 Market 枚举，收到 {self.market!r}")
        if not isinstance(self.frequency, Frequency):
            raise ArchiveError(f"frequency 必须是 Frequency 枚举，收到 {self.frequency!r}")

    @property
    def relative_dir(self) -> tuple[str, str]:
        """归档目录的两级相对路径：(market, frequency)。"""
        return self.market.value, self.frequency.value


class ArchiveHandler(ABC):
    """归档读写接口。实现方负责具体的文件格式。"""

    @abstractmethod
    def read(self, key: ArchiveKey, start: Date, end: Date) -> list[Bar]:
        """读取 [start, end] 闭区间内的 bar（按时间升序）；无归档返回空列表。"""

    @abstractmethod
    def write(self, key: ArchiveKey, bars: list[Bar]) -> int:
        """写入 bar（与已有数据按时间合并去重），返回**实际新增**的条数。"""

    @abstractmethod
    def list_keys(self) -> list[ArchiveKey]:
        """列出全部已归档条目。"""

    @abstractmethod
    def coverage(self, key: ArchiveKey) -> tuple[Date, Date] | None:
        """返回归档覆盖的 (首日, 末日)；空归档返回 None。"""

    @abstractmethod
    def delete(self, key: ArchiveKey) -> bool:
        """删除归档文件；文件不存在返回 False。"""
