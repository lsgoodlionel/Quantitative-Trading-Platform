"""
ScreenerService — 股票筛选器数据服务（Epic A / A3）

职责:
1. 快照采集: 汇总面板标的的行情 + 基本面（价格/涨跌幅/PE/PB/市值/股息/换手）
   - A股:  AkShare 东财实时行情，一次拉全市场（含 PE/PB/总市值），面板过滤
   - 美股/港股: yfinance 批量下载价格 + 逐票基本面（Redis 缓存，优雅降级）
2. 条件过滤: 按市值/PE/PB/股息/涨跌幅/行业/价格等链式过滤 + 排序
3. 涨跌榜: 从快照按涨跌幅取涨幅榜 / 跌幅榜
4. 缓存: 快照 60s、基本面 6h（Redis），Redis 不可用时静默降级

参考: OpenBB finviz equity_screener（条件字段口径）；不复制其实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field

from app.core.config import settings
from app.data.models import Market
from app.data.screener_meta import sector_of
from app.data.symbol_dict import A_PANEL, HK_PANEL, US_PANEL

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
_SNAPSHOT_TTL = 60           # 快照缓存 60 秒
_FUND_TTL = 6 * 3600         # 基本面缓存 6 小时
_FUND_DEADLINE = 22.0        # 基本面采集软截止（秒），留余量给外层 wait_for
_PRICE_TIMEOUT = 18.0
_FUND_TIMEOUT = 28.0
_YI = 1e8                    # 「亿」换算因子

_PANELS: dict[Market, list[tuple[str, str]]] = {
    Market.A: A_PANEL,
    Market.HK: HK_PANEL,
    Market.US: US_PANEL,
}

SORT_KEYS = ("change_pct", "market_cap", "pe", "pb", "dividend_yield",
             "turnover", "price")


# ── 数据结构 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    """筛选候选标的快照。market_cap/turnover 为本币原始单位。"""

    symbol: str
    market: str
    name: str
    sector: str = "其他"
    price: float | None = None
    change_pct: float | None = None       # 当日涨跌幅 %
    pe: float | None = None               # 市盈率（动态/TTM）
    pb: float | None = None               # 市净率
    market_cap: float | None = None       # 总市值（本币元）
    dividend_yield: float | None = None   # 股息率 %
    volume: int | None = None
    turnover: float | None = None         # 成交额（本币元）
    turnover_rate: float | None = None    # 换手率 %


@dataclass
class ScreenerCriteria:
    """筛选条件（市值阈值以「亿」为单位）。"""

    market: Market = Market.US
    min_price: float | None = None
    max_price: float | None = None
    min_market_cap_yi: float | None = None
    max_market_cap_yi: float | None = None
    min_pe: float | None = None
    max_pe: float | None = None
    min_pb: float | None = None
    max_pb: float | None = None
    min_dividend_yield: float | None = None
    min_change_pct: float | None = None
    max_change_pct: float | None = None
    min_volume: int | None = None
    sectors: list[str] = field(default_factory=list)
    sort_by: str = "change_pct"
    sort_dir: str = "desc"
    limit: int = 50


# ── 通用解析工具 ───────────────────────────────────────────────
def _f(v: object) -> float | None:
    """安全转 float，NaN/None/非法 → None。"""
    try:
        if v is None:
            return None
        fv = float(v)
        if fv != fv:  # NaN
            return None
        return fv
    except (TypeError, ValueError):
        return None


def _norm_dividend_yield(v: object) -> float | None:
    """yfinance dividendYield 可能是分数(0.015)或百分比(1.5)，统一为百分比。"""
    dy = _f(v)
    if dy is None:
        return None
    return round(dy * 100, 2) if 0 < dy < 1 else round(dy, 2)


def _to_yf_symbol(symbol: str, market: Market) -> str:
    if market == Market.HK:
        if symbol.endswith(".HK"):
            return symbol
        # Yahoo 港股要求 4 位零填充（腾讯 00700 → 0700.HK），与 bars.py 一致
        if symbol.isdigit():
            return f"{int(symbol):04d}.HK"
        return f"{symbol}.HK"
    return symbol


def _redis():
    """同步 Redis 客户端；连接失败返回 None（优雅降级）。"""
    try:
        import redis as sync_redis
        return sync_redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("screener redis unavailable: %s", exc)
        return None


# ── 快照缓存（Redis）──────────────────────────────────────────
def _read_snapshot_cache(market: Market) -> list[Candidate] | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(f"screener:snap:{market.value}")
        r.close()
        if not raw:
            return None
        return [Candidate(**d) for d in json.loads(raw)]
    except Exception as exc:  # noqa: BLE001
        logger.debug("read snapshot cache failed: %s", exc)
        return None


def _write_snapshot_cache(market: Market, cands: list[Candidate]) -> None:
    r = _redis()
    if r is None:
        return
    try:
        payload = json.dumps([asdict(c) for c in cands])
        r.setex(f"screener:snap:{market.value}", _SNAPSHOT_TTL, payload)
        r.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("write snapshot cache failed: %s", exc)


# ── A股快照（AkShare 一次拉全市场）──────────────────────────────
def _fetch_a_snapshot_sync() -> list[Candidate]:
    codes = {s for s, _ in A_PANEL}
    spot: dict[str, dict] = {}
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            if code not in codes:
                continue
            spot[code] = {
                "price": _f(row.get("最新价")),
                "change_pct": _f(row.get("涨跌幅")),
                "pe": _f(row.get("市盈率-动态")),
                "pb": _f(row.get("市净率")),
                "market_cap": _f(row.get("总市值")),
                "turnover": _f(row.get("成交额")),
                "turnover_rate": _f(row.get("换手率")),
                "volume": row.get("成交量"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_a_snapshot error: %s: %s", type(exc).__name__, exc)

    out: list[Candidate] = []
    for symbol, cn in A_PANEL:
        d = spot.get(symbol, {})
        vol = d.get("volume")
        out.append(Candidate(
            symbol=symbol, market="A", name=cn, sector=sector_of(Market.A, symbol),
            price=d.get("price"), change_pct=d.get("change_pct"),
            pe=d.get("pe"), pb=d.get("pb"), market_cap=d.get("market_cap"),
            dividend_yield=None,
            volume=int(_f(vol)) if _f(vol) is not None else None,
            turnover=d.get("turnover"), turnover_rate=d.get("turnover_rate"),
        ))
    return out


# ── 美股/港股价格批量（yfinance download）──────────────────────
def _fetch_price_batch_sync(panel: list[tuple[str, str]], market: Market) -> dict[str, dict]:
    yf_map = {_to_yf_symbol(s, market): s for s, _ in panel}
    result: dict[str, dict] = {}
    try:
        import yfinance as yf
        df = yf.download(
            list(yf_map.keys()), period="2d", group_by="ticker",
            auto_adjust=False, progress=False, threads=True, timeout=15,
        )
        for yf_sym, symbol in yf_map.items():
            try:
                sub = df[yf_sym] if len(yf_map) > 1 else df
                closes = sub["Close"].dropna()
                vols = sub["Volume"].dropna()
                if closes.empty:
                    continue
                price = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
                chg = ((price - prev) / prev * 100) if prev else None
                result[symbol] = {
                    "price": round(price, 3),
                    "change_pct": round(chg, 2) if chg is not None else None,
                    "volume": int(vols.iloc[-1]) if not vols.empty else None,
                }
            except Exception:  # noqa: BLE001, PERF203
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_price_batch error: %s: %s", type(exc).__name__, exc)
    return result


# ── 美股/港股逐票基本面（Redis 缓存 + 线程池 + 软截止）──────────
def _fetch_one_fundamental(symbol: str, market: Market) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(_to_yf_symbol(symbol, market)).info or {}
        return {
            "pe": _f(info.get("trailingPE")),
            "pb": _f(info.get("priceToBook")),
            "market_cap": _f(info.get("marketCap")),
            "dividend_yield": _norm_dividend_yield(info.get("dividendYield")),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("fundamental fetch failed %s: %s", symbol, exc)
        return {}


def _fetch_fundamentals_sync(panel: list[tuple[str, str]], market: Market) -> dict[str, dict]:
    r = _redis()
    result: dict[str, dict] = {}
    misses: list[str] = []
    for symbol, _ in panel:
        cached = None
        if r is not None:
            try:
                raw = r.get(f"screener:fund:{market.value}:{symbol}")
                cached = json.loads(raw) if raw else None
            except Exception:  # noqa: BLE001
                cached = None
        if cached is not None:
            result[symbol] = cached
        else:
            misses.append(symbol)

    if misses:
        deadline = time.monotonic() + _FUND_DEADLINE
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_one_fundamental, s, market): s for s in misses}
            for fut in as_completed(futures):
                if time.monotonic() > deadline:
                    break
                symbol = futures[fut]
                data = fut.result()
                result[symbol] = data
                if r is not None and data:
                    try:
                        r.setex(f"screener:fund:{market.value}:{symbol}",
                                _FUND_TTL, json.dumps(data))
                    except Exception:  # noqa: BLE001
                        pass
    if r is not None:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass
    return result


async def _fetch_yf_snapshot(panel: list[tuple[str, str]], market: Market) -> list[Candidate]:
    loop = asyncio.get_running_loop()
    try:
        price_map = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_price_batch_sync, panel, market),
            timeout=_PRICE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("price batch timeout %s: %s", market.value, exc)
        price_map = {}

    try:
        fund_map = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_fundamentals_sync, panel, market),
            timeout=_FUND_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fundamentals timeout %s: %s", market.value, exc)
        fund_map = {}

    out: list[Candidate] = []
    for symbol, cn in panel:
        p = price_map.get(symbol, {})
        f = fund_map.get(symbol, {})
        out.append(Candidate(
            symbol=symbol, market=market.value, name=cn,
            sector=sector_of(market, symbol),
            price=p.get("price"), change_pct=p.get("change_pct"),
            pe=f.get("pe"), pb=f.get("pb"), market_cap=f.get("market_cap"),
            dividend_yield=f.get("dividend_yield"), volume=p.get("volume"),
        ))
    return out


# ── 对外接口 ──────────────────────────────────────────────────
async def get_snapshot(market: Market) -> list[Candidate]:
    """获取指定市场面板快照（优先 60s 缓存）。"""
    cached = _read_snapshot_cache(market)
    if cached:
        return cached

    if market == Market.A:
        cands = await _fetch_a_snapshot_sync_async()
    else:
        panel = _PANELS.get(market, [])
        cands = await _fetch_yf_snapshot(panel, market)

    _write_snapshot_cache(market, cands)
    return cands


async def _fetch_a_snapshot_sync_async() -> list[Candidate]:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_a_snapshot_sync), timeout=12.0
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("A snapshot timeout: %s", exc)
        return [
            Candidate(symbol=s, market="A", name=cn, sector=sector_of(Market.A, s))
            for s, cn in A_PANEL
        ]


def _passes(c: Candidate, cr: ScreenerCriteria) -> bool:
    """判断候选是否满足全部指定条件；缺失字段遇到相关阈值则排除。"""
    cap_min = cr.min_market_cap_yi * _YI if cr.min_market_cap_yi is not None else None
    cap_max = cr.max_market_cap_yi * _YI if cr.max_market_cap_yi is not None else None
    checks: list[tuple[float | None, float | None, float | None]] = [
        (c.price, cr.min_price, cr.max_price),
        (c.change_pct, cr.min_change_pct, cr.max_change_pct),
        (c.pe, cr.min_pe, cr.max_pe),
        (c.pb, cr.min_pb, cr.max_pb),
        (c.market_cap, cap_min, cap_max),
        (c.volume, cr.min_volume, None),
        (c.dividend_yield, cr.min_dividend_yield, None),
    ]
    for value, lo, hi in checks:
        if lo is not None or hi is not None:
            if value is None:
                return False
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
    if cr.sectors and c.sector not in cr.sectors:
        return False
    return True


def _sort_candidates(cands: list[Candidate], cr: ScreenerCriteria) -> list[Candidate]:
    key = cr.sort_by if cr.sort_by in SORT_KEYS else "change_pct"
    reverse = cr.sort_dir != "asc"
    # 缺失值统一排到末尾
    sentinel = float("-inf") if reverse else float("inf")

    def _k(c: Candidate) -> float:
        v = getattr(c, key, None)
        return v if v is not None else sentinel

    return sorted(cands, key=_k, reverse=reverse)


def apply_filter(cands: list[Candidate], cr: ScreenerCriteria) -> list[Candidate]:
    matched = [c for c in cands if _passes(c, cr)]
    ordered = _sort_candidates(matched, cr)
    limit = max(1, min(cr.limit, 200))
    return ordered[:limit]


def get_movers(cands: list[Candidate], top_n: int = 10) -> tuple[list[Candidate], list[Candidate]]:
    """从快照生成涨幅榜 / 跌幅榜（剔除无涨跌幅数据）。"""
    valued = [c for c in cands if c.change_pct is not None]
    gainers = sorted(valued, key=lambda c: c.change_pct, reverse=True)[:top_n]
    losers = sorted(valued, key=lambda c: c.change_pct)[:top_n]
    return gainers, losers
