"""
指数成分股宇宙（M7）

数据源：A 股走 AkShare（中证指数官网成份股目录），不新增依赖。

⚠️ **本期只能拿到「最新」成分股，拿不到任意历史日的成分**（数据源限制，
见契约「四、不做」）。因此 `on` 参数的语义被严格限定：

- `on=None`        → 返回最新成分
- `on >= 快照日期`  → 允许。快照是那一天**已经公布**的最新成分，不含未来信息
- `on <  快照日期`  → **报错**（`HistoricalComponentsUnavailableError`）

最后一条是重点：用历史区间回测配上今天的成分股是典型的幸存者偏差 ——
当年被剔除的股票消失了，只剩活到今天的赢家。静默降级会产出看起来很美的假回测，
所以这里明确报错而不是返回最新。

美股指数（S&P 500 等）暂无可用的免费成分股接口：yfinance 不提供成分股列表，
ETF 持仓接口只给前十大持仓——用前十大冒充 500 只成分是更坏的错误，故直接报错。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from app.data.models import Market
from app.data.universe.cache import COMPONENTS_TTL, read_json, write_json
from app.data.universe.errors import (
    ComponentsFetchError,
    HistoricalComponentsUnavailableError,
    IndexNotSupportedError,
)

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 20.0


@dataclass(frozen=True)
class IndexSpec:
    """一个受支持的指数。"""

    code: str
    name: str
    market: Market


# 受支持的指数（AkShare 中证指数目录能覆盖的部分）
SUPPORTED_INDEXES: dict[str, IndexSpec] = {
    "000300": IndexSpec("000300", "沪深300", Market.A),
    "000905": IndexSpec("000905", "中证500", Market.A),
    "000852": IndexSpec("000852", "中证1000", Market.A),
    "000016": IndexSpec("000016", "上证50", Market.A),
}

# 常见别名 → 标准代码
_ALIASES: dict[str, str] = {
    "HS300": "000300", "CSI300": "000300", "SH000300": "000300", "000300.SH": "000300",
    "ZZ500": "000905", "CSI500": "000905", "SH000905": "000905",
    "ZZ1000": "000852", "CSI1000": "000852",
    "SZ50": "000016", "SSE50": "000016",
}


@dataclass(frozen=True)
class ComponentSnapshot:
    """一份成分股快照。`as_of` 为数据源标注的成分生效日期。"""

    index_code: str
    index_name: str
    symbols: tuple[str, ...]
    as_of: Date | None


def normalize_index_code(index_code: str) -> str:
    """把别名归一到标准 6 位代码；无法识别的原样返回（交给后续校验报错）。"""
    if not isinstance(index_code, str) or not index_code.strip():
        raise IndexNotSupportedError("指数代码不能为空")
    raw = index_code.strip().upper()
    return _ALIASES.get(raw, raw)


async def index_components(index_code: str, on: Date | None = None) -> list[str]:
    """
    取指数成分股代码列表。

    Args:
        index_code: 指数代码或别名（如 "000300" / "HS300"）
        on:         需要哪一天的成分；None = 最新

    Raises:
        IndexNotSupportedError:                该指数无可用数据源
        HistoricalComponentsUnavailableError:  `on` 早于数据源快照日期
        ComponentsFetchError:                  数据源请求失败
    """
    snapshot = await index_components_snapshot(index_code, on=on)
    return list(snapshot.symbols)


async def index_components_snapshot(
    index_code: str, on: Date | None = None
) -> ComponentSnapshot:
    """同 `index_components`，但连带返回 `as_of` 等元信息。"""
    code = normalize_index_code(index_code)
    spec = SUPPORTED_INDEXES.get(code)
    if spec is None:
        raise IndexNotSupportedError(
            f"指数 {index_code} 暂无可用的成分股数据源；"
            f"当前支持: {', '.join(sorted(SUPPORTED_INDEXES))}"
        )

    snapshot = await _load_snapshot(spec)
    _assert_usable_on(snapshot, on)
    return snapshot


def _assert_usable_on(snapshot: ComponentSnapshot, on: Date | None) -> None:
    """`on` 早于快照日期即拒绝 —— 那意味着把未来的成分名单用到过去。"""
    if on is None:
        return
    if snapshot.as_of is None:
        raise HistoricalComponentsUnavailableError(
            f"指数 {snapshot.index_code} 的数据源未标注成分生效日期，"
            f"无法确认 {on} 当日的成分，拒绝以最新成分代替（幸存者偏差）"
        )
    if on < snapshot.as_of:
        raise HistoricalComponentsUnavailableError(
            f"指数 {snapshot.index_code} 只能取到 {snapshot.as_of} 的成分，"
            f"请求的 {on} 早于该日期；以最新成分回测历史区间会产生幸存者偏差，故拒绝"
        )


# ── 数据源 ────────────────────────────────────────────────────
def _cache_key(code: str) -> str:
    return f"universe:index_cons:{code}"


async def _load_snapshot(spec: IndexSpec) -> ComponentSnapshot:
    cached = read_json(_cache_key(spec.code))
    if cached:
        return _snapshot_from_cache(spec, cached)

    loop = asyncio.get_running_loop()
    try:
        symbols, as_of = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_csindex_sync, spec.code),
            timeout=_FETCH_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        raise ComponentsFetchError(f"获取指数 {spec.code} 成分股失败: {exc}") from exc

    if not symbols:
        raise ComponentsFetchError(f"指数 {spec.code} 成分股数据源返回空列表")

    write_json(
        _cache_key(spec.code),
        {"symbols": list(symbols), "as_of": as_of.isoformat() if as_of else None},
        COMPONENTS_TTL,
    )
    return ComponentSnapshot(spec.code, spec.name, tuple(symbols), as_of)


def _snapshot_from_cache(spec: IndexSpec, cached: dict) -> ComponentSnapshot:
    raw_as_of = cached.get("as_of")
    as_of: Date | None = None
    if raw_as_of:
        try:
            as_of = datetime.fromisoformat(raw_as_of).date()
        except ValueError:
            logger.debug("指数 %s 缓存的 as_of 无法解析: %r", spec.code, raw_as_of)
    return ComponentSnapshot(
        spec.code, spec.name, tuple(cached.get("symbols") or ()), as_of
    )


def _fetch_csindex_sync(code: str) -> tuple[list[str], Date | None]:
    """同步拉取中证指数官网成份股目录（在线程池中调用）。"""
    import akshare as ak

    frame = ak.index_stock_cons_csindex(symbol=code)
    symbols = [str(s).zfill(6) for s in frame["成分券代码"].tolist()]
    return symbols, _first_date(frame)


def _first_date(frame) -> Date | None:
    """取成分目录标注的生效日期（整表同一个值）。"""
    if "日期" not in frame.columns or frame.empty:
        return None
    raw = frame["日期"].iloc[0]
    if isinstance(raw, Date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None
