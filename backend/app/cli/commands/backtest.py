"""
`backtest` 子命令（Wave O-a / O3）

薄壳：取数走 `DataService`，跑批走 `BacktestEngine`，指标计算一行都不在这里重写。

默认 `use_cache=False` —— 缓存读写要连 TimescaleDB，而 CLI 常在没起 docker 的
机器上一次性跑。想吃缓存加 `--use-cache`。
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.cli.errors import EXIT_OK, CliError, CliUsageError

__all__ = ["run_backtest"]

#: 回测引擎至少要这么多根 bar 才有意义，低于此值直接告诉用户去查区间/代码
_MIN_BARS = 5


def run_backtest(args: argparse.Namespace) -> int:
    """跑一次单标的回测并打印摘要（或 `--json` 全量结果）。"""
    import asyncio

    from app.engine.backtest.engine import BacktestConfig, BacktestEngine
    from app.strategy.resolver import StrategyNameConflictError, available_strategies

    start, end = _parse_window(args.start, args.end)
    market, frequency = _parse_market_frequency(args.market, args.frequency)
    params = _parse_params(args.params)

    try:
        registry = available_strategies()
    except StrategyNameConflictError as exc:
        raise CliError(str(exc)) from exc
    if args.strategy not in registry:
        raise CliUsageError(
            f"未知策略 '{args.strategy}'。可用策略见 `list-strategies`。"
        )

    bars = asyncio.run(_fetch_bars(args, market, frequency, start, end))
    if len(bars) < _MIN_BARS:
        raise CliError(
            f"数据不足：仅拿到 {len(bars)} 根 K 线。请检查标的代码、市场与日期区间。"
        )

    strategy = registry[args.strategy](params=params)
    engine = BacktestEngine(BacktestConfig(initial_cash=args.cash, market=market))
    result = engine.run(strategy, bars)

    _print_result(args, result, len(bars))
    return EXIT_OK


async def _fetch_bars(args: argparse.Namespace, market, frequency, start: date, end: date):
    """自建 async engine 取数（CLI 没有 FastAPI 的依赖注入 session）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.data.service import DataService

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            return await DataService(session).get_bars(
                symbol=args.symbol, market=market, frequency=frequency,
                start=start, end=end, use_cache=args.use_cache,
            )
    except Exception as exc:
        raise CliError(
            f"取数失败: {exc}。"
            "若是数据库连接问题，去掉 --use-cache 可完全绕过 TimescaleDB。"
        ) from exc
    finally:
        await engine.dispose()


def _print_result(args: argparse.Namespace, result, bar_count: int) -> None:
    metrics = result.report["metrics"]
    if args.json:
        print(json.dumps({
            "strategy": args.strategy,
            "symbol": args.symbol,
            "market": args.market,
            "start": args.start,
            "end": args.end,
            "bars": bar_count,
            "initial_cash": args.cash,
            "final_value": result.final_value,
            "metrics": metrics,
        }, ensure_ascii=False, indent=2, default=str))
        return

    print(f"策略 {args.strategy} · {args.symbol}({args.market}) · {args.start}~{args.end}")
    print(f"K 线数量   {bar_count}")
    print(f"初始资金   {args.cash:,.2f}")
    print(f"期末净值   {result.final_value:,.2f}")
    for label, key in (
        ("总收益率", "total_return_pct"), ("夏普比率", "sharpe_ratio"),
        ("最大回撤", "max_drawdown_pct"), ("交易次数", "total_trades"),
    ):
        print(f"{label}   {metrics.get(key)}")


# ── 入参解析（一律翻成 CliUsageError → 退出码 2）───────────────

def _parse_window(start: str, end: str) -> tuple[date, date]:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise CliUsageError(f"日期格式应为 YYYY-MM-DD: {exc}") from exc
    if end_date < start_date:
        raise CliUsageError(f"--end {end_date} 早于 --start {start_date}")
    return start_date, end_date


def _parse_market_frequency(market: str, frequency: str):
    from app.data.models import Frequency, Market

    try:
        market_enum = Market(market.upper())
    except ValueError as exc:
        raise CliUsageError(
            f"未知市场 '{market}'，可选: {', '.join(m.value for m in Market)}"
        ) from exc
    try:
        frequency_enum = Frequency(frequency)
    except ValueError as exc:
        raise CliUsageError(
            f"未知周期 '{frequency}'，可选: {', '.join(f.value for f in Frequency)}"
        ) from exc
    return market_enum, frequency_enum


def _parse_params(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"--params 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CliUsageError("--params 必须是 JSON 对象，如 '{\"fast_period\": 5}'")
    return parsed
