"""容量 · 换手 · 杠杆（Wave N-a / N2）

参考 Lean（Apache-2.0, Copyright QuantConnect Corporation）
`Report/ReportElements/{EstimatedCapacity,Turnover,LeverageUtilization}` 的报告口径，
按本项目的数据结构重写。

**数据来源固定为 K-c 的 `PortfolioDailyResult`**（已含 `turnover` 与
`contracts[*].end_pos`），不从 fills 重算 —— 两套算法必然漂移。

三个口径：

- 换手率  `turnover / 当日净值`
- 杠杆    `Σ|持仓市值| / 净值` —— **绝对值加总**。带符号加总会让多空对冲的组合
  算出「零杠杆」这种荒谬结论。
- 容量    在「单标的单日成交额不超过其 ADV 的 `max_adv_share`」约束下，
  策略最多能管多少钱。**这是粗估**，见 `CapacityEstimate.assumptions`。

口径冲突提示：`rolling_stats.py` 另有一条 `turnover_series`，它由 fills 重算且以
「成交额 / 初始资金」为分母，与本模块的「成交额 / 当日净值」不是同一个数。
本模块是 N2 的规范口径，两者不要混用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.data.models import Bar
from app.engine.backtest.daily_result import PortfolioDailyResult

_EPS = 1e-12

#: 默认 ADV 回看窗口（交易日）
DEFAULT_ADV_WINDOW = 20
#: 默认单标的单日成交额占 ADV 的上限
DEFAULT_MAX_ADV_SHARE = 0.05
#: 换手率年化用的交易日数（与 metrics.TRADING_DAYS_US 同口径）
TRADING_DAYS_PER_YEAR = 252


# ── 换手率 ───────────────────────────────────────────────────────

def turnover_series(
    daily: Sequence[PortfolioDailyResult], equity: pd.Series
) -> pd.Series:
    """日换手率 = 当日成交额 / 当日组合净值。

    净值缺失（净值曲线未覆盖该会话日）的日子直接跳过，而不是记 0 ——
    记 0 会被读成「这天没交易」，事实是「这天没净值可比」。
    """
    nav = _nav_by_date(equity)
    points = [
        (day, d.turnover / nav[day])
        for d in daily
        if (day := d.date) in nav and nav[day] > _EPS
    ]
    return _to_series(points)


# ── 杠杆利用率 ───────────────────────────────────────────────────

def leverage_series(
    daily: Sequence[PortfolioDailyResult], equity: pd.Series
) -> pd.Series:
    """杠杆利用率 = Σ|持仓市值| / 净值（绝对值加总，见模块 docstring）。"""
    nav = _nav_by_date(equity)
    points = [
        (day, gross_exposure(d) / nav[day])
        for d in daily
        if (day := d.date) in nav and nav[day] > _EPS
    ]
    return _to_series(points)


def gross_exposure(day_result: PortfolioDailyResult) -> float:
    """当日持仓总市值（多空都取绝对值）。"""
    return sum(
        abs(c.end_pos * c.close_price) for c in day_result.contracts.values()
    )


# ── 容量估计 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class CapacityEstimate:
    """策略容量的**粗估**结果。

    `capacity` 为 None 表示样本不足（没有任何一天既有成交又有可用 ADV），
    此时不要把它当成「容量为 0」。
    """

    capacity: float | None
    scale_factor: float | None
    reference_equity: float
    adv_window: int
    max_adv_share: float
    binding_symbol: str | None
    binding_date: date | None
    #: 约束日那条 ADV 实际由几个交易日平均得出（< adv_window 即窗口未满）
    binding_adv_samples: int
    sample_days: int
    assumptions: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "capacity": round(self.capacity, 2) if self.capacity is not None else None,
            "scale_factor": (
                round(self.scale_factor, 4) if self.scale_factor is not None else None
            ),
            "reference_equity": round(self.reference_equity, 2),
            "adv_window": self.adv_window,
            "max_adv_share": self.max_adv_share,
            "binding_symbol": self.binding_symbol,
            "binding_date": self.binding_date.isoformat() if self.binding_date else None,
            "binding_adv_samples": self.binding_adv_samples,
            "adv_window_full": self.binding_adv_samples >= self.adv_window,
            "sample_days": self.sample_days,
            "is_rough_estimate": True,
            "assumptions": list(self.assumptions),
        }


def estimate_capacity(
    daily: Sequence[PortfolioDailyResult],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    reference_equity: float,
    adv_window: int = DEFAULT_ADV_WINDOW,
    max_adv_share: float = DEFAULT_MAX_ADV_SHARE,
) -> CapacityEstimate:
    """策略容量：在「单标的单日成交不超过 ADV 的 max_adv_share」约束下能管多少钱。

    `reference_equity` 是本次回测的参考净值（通常取初始资金）；容量 =
    最紧约束下的可放大倍数 × 参考净值。契约原签名没有这一项，但没有它就只能
    给出一个无量纲倍数，无法落到金额上。
    """
    if adv_window < 1:
        raise ValueError(f"adv_window 必须 >= 1，收到 {adv_window}")
    if not 0 < max_adv_share <= 1:
        raise ValueError(f"max_adv_share 必须落在 (0, 1]，收到 {max_adv_share}")

    adv = _adv_by_symbol_date(bars_by_symbol, adv_window)
    binding = _binding_constraint(daily, adv, max_adv_share)

    capacity = (
        binding.scale * reference_equity
        if binding.scale is not None and reference_equity > _EPS
        else None
    )
    return CapacityEstimate(
        capacity=capacity,
        scale_factor=binding.scale,
        reference_equity=reference_equity,
        adv_window=adv_window,
        max_adv_share=max_adv_share,
        binding_symbol=binding.symbol,
        binding_date=binding.day,
        binding_adv_samples=binding.adv_samples,
        sample_days=binding.sample_days,
        assumptions=_assumptions(adv_window, max_adv_share, binding),
    )


def _assumptions(
    adv_window: int, max_adv_share: float, binding: _Binding
) -> tuple[str, ...]:
    """把「这只是个粗估」写清楚，避免一个看起来很精确的单一数字被误读。"""
    base = (
        f"约束条件：单标的单日成交额 ≤ 其 {adv_window} 日平均成交额（ADV）的 "
        f"{max_adv_share:.1%}",
        "ADV 取回测区间内的实际成交额，未考虑策略自身进场后对成交量的抬升或抽干",
        "假设策略线性等比放大：所有标的、所有交易日同倍放大，权重结构与择时不变",
        "ADV 占比只是冲击成本的一个**代理**，未建模冲击成本曲线、执行算法与市场状态",
        "取全区间最紧的一天作为约束，单个异常放量/缩量日会主导结果",
        "结论是数量级参考（粗估），不是可直接下单的精确资金上限",
    )
    if 0 < binding.adv_samples < adv_window:
        return (
            *base,
            f"⚠ 约束日落在回测早期：该日 ADV 只由 {binding.adv_samples} 个交易日"
            f"（而非 {adv_window} 个）平均得出，样本偏薄",
        )
    return base


@dataclass(frozen=True)
class _Binding:
    """最紧的那一天：可放大倍数、是哪个标的哪一天、以及该日 ADV 的样本数。"""

    scale: float | None = None
    symbol: str | None = None
    day: date | None = None
    adv_samples: int = 0
    sample_days: int = 0


def _binding_constraint(
    daily: Sequence[PortfolioDailyResult],
    adv: Mapping[str, Mapping[date, tuple[float, int]]],
    max_adv_share: float,
) -> _Binding:
    """扫描每个 (会话日, 标的)，找出可放大倍数最小的那一个。"""
    best = _Binding()

    for day_result in daily:
        for symbol, contract in day_result.contracts.items():
            if contract.turnover <= _EPS:
                continue
            entry = adv.get(symbol, {}).get(day_result.date)
            if entry is None or entry[0] <= _EPS:
                continue
            symbol_adv, adv_samples = entry
            scale = symbol_adv * max_adv_share / contract.turnover
            tighter = best.scale is None or scale < best.scale
            best = _Binding(
                scale=scale if tighter else best.scale,
                symbol=symbol if tighter else best.symbol,
                day=day_result.date if tighter else best.day,
                adv_samples=adv_samples if tighter else best.adv_samples,
                sample_days=best.sample_days + 1,
            )

    return best


def _adv_by_symbol_date(
    bars_by_symbol: Mapping[str, Sequence[Bar]], window: int
) -> dict[str, dict[date, tuple[float, int]]]:
    """逐标的算「截至该会话日（含）的近 window 日平均成交额」。

    先按会话日汇总成交额，分钟级 bar 与日线走同一条路径。
    """
    return {
        symbol: _rolling_mean_by_date(_dollar_volume_by_date(bars), window)
        for symbol, bars in bars_by_symbol.items()
        if bars
    }


def _dollar_volume_by_date(bars: Sequence[Bar]) -> dict[date, float]:
    """按会话日汇总成交额：优先用交易所口径的 turnover，缺失时退回 close × volume。"""
    totals: dict[date, float] = {}
    for bar in bars:
        day = bar.time.date()
        amount = bar.turnover if bar.turnover else bar.close * bar.volume
        totals[day] = totals.get(day, 0.0) + float(amount)
    return totals


def _rolling_mean_by_date(
    totals: Mapping[date, float], window: int
) -> dict[date, tuple[float, int]]:
    """返回 `日期 → (ADV, 实际样本数)`。

    刻意**不**丢弃窗口未满的早期日子：短回测（少于 window 天）否则会直接算不出
    容量。代价是前几天的 ADV 是小样本平均，所以把样本数一并带出去 ——
    约束日落在小样本上时要能看见，而不是被一个「20 日均量」的名字盖住。
    """
    days = sorted(totals)
    out: dict[date, tuple[float, int]] = {}
    for i, day in enumerate(days):
        start = max(0, i - window + 1)
        chunk = [totals[d] for d in days[start : i + 1]]
        out[day] = (sum(chunk) / len(chunk), len(chunk))
    return out


# ── 报告 section ─────────────────────────────────────────────────

def build_capacity_section(
    daily: Sequence[PortfolioDailyResult],
    equity: pd.Series,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    reference_equity: float | None = None,
    adv_window: int = DEFAULT_ADV_WINDOW,
    max_adv_share: float = DEFAULT_MAX_ADV_SHARE,
) -> dict | None:
    """组装 N2 的可空 section；无日结数据时返回 None（而非一堆 0）。"""
    if not daily:
        return None

    turnover = turnover_series(daily, equity)
    leverage = leverage_series(daily, equity)
    ref = reference_equity if reference_equity is not None else _first_equity(equity)
    estimate = estimate_capacity(
        daily,
        bars_by_symbol,
        reference_equity=ref,
        adv_window=adv_window,
        max_adv_share=max_adv_share,
    )

    return {
        "turnover": _turnover_block(turnover),
        "leverage": _leverage_block(leverage),
        "capacity": estimate.to_dict(),
    }


def _turnover_block(series: pd.Series) -> dict:
    if series.empty:
        return {
            "avg_daily_pct": 0.0,
            "median_daily_pct": 0.0,
            "max_daily_pct": 0.0,
            "annualized_pct": 0.0,
            "series": [],
        }
    pct = series * 100
    return {
        "avg_daily_pct": round(float(pct.mean()), 4),
        "median_daily_pct": round(float(pct.median()), 4),
        "max_daily_pct": round(float(pct.max()), 4),
        "annualized_pct": round(float(pct.mean()) * TRADING_DAYS_PER_YEAR, 4),
        "series": _series_points(series),
    }


def _leverage_block(series: pd.Series) -> dict:
    if series.empty:
        return {"avg": 0.0, "median": 0.0, "max": 0.0, "p95": 0.0, "series": []}
    return {
        "avg": round(float(series.mean()), 4),
        "median": round(float(series.median()), 4),
        "max": round(float(series.max()), 4),
        "p95": round(float(series.quantile(0.95)), 4),
        "series": _series_points(series),
    }


# ── 内部工具 ─────────────────────────────────────────────────────

def _nav_by_date(equity: pd.Series) -> dict[date, float]:
    """净值曲线折成「会话日 → 当日最后一个净值」。"""
    if equity is None or equity.empty:
        return {}
    nav: dict[date, float] = {}
    for ts, value in equity.items():
        nav[pd.Timestamp(ts).date()] = float(value)
    return nav


def _first_equity(equity: pd.Series) -> float:
    if equity is None or equity.empty:
        return 0.0
    return float(equity.iloc[0])


def _to_series(points: list[tuple[date, float]]) -> pd.Series:
    if not points:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in points])
    return pd.Series([v for _, v in points], index=index)


def _series_points(series: pd.Series) -> list[dict]:
    return [
        {"time": pd.Timestamp(ts).isoformat(), "value": round(float(v), 6)}
        for ts, v in series.items()
    ]
