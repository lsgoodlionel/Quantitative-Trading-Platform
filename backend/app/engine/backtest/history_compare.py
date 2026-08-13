"""
回测历史对比视图（V3 · H5）

把多条历史记录的净值曲线对齐到同一条时间轴上，供前端叠加绘制；同时并列输出
关键指标表格。纯函数、无 IO。

对齐方式：取所有曲线时间标签的**并集**作为公共时间轴，每条曲线在该轴上做
前向填充（首个观测之前留 None，表示该回测尚未开始，前端断线即可）。
曲线值统一归一化为「相对初始资金的倍数」，不同初始资金的回测才可比。
"""

from __future__ import annotations

from typing import Any

# 公共时间轴最多保留的点数（首尾必留），避免多条长曲线叠加后响应过大
MAX_AXIS_POINTS = 1000

# 指标表格并列展示的字段（顺序即前端列顺序）
COMPARE_METRIC_KEYS: tuple[str, ...] = (
    "total_return_pct",
    "annual_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_pct",
    "win_rate_pct",
    "profit_factor",
    "total_trades",
)

_EPS = 1e-12


def _thin(axis: list[str], max_points: int) -> list[str]:
    """等步长抽稀时间轴，首尾必留。"""
    if len(axis) <= max_points:
        return axis
    stride = len(axis) / (max_points - 1)
    picked = [axis[min(int(i * stride), len(axis) - 1)] for i in range(max_points - 1)]
    picked.append(axis[-1])
    # 抽稀可能撞出重复项，去重后保持顺序
    seen: set[str] = set()
    return [t for t in picked if not (t in seen or seen.add(t))]


def _normalized_series(curve: list[dict], initial_cash: float, axis: list[str]) -> list[float | None]:
    """把一条曲线前向填充到公共时间轴，值归一化为初始资金的倍数。"""
    base = initial_cash if abs(initial_cash) > _EPS else 1.0
    by_time = {str(p.get("time")): float(p.get("value", 0.0)) for p in curve}
    ordered = sorted(by_time)
    out: list[float | None] = []
    cursor = 0
    last: float | None = None
    for stamp in axis:
        while cursor < len(ordered) and ordered[cursor] <= stamp:
            last = by_time[ordered[cursor]]
            cursor += 1
        out.append(round(last / base, 6) if last is not None else None)
    return out


def build_comparison(records: list[Any], max_axis_points: int = MAX_AXIS_POINTS) -> dict:
    """
    构造对比视图数据。

    Args:
        records: `BacktestHistoryRecord` 列表（需含 equity_curve）。
        max_axis_points: 公共时间轴最大点数。

    Returns:
        {"axis": [...], "series": [{id, name, ..., values}], "metrics": {...}}
        —— `series[*].values` 与 `axis` 等长且逐点对齐。
    """
    if not records:
        return {"axis": [], "series": [], "metrics": {"keys": list(COMPARE_METRIC_KEYS), "rows": []}}

    stamps: set[str] = set()
    for record in records:
        stamps.update(str(p.get("time")) for p in record.equity_curve)
    axis = _thin(sorted(stamps), max_axis_points)

    series = [
        {
            "id": r.id,
            "name": r.name,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "initial_cash": r.initial_cash,
            "values": _normalized_series(r.equity_curve, r.initial_cash, axis),
        }
        for r in records
    ]
    rows = [
        {
            "id": r.id,
            "name": r.name,
            "values": {k: r.metrics.get(k) for k in COMPARE_METRIC_KEYS},
        }
        for r in records
    ]
    return {"axis": axis, "series": series, "metrics": {"keys": list(COMPARE_METRIC_KEYS), "rows": rows}}
