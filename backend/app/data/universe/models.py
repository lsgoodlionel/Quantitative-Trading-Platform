"""市值/成分股宇宙的数据结构（M7）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

_YI = 1e8   # 「亿」换算因子


@dataclass(frozen=True)
class UniverseItem:
    """
    宇宙中的一个标的。

    `as_of` 是**数据快照时间**（ISO-8601），不是请求时间 —— 市值榜单带 6 小时
    Redis 缓存，调用方必须能看出手上这份榜单有多旧，否则无从判断是否可用。
    """

    symbol: str
    market: str
    name: str = ""
    market_cap: float | None = None   # 总市值（本币原始单位）
    rank: int | None = None           # 榜单名次，从 1 开始
    as_of: str = ""

    @property
    def market_cap_yi(self) -> float | None:
        return round(self.market_cap / _YI, 3) if self.market_cap is not None else None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["market_cap_yi"] = self.market_cap_yi
        return data
