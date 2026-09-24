"""
动态标的池（Epic E / E5 + M7）

- `core`    规则链：按 成交量/波动/价格/价差/市值/近期表现 顺序链式过滤
- `plugins` 标的池生成插件：按市值排名 / 按指数成分股产出 universe

本模块原先是单文件 `app/data/pairlist.py`，M7 拆成包后在此原样再导出，
既有的 `from app.data.pairlist import ...` 调用点无需改动。
"""

from __future__ import annotations

from app.data.pairlist.core import (
    VALID_KINDS,
    PairlistRule,
    PairMetrics,
    apply_chain,
    build_universe,
    clamp_lookback,
    metrics_to_dict,
)
from app.data.pairlist.plugins import (
    IndexComponentPairList,
    MarketCapPairList,
    run_plugins,
)

__all__ = [
    "VALID_KINDS",
    "IndexComponentPairList",
    "MarketCapPairList",
    "PairMetrics",
    "PairlistRule",
    "apply_chain",
    "build_universe",
    "clamp_lookback",
    "metrics_to_dict",
    "run_plugins",
]
