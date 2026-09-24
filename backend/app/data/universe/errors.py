"""市值/成分股宇宙的错误类型（M7）。"""

from __future__ import annotations


class UniverseError(Exception):
    """宇宙构建通用错误。"""


class IndexNotSupportedError(UniverseError):
    """该指数代码没有可用的成分股数据源。"""


class HistoricalComponentsUnavailableError(UniverseError):
    """
    请求的是历史某日的成分股，但数据源只能给最新成分。

    这里**必须**报错而不是静默返回最新成分：用历史区间回测配今天的成分股
    是典型的幸存者偏差（当年被剔除的股票消失了，只剩活到今天的赢家），
    静默降级会产出看起来很美的假回测。
    """


class ComponentsFetchError(UniverseError):
    """成分股数据源请求失败。"""
