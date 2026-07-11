"""
PairlistService — 动态标的池筛选（Epic E / E5）

区别于 A3 一次性筛选（screener.py）：这里是「规则链」——按 成交量/波动/价格/
价差/市值/近期表现 顺序链式过滤，产出可保存、可被策略引用的可交易 universe。

设计:
- 复用 W2b screener 快照（get_snapshot → Candidate：价格/涨跌幅/成交量/市值/成交额）
- 波动率/近期表现/价差需历史日线，按需批量拉取（yfinance），Redis 缓存 30 分钟
- 规则链有序执行：每条规则做 min/max 过滤 + 可选排序 + 可选取头部 N
- 全部返回新列表（不可变），不修改输入

参考: refs/freqtrade/plugins/pairlist/（VolumePairList/VolatilityFilter/SpreadFilter
      /PriceFilter/PerformanceFilter 的口径），用 pandas/numpy 实现，不复制其代码。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import asdict, dataclass, field

from app.data import screener as snap
from app.data.models import Market
from app.data.screener import Candidate, _redis, _to_yf_symbol

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
_METRICS_TTL = 30 * 60            # bars 派生指标缓存 30 分钟
_BARS_TIMEOUT = 20.0              # 历史日线批量下载超时（秒）
_TRADING_DAYS = 252              # 年化因子
_YI = 1e8                        # 「亿」换算因子
_DEFAULT_LOOKBACK = 20           # 默认回看天数
_MAX_LOOKBACK = 120
_MAX_UNIVERSE = 200

# 需历史日线才能计算的规则种类
_BARS_KINDS = frozenset({"volatility", "performance", "spread"})
# 全部合法规则种类
VALID_KINDS = frozenset(
    {"volume", "price", "market_cap", "volatility", "performance", "spread"}
)
# 每个规则种类对应 PairMetrics 的字段
_KIND_FIELD: dict[str, str] = {
    "volume": "volume",
    "price": "price",
    "market_cap": "market_cap",
    "volatility": "volatility",
    "performance": "performance",
    "spread": "spread_proxy",
}


# ── 数据结构 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class PairMetrics:
    """标的富化指标（快照 + bars 派生）。market_cap 为本币原始单位。"""

    symbol: str
    market: str
    name: str
    sector: str = "其他"
    price: float | None = None
    change_pct: float | None = None       # 当日涨跌幅 %
    volume: int | None = None
    turnover: float | None = None         # 成交额（本币元）
    market_cap: float | None = None       # 总市值（本币元）
    volatility: float | None = None       # 近 N 日日收益率年化波动率 %
    performance: float | None = None      # 近 N 日累计收益率 %
    spread_proxy: float | None = None     # 近 N 日 (high-low)/close 均值 %（价差/流动性代理）


@dataclass(frozen=True)
class PairlistRule:
    """单条链式过滤规则。

    min_value/max_value 的单位随 kind：
      volume → 股；price → 本币价；market_cap → 亿；
      volatility/performance/spread → 百分比。
    sort: 'asc' | 'desc' | None（按该规则字段排序）
    top: 保留头部 N 个（None = 不截断）
    """

    kind: str
    min_value: float | None = None
    max_value: float | None = None
    sort: str | None = None
    top: int | None = None


# ── 指标解析工具 ───────────────────────────────────────────────
def _safe_float(v: object) -> float | None:
    try:
        if v is None:
            return None
        fv = float(v)
        return None if fv != fv else fv
    except (TypeError, ValueError):
        return None


def _compute_bar_metrics(closes, highs, lows) -> dict[str, float | None]:
    """由日线序列计算年化波动率 / 累计收益 / 价差代理（均为 %）。"""
    import numpy as np

    close = np.asarray([c for c in closes if _safe_float(c) is not None], dtype=float)
    if close.size < 2:
        return {"volatility": None, "performance": None, "spread_proxy": None}

    rets = np.diff(close) / close[:-1]
    vol = float(np.std(rets, ddof=1)) * math.sqrt(_TRADING_DAYS) * 100 if rets.size >= 2 else None
    perf = float((close[-1] / close[0] - 1.0) * 100) if close[0] else None

    spread = None
    hi = np.asarray([h for h in highs if _safe_float(h) is not None], dtype=float)
    lo = np.asarray([lv for lv in lows if _safe_float(lv) is not None], dtype=float)
    n = min(hi.size, lo.size, close.size)
    if n >= 1:
        c = close[-n:]
        rng = (hi[-n:] - lo[-n:]) / np.where(c == 0, np.nan, c)
        rng = rng[~np.isnan(rng)]
        if rng.size:
            spread = float(np.mean(rng)) * 100

    return {
        "volatility": round(vol, 3) if vol is not None else None,
        "performance": round(perf, 3) if perf is not None else None,
        "spread_proxy": round(spread, 4) if spread is not None else None,
    }


# ── 历史日线批量拉取（yfinance，线程池 + Redis 缓存）────────────
def _fetch_bars_metrics_sync(
    panel: list[tuple[str, str]], market: Market, lookback: int
) -> dict[str, dict]:
    yf_map = {_to_yf_symbol(s, market): s for s, _ in panel}
    result: dict[str, dict] = {}
    try:
        import yfinance as yf

        period = f"{max(lookback + 10, 30)}d"
        df = yf.download(
            list(yf_map.keys()), period=period, group_by="ticker",
            auto_adjust=False, progress=False, threads=True, timeout=15,
        )
        for yf_sym, symbol in yf_map.items():
            try:
                sub = df[yf_sym] if len(yf_map) > 1 else df
                closes = sub["Close"].dropna().tolist()[-lookback:]
                highs = sub["High"].dropna().tolist()[-lookback:]
                lows = sub["Low"].dropna().tolist()[-lookback:]
                result[symbol] = _compute_bar_metrics(closes, highs, lows)
            except Exception:  # noqa: BLE001, PERF203
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairlist bars fetch error %s: %s", market.value, exc)
    return result


def _read_metrics_cache(market: Market, lookback: int) -> dict[str, dict] | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(f"pairlist:metrics:{market.value}:{lookback}")
        r.close()
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("read metrics cache failed: %s", exc)
        return None


def _write_metrics_cache(market: Market, lookback: int, data: dict[str, dict]) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.setex(f"pairlist:metrics:{market.value}:{lookback}",
                _METRICS_TTL, json.dumps(data))
        r.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("write metrics cache failed: %s", exc)


async def _get_bars_metrics(
    panel: list[tuple[str, str]], market: Market, lookback: int
) -> dict[str, dict]:
    cached = _read_metrics_cache(market, lookback)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    try:
        data = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_bars_metrics_sync, panel, market, lookback),
            timeout=_BARS_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bars metrics timeout %s: %s", market.value, exc)
        data = {}
    _write_metrics_cache(market, lookback, data)
    return data


# ── universe 构建 ─────────────────────────────────────────────
def _needs_bars(rules: list[PairlistRule]) -> bool:
    return any(rule.kind in _BARS_KINDS for rule in rules)


def _merge_metrics(cand: Candidate, bars: dict) -> PairMetrics:
    return PairMetrics(
        symbol=cand.symbol, market=cand.market, name=cand.name, sector=cand.sector,
        price=cand.price, change_pct=cand.change_pct, volume=cand.volume,
        turnover=cand.turnover, market_cap=cand.market_cap,
        volatility=bars.get("volatility"),
        performance=bars.get("performance"),
        spread_proxy=bars.get("spread_proxy"),
    )


async def build_universe(market: Market, rules: list[PairlistRule], lookback: int) -> list[PairMetrics]:
    """采集快照并按需富化历史指标，产出待过滤 universe（未过滤）。"""
    candidates = await snap.get_snapshot(market)
    bars_map: dict[str, dict] = {}
    if _needs_bars(rules):
        panel = [(c.symbol, c.name) for c in candidates]
        bars_map = await _get_bars_metrics(panel, market, lookback)
    return [_merge_metrics(c, bars_map.get(c.symbol, {})) for c in candidates]


# ── 规则链执行 ────────────────────────────────────────────────
def _rule_bound(rule: PairlistRule) -> tuple[float | None, float | None]:
    """返回规则的 (下界, 上界)，市值规则从「亿」换算为本币原始单位。"""
    lo, hi = rule.min_value, rule.max_value
    if rule.kind == "market_cap":
        lo = lo * _YI if lo is not None else None
        hi = hi * _YI if hi is not None else None
    return lo, hi


def _apply_rule(items: list[PairMetrics], rule: PairlistRule) -> list[PairMetrics]:
    """执行单条规则：min/max 过滤 → 可选排序 → 可选取头部 N。返回新列表。"""
    field_name = _KIND_FIELD[rule.kind]
    lo, hi = _rule_bound(rule)
    has_bound = lo is not None or hi is not None

    kept: list[PairMetrics] = []
    for it in items:
        value = getattr(it, field_name, None)
        if has_bound:
            if value is None:
                continue  # 缺失值遇到阈值 → 排除（与 screener 口径一致）
            if lo is not None and value < lo:
                continue
            if hi is not None and value > hi:
                continue
        kept.append(it)

    if rule.sort in ("asc", "desc"):
        reverse = rule.sort == "desc"
        sentinel = float("-inf") if reverse else float("inf")
        kept = sorted(
            kept,
            key=lambda it: (getattr(it, field_name, None)
                            if getattr(it, field_name, None) is not None else sentinel),
            reverse=reverse,
        )

    if rule.top is not None and rule.top > 0:
        kept = kept[: rule.top]
    return kept


def apply_chain(universe: list[PairMetrics], rules: list[PairlistRule]) -> list[PairMetrics]:
    """按顺序执行规则链，返回最终 universe（截断到上限）。"""
    result = list(universe)
    for rule in rules:
        result = _apply_rule(result, rule)
    return result[:_MAX_UNIVERSE]


# ── 参数规整 ──────────────────────────────────────────────────
def clamp_lookback(lookback: int | None) -> int:
    if lookback is None:
        return _DEFAULT_LOOKBACK
    return max(2, min(int(lookback), _MAX_LOOKBACK))


def metrics_to_dict(m: PairMetrics) -> dict:
    d = asdict(m)
    d["market_cap_yi"] = round(m.market_cap / _YI, 3) if m.market_cap is not None else None
    return d
