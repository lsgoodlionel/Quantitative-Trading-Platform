"""L2 组合风控模型测试（Wave L-b）

对应契约 docs/contracts/waveLb-risk-execution-models.md §二 与 §五.2。

注意：`tests/test_risk_models.py` 测的是 `engine/portfolio/risk_models.py`（VaR 那套），
与本文件无关，两者不要混淆。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.backtest.trade import EXIT_STOP_LOSS, ExitRules
from app.engine.framework import (
    AlphaModel,
    EqualWeightingPCM,
    FrameworkStrategy,
    Insight,
    InsightDirection,
    MaximumDrawdownPerSecurity,
    MaximumDrawdownPortfolio,
    MaximumSectorExposure,
    MaximumUnrealizedProfitPerSecurity,
    NullRiskModel,
    PortfolioTarget,
    TrailingStopRiskManagement,
)
from app.engine.framework.risk import RISK_TAG_MAX_DRAWDOWN
from app.strategy.context import PortfolioContext

START = datetime(2024, 1, 2, tzinfo=UTC)
_DAY = timedelta(days=1)


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(symbol: str, price: float, ts: datetime, volume: float = 5_000_000.0) -> Bar:
    return Bar(
        time=ts,
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([START + timedelta(days=i) for i in range(len(closes))])
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [5_000_000.0] * len(closes),
        },
        index=index,
    )


def _broker(cash: float = 1_000_000.0) -> PortfolioBroker:
    return PortfolioBroker(
        initial_cash=cash,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
        allow_short=True,
    )


def _advance(broker: PortfolioBroker, prices: dict[str, float], ts: datetime) -> dict[str, Bar]:
    """把行情推进一个时点并撮合挂单，返回该时点的 bars。"""
    bars = {s: _bar(s, p, ts) for s, p in prices.items()}
    broker.process_bars(ts, bars)
    return bars


def _context(broker: PortfolioBroker, prices: dict[str, float], ts: datetime) -> PortfolioContext:
    return PortfolioContext(
        time=ts,
        bars={s: _bar(s, p, ts) for s, p in prices.items()},
        symbols=sorted(prices),
        broker=broker,
        histories={s: _frame([p, p]) for s, p in prices.items()},
        market=Market.US,
    )


def _ctx(
    prices: dict[str, float],
    *,
    opens: dict[str, float] | None = None,
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> PortfolioContext:
    """构造带**真实 Trade**的组合上下文。

    positions 走券商的下单 → 撮合链路建仓（正 = 多，负 = 空），这样
    `broker.open_trades` 里才会有 K4 的 `Trade`；直接改 `broker.positions`
    是绕过 `_track_trade` 的，浮盈基准就无从谈起。
    opens 缺省与 prices 相同（开仓即现价，浮盈为 0）。
    """
    broker = _broker(cash)
    open_prices = {**prices, **(opens or {})}
    for symbol, qty in (positions or {}).items():
        if qty > 0:
            broker.buy(symbol, qty, Market.US)
        elif qty < 0:
            broker.short(symbol, -qty, Market.US)
    _advance(broker, open_prices, START)          # 以开仓价成交
    _advance(broker, prices, START + _DAY)        # 把估值推到现价
    return _context(broker, prices, START + _DAY)


def _by_symbol(targets: list[PortfolioTarget]) -> dict[str, int]:
    return {t.symbol: t.quantity for t in targets}


# ── 0. 模块变包后的导入路径与直通语义 ─────────────────────────


def test_module_to_package_keeps_legacy_import_paths():
    """risk.py → risk/ 是模块变包，既有导入路径必须一字不改地继续可用。"""
    from app.engine.framework.risk import NullRiskModel as FromPackage
    from app.engine.framework.risk import RiskManagementModel

    assert issubclass(FromPackage, RiskManagementModel)
    assert FromPackage is NullRiskModel


def test_null_risk_returns_targets_untouched():
    ctx = _ctx({"AAPL": 100.0})
    targets = [PortfolioTarget("AAPL", 100)]

    assert NullRiskModel().manage_risk(ctx, targets) == targets


# ── 1. MaximumDrawdownPerSecurity ─────────────────────────────


def test_per_security_drawdown_liquidates_only_the_losing_symbol():
    # Arrange: AAPL 从 100 跌到 90（-10%），MSFT 从 100 跌到 98（-2%）
    ctx = _ctx(
        {"AAPL": 90.0, "MSFT": 98.0},
        opens={"AAPL": 100.0, "MSFT": 100.0},
        positions={"AAPL": 100, "MSFT": 100},
    )
    targets = [PortfolioTarget("AAPL", 100), PortfolioTarget("MSFT", 100)]

    # Act
    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(ctx, targets)

    # Assert
    assert _by_symbol(out) == {"AAPL": 0, "MSFT": 100}
    assert targets[0].quantity == 100                 # 入参不被就地修改


def test_per_security_drawdown_tags_the_liquidation_reason():
    ctx = _ctx({"AAPL": 90.0}, opens={"AAPL": 100.0}, positions={"AAPL": 100})

    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert out[0].tag == RISK_TAG_MAX_DRAWDOWN        # 执行模型据此写 exit_reason


def test_per_security_drawdown_boundary_triggers_at_exact_threshold():
    ctx = _ctx({"AAPL": 95.0}, opens={"AAPL": 100.0}, positions={"AAPL": 100})

    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert out[0].quantity == 0                       # 与 K4 一致：达到阈值即触发


def test_per_security_drawdown_does_not_trigger_just_inside_threshold():
    ctx = _ctx({"AAPL": 96.0}, opens={"AAPL": 100.0}, positions={"AAPL": 100})

    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert out[0].quantity == 100


def test_per_security_drawdown_uses_direction_aware_profit_for_shorts():
    # Arrange: 空头在 100 开仓，价格涨到 110 = 浮亏 10%
    ctx = _ctx({"AAPL": 110.0}, opens={"AAPL": 100.0}, positions={"AAPL": -100})

    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", -100)]
    )

    assert out[0].quantity == 0


def test_per_security_drawdown_ignores_symbols_without_a_trade():
    ctx = _ctx({"AAPL": 90.0}, opens={"AAPL": 100.0})      # 无持仓 → 无 Trade

    out = MaximumDrawdownPerSecurity(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert out[0].quantity == 100


def test_per_security_drawdown_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="max_drawdown"):
        MaximumDrawdownPerSecurity(max_drawdown=0.0)


# ── 2. MaximumDrawdownPortfolio ───────────────────────────────


def test_portfolio_drawdown_first_call_only_sets_the_high_water_mark():
    ctx = _ctx({"AAPL": 100.0}, cash=1_000_000.0)

    out = MaximumDrawdownPortfolio(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert _by_symbol(out) == {"AAPL": 100}


def test_portfolio_drawdown_zeroes_every_target_when_breached():
    model = MaximumDrawdownPortfolio(max_drawdown=0.05)
    targets = [PortfolioTarget("AAPL", 100), PortfolioTarget("MSFT", 200)]

    model.manage_risk(_ctx({"AAPL": 100.0, "MSFT": 100.0}, cash=1_000_000.0), targets)
    out = model.manage_risk(_ctx({"AAPL": 100.0, "MSFT": 100.0}, cash=900_000.0), targets)

    assert _by_symbol(out) == {"AAPL": 0, "MSFT": 0}
    assert _by_symbol(targets) == {"AAPL": 100, "MSFT": 200}     # 入参不变


def test_portfolio_drawdown_trailing_tracks_new_peak():
    model = MaximumDrawdownPortfolio(max_drawdown=0.05, is_trailing=True)
    targets = [PortfolioTarget("AAPL", 100)]

    # 净值 100 万 → 120 万（新高）→ 115 万（较峰值 -4.2%，未触发）
    model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_000_000.0), targets)
    model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_200_000.0), targets)
    ok = model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_150_000.0), targets)
    assert _by_symbol(ok) == {"AAPL": 100}

    # 再跌到 113 万 = 较峰值 -5.8% → 触发
    hit = model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_130_000.0), targets)
    assert _by_symbol(hit) == {"AAPL": 0}


def test_portfolio_drawdown_non_trailing_ignores_new_highs():
    model = MaximumDrawdownPortfolio(max_drawdown=0.05, is_trailing=False)
    targets = [PortfolioTarget("AAPL", 100)]

    model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_000_000.0), targets)
    model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_500_000.0), targets)   # 不抬高水位
    out = model.manage_risk(_ctx({"AAPL": 100.0}, cash=980_000.0), targets)

    assert _by_symbol(out) == {"AAPL": 100}            # 相对初始高水位只跌了 2%


def test_portfolio_drawdown_rearms_after_liquidation():
    model = MaximumDrawdownPortfolio(max_drawdown=0.05)
    targets = [PortfolioTarget("AAPL", 100)]

    model.manage_risk(_ctx({"AAPL": 100.0}, cash=1_000_000.0), targets)
    assert _by_symbol(model.manage_risk(_ctx({"AAPL": 100.0}, cash=900_000.0), targets)) == {
        "AAPL": 0
    }
    # 清仓后高水位从当下重新起算，同一净值不该再次触发
    out = model.manage_risk(_ctx({"AAPL": 100.0}, cash=900_000.0), targets)
    assert _by_symbol(out) == {"AAPL": 100}


def test_portfolio_drawdown_rejects_out_of_range_threshold():
    with pytest.raises(ValueError, match="max_drawdown"):
        MaximumDrawdownPortfolio(max_drawdown=1.5)


# ── 3. MaximumUnrealizedProfitPerSecurity ─────────────────────


def test_unrealized_profit_takes_money_off_the_table():
    ctx = _ctx(
        {"AAPL": 110.0, "MSFT": 102.0},
        opens={"AAPL": 100.0, "MSFT": 100.0},
        positions={"AAPL": 100, "MSFT": 100},
    )

    out = MaximumUnrealizedProfitPerSecurity(max_profit=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100), PortfolioTarget("MSFT", 100)]
    )

    assert _by_symbol(out) == {"AAPL": 0, "MSFT": 100}


def test_unrealized_profit_triggers_for_profitable_short():
    ctx = _ctx({"AAPL": 90.0}, opens={"AAPL": 100.0}, positions={"AAPL": -100})

    out = MaximumUnrealizedProfitPerSecurity(max_profit=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", -100)]
    )

    assert out[0].quantity == 0


def test_unrealized_profit_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="max_profit"):
        MaximumUnrealizedProfitPerSecurity(max_profit=-0.01)


# ── 4. MaximumSectorExposure ──────────────────────────────────

_SECTORS = {"AAPL": "tech", "MSFT": "tech", "XOM": "energy"}


def _sector_of(symbol: str) -> str | None:
    return _SECTORS.get(symbol)


def test_sector_exposure_scales_down_the_over_exposed_sector_only():
    # Arrange: 净值 100 万；tech 目标 40 万（40%）超过 20% 上限，energy 10 万（10%）不超
    ctx = _ctx({"AAPL": 100.0, "MSFT": 100.0, "XOM": 100.0}, cash=1_000_000.0)
    targets = [
        PortfolioTarget("AAPL", 2_000),
        PortfolioTarget("MSFT", 2_000),
        PortfolioTarget("XOM", 1_000),
    ]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    # tech 整体按 0.20/0.40 = 0.5 缩减
    assert _by_symbol(out) == {"AAPL": 1_000, "MSFT": 1_000, "XOM": 1_000}


def test_sector_exposure_leaves_everything_alone_below_threshold():
    ctx = _ctx({"AAPL": 100.0, "MSFT": 100.0}, cash=1_000_000.0)
    targets = [PortfolioTarget("AAPL", 500), PortfolioTarget("MSFT", 500)]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    assert _by_symbol(out) == {"AAPL": 500, "MSFT": 500}


def test_sector_exposure_skips_unknown_sectors_instead_of_merging_them():
    """契约 §2.1.2：行业未知的标的必须**跳过**，不得并成同一个巨大敞口而全部砍掉。"""
    # Arrange: 两个未知行业标的合计 80% 敞口；若被并成一个行业会被砍到 20%
    ctx = _ctx({"UNK1": 100.0, "UNK2": 100.0}, cash=1_000_000.0)
    targets = [PortfolioTarget("UNK1", 4_000), PortfolioTarget("UNK2", 4_000)]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    assert _by_symbol(out) == {"UNK1": 4_000, "UNK2": 4_000}


def test_sector_exposure_known_and_unknown_do_not_contaminate_each_other():
    ctx = _ctx({"AAPL": 100.0, "MSFT": 100.0, "UNK1": 100.0}, cash=1_000_000.0)
    targets = [
        PortfolioTarget("AAPL", 2_000),
        PortfolioTarget("MSFT", 2_000),
        PortfolioTarget("UNK1", 5_000),
    ]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    assert _by_symbol(out) == {"AAPL": 1_000, "MSFT": 1_000, "UNK1": 5_000}


def test_sector_exposure_default_injection_is_a_noop_until_c4_lands():
    """本项目尚无行业字典（蓝图 C4）：默认注入一律返回 None ⇒ 模型不动任何目标。"""
    ctx = _ctx({"AAPL": 100.0, "MSFT": 100.0}, cash=1_000_000.0)
    targets = [PortfolioTarget("AAPL", 5_000), PortfolioTarget("MSFT", 5_000)]

    out = MaximumSectorExposure(max_exposure=0.20).manage_risk(ctx, targets)

    assert _by_symbol(out) == {"AAPL": 5_000, "MSFT": 5_000}


def test_sector_exposure_counts_shorts_by_absolute_notional():
    ctx = _ctx({"AAPL": 100.0, "MSFT": 100.0}, cash=1_000_000.0)
    targets = [PortfolioTarget("AAPL", 2_000), PortfolioTarget("MSFT", -2_000)]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    # 多空各 20% ⇒ 毛敞口 40%，按 0.5 缩减；空头缩减后仍为负
    assert _by_symbol(out) == {"AAPL": 1_000, "MSFT": -1_000}


def test_sector_exposure_rejects_out_of_range_threshold():
    with pytest.raises(ValueError, match="max_exposure"):
        MaximumSectorExposure(max_exposure=1.5)


# ── 5. TrailingStopRiskManagement ─────────────────────────────


def test_trailing_stop_fires_after_giving_back_the_peak():
    model = TrailingStopRiskManagement(max_drawdown=0.05)
    targets = [PortfolioTarget("AAPL", 100)]
    broker = _broker()
    broker.buy("AAPL", 100, Market.US)
    _advance(broker, {"AAPL": 100.0}, START)

    # 涨到 120 建立 +20% 的峰值
    _advance(broker, {"AAPL": 120.0}, START + _DAY)
    model.manage_risk(_context(broker, {"AAPL": 120.0}, START + _DAY), targets)

    # 回落到 118（较峰值回撤 2 个点，未触发）
    ok = model.manage_risk(_context(broker, {"AAPL": 118.0}, START + 2 * _DAY), targets)
    assert _by_symbol(ok) == {"AAPL": 100}

    # 回落到 114（收益率 +14%，较峰值 20% 回撤 6 个点 > 5%）
    hit = model.manage_risk(_context(broker, {"AAPL": 114.0}, START + 3 * _DAY), targets)
    assert _by_symbol(hit) == {"AAPL": 0}


def test_trailing_stop_uses_zero_as_initial_peak():
    """从未盈利过的仓位：峰值为 0，跌破 -5% 即触发（与 K4 追踪止损口径一致）。"""
    ctx = _ctx({"AAPL": 94.0}, opens={"AAPL": 100.0}, positions={"AAPL": 100})

    out = TrailingStopRiskManagement(max_drawdown=0.05).manage_risk(
        ctx, [PortfolioTarget("AAPL", 100)]
    )

    assert out[0].quantity == 0


def test_trailing_stop_resets_peak_when_adding_to_the_position():
    """契约 §2.1.3 / K-d 语义：同向加仓改变开仓基准，旧峰值必须一并重置。"""
    model = TrailingStopRiskManagement(max_drawdown=0.05)
    targets = [PortfolioTarget("AAPL", 200)]
    broker = _broker()
    broker.buy("AAPL", 100, Market.US)
    _advance(broker, {"AAPL": 100.0}, START)

    # 涨到 120 → 峰值 +20%
    _advance(broker, {"AAPL": 120.0}, START + _DAY)
    model.manage_risk(_context(broker, {"AAPL": 120.0}, START + _DAY), targets)

    # 在 140 加仓 100 股 ⇒ 平均成本 120，K-d 会把 Trade 的收益率极值重置
    broker.buy("AAPL", 100, Market.US)
    _advance(broker, {"AAPL": 140.0}, START + 2 * _DAY)
    assert broker.open_trades["AAPL"].open_price == pytest.approx(120.0)

    # 价格回到 120 ⇒ 相对新基准收益率 0。沿用旧峰值 20% 会被立刻打掉；
    # 正确行为是峰值随基准一起归零 ⇒ 不触发
    out = model.manage_risk(_context(broker, {"AAPL": 120.0}, START + 3 * _DAY), targets)

    assert _by_symbol(out) == {"AAPL": 200}


def test_trailing_stop_forgets_state_once_the_position_is_closed():
    model = TrailingStopRiskManagement(max_drawdown=0.05)
    targets = [PortfolioTarget("AAPL", 100)]
    broker = _broker()
    broker.buy("AAPL", 100, Market.US)
    _advance(broker, {"AAPL": 100.0}, START)
    _advance(broker, {"AAPL": 120.0}, START + _DAY)
    model.manage_risk(_context(broker, {"AAPL": 120.0}, START + _DAY), targets)

    # 平掉持仓 → Trade 消失 → 峰值状态必须一起清掉
    broker.sell("AAPL", 100, Market.US)
    _advance(broker, {"AAPL": 114.0}, START + 2 * _DAY)
    assert "AAPL" not in broker.open_trades

    out = model.manage_risk(_context(broker, {"AAPL": 114.0}, START + 2 * _DAY), targets)
    assert _by_symbol(out) == {"AAPL": 100}


def test_trailing_stop_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="max_drawdown"):
        TrailingStopRiskManagement(max_drawdown=0.0)


# ── 6. 数据缺失时的降级路径（不得静默做错事）──────────────────


def _suspended(ctx: PortfolioContext) -> PortfolioContext:
    """把上下文改成「本时点无行情、且券商从未见过该标的」。"""
    return PortfolioContext(
        time=ctx.time,
        bars={},
        symbols=ctx.symbols,
        broker=_broker(),                     # 全新券商 → 无最后已知价、无 Trade
        histories=ctx.histories,
        market=Market.US,
    )


class _NoTradeBroker:
    """模拟不提供 `open_trades` 的券商（例如实盘的 OMS 上下文）。"""

    positions = None


@pytest.mark.parametrize(
    "model",
    [
        MaximumDrawdownPerSecurity(max_drawdown=0.05),
        MaximumUnrealizedProfitPerSecurity(max_profit=0.05),
        TrailingStopRiskManagement(max_drawdown=0.05),
    ],
    ids=["drawdown", "profit", "trailing"],
)
def test_trade_based_models_are_noop_without_open_trades(model):
    ctx = _ctx({"AAPL": 100.0})
    ctx.broker = _NoTradeBroker()             # type: ignore[assignment]
    targets = [PortfolioTarget("AAPL", 100)]

    assert model.manage_risk(ctx, targets) == targets


@pytest.mark.parametrize(
    "model",
    [
        MaximumDrawdownPerSecurity(max_drawdown=0.05),
        MaximumUnrealizedProfitPerSecurity(max_profit=0.05),
    ],
    ids=["drawdown", "profit"],
)
def test_trade_based_models_skip_symbols_without_a_usable_price(model):
    """有持仓但本时点无任何可用价格：不能拿 0 当价格判成巨亏/巨盈。"""
    ctx = _ctx({"AAPL": 90.0}, opens={"AAPL": 100.0}, positions={"AAPL": 100})
    trades = ctx.broker.open_trades
    blind = _suspended(ctx)
    blind.broker._open_trades = dict(trades)          # noqa: SLF001
    targets = [PortfolioTarget("AAPL", 100)]

    assert model.manage_risk(blind, targets) == targets


def test_portfolio_drawdown_skips_when_equity_is_not_available():
    broker = _broker(cash=0.0)
    ctx = _context(broker, {"AAPL": 100.0}, START)
    targets = [PortfolioTarget("AAPL", 100)]

    model = MaximumDrawdownPortfolio(max_drawdown=0.05)

    assert model.manage_risk(ctx, targets) == targets
    assert model.high_water_mark is None               # 高水位也不该被 0 污染


def test_sector_exposure_skips_when_equity_is_not_available():
    ctx = _context(_broker(cash=0.0), {"AAPL": 100.0}, START)
    targets = [PortfolioTarget("AAPL", 100)]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    assert out == targets


def test_sector_exposure_skips_symbols_without_a_usable_price():
    ctx = _suspended(_ctx({"AAPL": 100.0}, cash=1_000_000.0))
    ctx.broker._cash = 1_000_000.0                     # noqa: SLF001
    targets = [PortfolioTarget("AAPL", 9_000)]

    out = MaximumSectorExposure(max_exposure=0.20, sector_of=_sector_of).manage_risk(ctx, targets)

    assert out == targets


# ── 7. K4 策略级止损 × L2 组合风控 同时开启 ────────────────────


class _AlwaysLongAlpha(AlphaModel):
    """每根 bar 都对全部标的给出看多观点，逼出「每根 bar 都想满仓」的最坏情形。"""

    name = "always_long"

    def update(self, ctx: PortfolioContext) -> list[Insight]:
        return [
            Insight(
                symbol=symbol,
                direction=InsightDirection.UP,
                period=_DAY,
                generated_at=ctx.time,
            )
            for symbol in sorted(ctx.bars)
        ]


class _StopLossFramework(FrameworkStrategy):
    """同时开启 K4 类级止损与 L2 组合风控。"""

    def exit_rules(self) -> ExitRules:
        return ExitRules(stoploss=-0.05)


#: 三段式行情，保证两条平仓路径都被走到：
#: ① 横盘建仓 ② 温和下跌 -3.5%（只触发 L2 的 -3%，不触发 K4 的 -5%）
#: ③ 重新建仓后连续暴跌（K4 与 L2 在同一根 bar 上同时触发 —— 超卖的高危场景）
_CRASH = [100.0, 100.0, 100.0, 96.5, 96.5, 96.5, 88.0, 80.0, 72.0, 65.0, 58.0]


def _crash_bars(symbols: list[str]) -> dict[str, list[Bar]]:
    return {
        s: [_bar(s, p, START + timedelta(days=i)) for i, p in enumerate(_CRASH)]
        for s in symbols
    }


def _run_k4_and_l2(symbols: list[str]):
    strategy = _StopLossFramework(
        alpha=_AlwaysLongAlpha(),
        portfolio_construction=EqualWeightingPCM(),
        risk=MaximumDrawdownPerSecurity(max_drawdown=0.03),
    )
    config = PortfolioBacktestConfig(
        initial_cash=100_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        commission_model=_ZeroCommission(),
    )
    return PortfolioBacktestEngine(config).run(strategy, _crash_bars(symbols))


def test_k4_stoploss_and_l2_risk_together_never_oversell():
    """契约 §2.2：两者都会平仓，本期不互斥，但绝不允许超卖或出现负持仓。"""
    symbols = ["AAPL", "MSFT"]

    result = _run_k4_and_l2(symbols)

    running = dict.fromkeys(symbols, 0)
    for fill in result.fills:
        running[fill["symbol"]] += fill["qty"] if fill["side"] == "BUY" else -fill["qty"]
        assert running[fill["symbol"]] >= 0, f"{fill['symbol']} 在 {fill['filled_at']} 变成负持仓"
    assert result.final_value > 0


def test_k4_stoploss_and_l2_risk_both_actually_fire():
    """确认这个用例真的把两条平仓路径都跑到了，而不是只有一条在起作用。"""
    result = _run_k4_and_l2(["AAPL", "MSFT"])

    reasons = {f["exit_reason"] for f in result.fills if f["exit_reason"]}

    assert EXIT_STOP_LOSS in reasons                  # K4 策略级止损
    assert RISK_TAG_MAX_DRAWDOWN in reasons           # L2 组合风控
