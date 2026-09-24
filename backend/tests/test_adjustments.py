"""
复权与公司行为测试（Wave K-b / K8）

验收（契约 waveKb §4.4/§4.5）：
  - 已知拆股案例（AAPL 2020-08-31 一拆四）复权后价格连续
  - 分红现金流入账
  - adjust_prices=False 时结果与不接入完全一致
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from app.data.adjustments import (
    CorporateAction,
    apply_adjustments,
    build_adjustment_factors,
    build_volume_factors,
    dividend_cash_per_share,
    fetch_corporate_actions,
)
from app.data.models import Bar, Frequency, Market
from app.engine.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    _apply_price_adjustments,
)
from app.strategy.base import StrategyBase

SYMBOL = "AAPL"
START = date(2020, 8, 27)


def _bar(day: date, close: float, volume: int = 1_000_000) -> Bar:
    return Bar(
        time=datetime(day.year, day.month, day.day, tzinfo=UTC),
        symbol=SYMBOL,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        vwap=close,
    )


def _sessions(count: int, start: date = START) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


# ── AAPL 2020-08-31 一拆四 ────────────────────────────────────

# 除权前后的真实收盘价量级：499.23 → 129.04（≈ 1/4），中间无基本面变化
AAPL_SPLIT = CorporateAction(
    symbol=SYMBOL, ex_date=date(2020, 8, 31), kind="split", ratio=4.0
)
_SPLIT_SESSIONS = [
    date(2020, 8, 27), date(2020, 8, 28),     # 除权前
    date(2020, 8, 31), date(2020, 9, 1), date(2020, 9, 2),   # 除权日及之后
]
_RAW_CLOSES = [500.04, 499.23, 129.04, 134.18, 131.40]


@pytest.fixture
def split_bars() -> list[Bar]:
    return [_bar(d, c) for d, c in zip(_SPLIT_SESSIONS, _RAW_CLOSES, strict=True)]


def test_split_factor_is_quarter_before_ex_date(split_bars: list[Bar]) -> None:
    sessions = [b.time.date() for b in split_bars]

    factors = build_adjustment_factors([AAPL_SPLIT], sessions)

    # 除权日（含）之后为 1.0，之前为 1/4
    assert factors.loc[pd.Timestamp(2020, 8, 27)] == pytest.approx(0.25)
    assert factors.loc[pd.Timestamp(2020, 8, 28)] == pytest.approx(0.25)
    assert factors.loc[pd.Timestamp(2020, 8, 31)] == pytest.approx(1.0)
    assert factors.loc[pd.Timestamp(2020, 9, 2)] == pytest.approx(1.0)


def test_split_adjustment_makes_price_continuous(split_bars: list[Bar]) -> None:
    """核心验收：复权后跨除权日不再出现 -74% 的虚假暴跌。"""
    sessions = [b.time.date() for b in split_bars]
    factors = build_adjustment_factors([AAPL_SPLIT], sessions)

    adjusted = apply_adjustments(split_bars, factors)
    closes = [b.close for b in adjusted]

    raw_jump = (_RAW_CLOSES[2] - _RAW_CLOSES[1]) / _RAW_CLOSES[1]
    adj_jump = (closes[2] - closes[1]) / closes[1]

    assert raw_jump < -0.70          # 原始数据是断崖
    assert abs(adj_jump) < 0.05      # 复权后连续
    assert closes[1] == pytest.approx(499.23 / 4)


def test_latest_price_stays_real(split_bars: list[Bar]) -> None:
    """口径：最新价 = 真实价，便于与实盘对账。"""
    sessions = [b.time.date() for b in split_bars]
    factors = build_adjustment_factors([AAPL_SPLIT], sessions)

    adjusted = apply_adjustments(split_bars, factors)

    assert adjusted[-1].close == pytest.approx(split_bars[-1].close)
    assert adjusted[-1].volume == split_bars[-1].volume


def test_split_scales_volume_inversely(split_bars: list[Bar]) -> None:
    sessions = [b.time.date() for b in split_bars]
    factors = build_adjustment_factors([AAPL_SPLIT], sessions)
    volume_factors = build_volume_factors([AAPL_SPLIT], sessions)

    adjusted = apply_adjustments(split_bars, factors, volume_factors)

    assert adjusted[0].volume == split_bars[0].volume * 4
    assert adjusted[-1].volume == split_bars[-1].volume


def test_split_preserves_turnover(split_bars: list[Bar]) -> None:
    """价 ×1/4、量 ×4 → 成交额不变，这是拆股复权正确性的守恒量。"""
    sessions = [b.time.date() for b in split_bars]
    adjusted = apply_adjustments(
        split_bars,
        build_adjustment_factors([AAPL_SPLIT], sessions),
        build_volume_factors([AAPL_SPLIT], sessions),
    )

    for raw, adj in zip(split_bars, adjusted, strict=True):
        assert adj.close * adj.volume == pytest.approx(raw.close * raw.volume, rel=1e-9)


def test_adjustment_returns_new_bars_without_mutating_input(split_bars: list[Bar]) -> None:
    """Bar 是 frozen dataclass，复权必须产出新对象。"""
    sessions = [b.time.date() for b in split_bars]
    original_close = split_bars[0].close

    adjusted = apply_adjustments(split_bars, build_adjustment_factors([AAPL_SPLIT], sessions))

    assert split_bars[0].close == original_close
    assert adjusted[0] is not split_bars[0]
    assert adjusted[-1] is split_bars[-1]   # 因子为 1 时可安全复用同一对象


def test_ohlc_all_scaled_consistently(split_bars: list[Bar]) -> None:
    sessions = [b.time.date() for b in split_bars]
    adjusted = apply_adjustments(split_bars, build_adjustment_factors([AAPL_SPLIT], sessions))

    raw, adj = split_bars[0], adjusted[0]
    for field in ("open", "high", "low", "close", "vwap"):
        assert getattr(adj, field) == pytest.approx(getattr(raw, field) * 0.25)


def test_reverse_split_scales_up_history() -> None:
    """N 合 1（缩股）：ratio = 1/N，历史价上调。"""
    sessions = _sessions(3)
    bars = [_bar(d, 1.0) for d in sessions]
    action = CorporateAction(SYMBOL, sessions[2], "split", ratio=0.1)   # 10 合 1

    adjusted = apply_adjustments(bars, build_adjustment_factors([action], sessions))

    assert adjusted[0].close == pytest.approx(10.0)
    assert adjusted[2].close == pytest.approx(1.0)


def test_multiple_splits_compound() -> None:
    sessions = _sessions(5)
    actions = [
        CorporateAction(SYMBOL, sessions[2], "split", ratio=2.0),
        CorporateAction(SYMBOL, sessions[4], "split", ratio=3.0),
    ]

    factors = build_adjustment_factors(actions, sessions)

    assert factors.iloc[0] == pytest.approx(1 / 6)   # 两次拆股累乘
    assert factors.iloc[3] == pytest.approx(1 / 3)
    assert factors.iloc[4] == pytest.approx(1.0)


# ── 分红 ──────────────────────────────────────────────────────

def test_dividend_factor_uses_prior_close() -> None:
    sessions = _sessions(3)
    bars = [_bar(d, 100.0) for d in sessions]
    closes = pd.Series([b.close for b in bars], index=pd.DatetimeIndex(sessions))
    action = CorporateAction(SYMBOL, sessions[2], "dividend", amount=2.0)

    factors = build_adjustment_factors([action], sessions, prices=closes)

    assert factors.iloc[0] == pytest.approx(0.98)   # 1 - 2/100
    assert factors.iloc[2] == pytest.approx(1.0)


def test_dividend_without_prices_skips_price_adjustment(caplog) -> None:
    """没有前收价就无法折算比例 —— 只保留现金流，并留下 warning，不静默。"""
    sessions = _sessions(3)
    action = CorporateAction(SYMBOL, sessions[2], "dividend", amount=2.0)

    with caplog.at_level("WARNING"):
        factors = build_adjustment_factors([action], sessions)

    assert (factors == 1.0).all()
    assert any("缺少除权前收盘价" in r.message for r in caplog.records)


def test_dividend_does_not_change_volume() -> None:
    sessions = _sessions(3)
    action = CorporateAction(SYMBOL, sessions[2], "dividend", amount=2.0)

    assert (build_volume_factors([action], sessions) == 1.0).all()


def test_dividend_cash_per_share_table() -> None:
    sessions = _sessions(3)
    actions = [
        CorporateAction(SYMBOL, sessions[1], "dividend", amount=0.5),
        CorporateAction(SYMBOL, sessions[1], "dividend", amount=0.25),   # 同日两笔累加
        CorporateAction(SYMBOL, sessions[2], "split", ratio=2.0),
    ]

    cash = dividend_cash_per_share(actions)

    assert cash == {sessions[1]: pytest.approx(0.75)}


def test_dividend_cash_scaled_into_adjusted_price_space() -> None:
    """持仓股数在复权空间，分红也必须折算到同一空间，否则现金流入被高估。"""
    sessions = _sessions(3)
    actions = [
        CorporateAction(SYMBOL, sessions[0], "dividend", amount=1.0),
        CorporateAction(SYMBOL, sessions[2], "split", ratio=4.0),
    ]
    factors = build_adjustment_factors(actions, sessions)

    cash = dividend_cash_per_share(actions, factors)

    assert cash[sessions[0]] == pytest.approx(0.25)   # 1.0 × 因子 0.25


# ── 输入校验 ──────────────────────────────────────────────────

def test_split_requires_positive_ratio() -> None:
    with pytest.raises(ValueError, match="ratio"):
        CorporateAction(SYMBOL, START, "split", ratio=0.0)


def test_dividend_requires_positive_amount() -> None:
    with pytest.raises(ValueError, match="amount"):
        CorporateAction(SYMBOL, START, "dividend", amount=None)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知公司行为类型"):
        CorporateAction(SYMBOL, START, "spinoff")   # type: ignore[arg-type]


def test_corporate_action_is_immutable() -> None:
    action = CorporateAction(SYMBOL, START, "split", ratio=2.0)
    with pytest.raises((AttributeError, TypeError)):
        action.ratio = 3.0   # type: ignore[misc]


def test_merger_is_skipped_with_warning(caplog) -> None:
    """契约 §5：merger 先留字段不实现 —— 但必须显式告警，不能装作调整过了。"""
    sessions = _sessions(3)
    action = CorporateAction(SYMBOL, sessions[2], "merger")

    with caplog.at_level("WARNING"):
        factors = build_adjustment_factors([action], sessions)

    assert (factors == 1.0).all()
    assert any("merger" in r.message for r in caplog.records)


def test_empty_inputs_are_safe() -> None:
    assert build_adjustment_factors([], []).empty
    assert apply_adjustments([], pd.Series(dtype=float)) == []
    bars = [_bar(d, 100.0) for d in _sessions(2)]
    assert apply_adjustments(bars, pd.Series(dtype=float)) == bars


def test_no_actions_yields_identity_factors() -> None:
    sessions = _sessions(4)
    assert (build_adjustment_factors([], sessions) == 1.0).all()


def test_bar_outside_factor_index_is_untouched() -> None:
    """因子里没有的日期原样返回，绝不静默丢 bar。"""
    sessions = _sessions(3)
    bars = [_bar(d, 100.0) for d in _sessions(5)]
    factors = build_adjustment_factors(
        [CorporateAction(SYMBOL, sessions[2], "split", ratio=2.0)], sessions
    )

    adjusted = apply_adjustments(bars, factors)

    assert len(adjusted) == 5
    assert adjusted[4].close == pytest.approx(100.0)


def test_fetch_corporate_actions_degrades_gracefully(caplog) -> None:
    """外部数据源不可用时返回空列表并 warning，绝不中断回测。"""
    with caplog.at_level("WARNING"):
        actions = fetch_corporate_actions("NO_SUCH_SYMBOL_XYZ", Market.US)

    assert actions == []


# ── 引擎接入 ──────────────────────────────────────────────────

class _BuyAndHold(StrategyBase):
    name = "buy_and_hold_test"

    def on_bar(self, ctx) -> None:
        if ctx.qty == 0:
            ctx.buy(100)


def _engine_bars(count: int = 10) -> list[Bar]:
    return [_bar(d, 100.0) for d in _sessions(count, start=date(2024, 3, 1))]


def test_adjust_prices_false_is_the_default_and_changes_nothing() -> None:
    """红线：默认关闭时逐笔结果必须与不接入完全一致。"""
    bars = _engine_bars()
    cfg = BacktestConfig(initial_cash=100_000.0, market=Market.US)

    assert cfg.adjust_prices is False
    assert cfg.calendar is None
    assert cfg.corporate_actions is None

    baseline = BacktestEngine(cfg).run(_BuyAndHold(), bars)
    again = BacktestEngine(BacktestConfig(initial_cash=100_000.0)).run(_BuyAndHold(), bars)

    assert baseline.final_value == again.final_value
    assert "adjustments" not in baseline.report
    assert "data_gaps" not in baseline.report


def test_engine_credits_dividend_cash_on_ex_date() -> None:
    bars = _engine_bars()
    ex_date = bars[5].time.date()
    action = CorporateAction(SYMBOL, ex_date, "dividend", amount=1.0)

    with_div = BacktestEngine(
        BacktestConfig(
            initial_cash=100_000.0, adjust_prices=True, corporate_actions=[action]
        )
    ).run(_BuyAndHold(), bars)
    without = BacktestEngine(BacktestConfig(initial_cash=100_000.0)).run(_BuyAndHold(), bars)

    assert with_div.report["adjustments"]["dividend_cash"] > 0
    # 持有 100 股、每股 1 元（除权日因子为 1）→ 现金多 100
    assert with_div.report["adjustments"]["dividend_cash"] == pytest.approx(100.0, abs=1.0)
    assert with_div.final_value > without.final_value


def test_engine_skips_dividend_when_flat() -> None:
    """空仓时不该凭空进账。"""
    bars = _engine_bars()
    action = CorporateAction(SYMBOL, bars[0].time.date(), "dividend", amount=1.0)

    class _NeverTrades(StrategyBase):
        name = "never_trades"

        def on_bar(self, ctx) -> None:
            return

    result = BacktestEngine(
        BacktestConfig(
            initial_cash=100_000.0, adjust_prices=True, corporate_actions=[action]
        )
    ).run(_NeverTrades(), bars)

    assert result.report["adjustments"]["dividend_cash"] == 0.0
    assert result.final_value == pytest.approx(100_000.0)


def test_engine_applies_split_before_matching() -> None:
    """开启复权后，撮合看到的是复权价（本例历史价被下调到 1/2）。"""
    bars = _engine_bars()
    action = CorporateAction(SYMBOL, bars[-1].time.date(), "split", ratio=2.0)

    result = BacktestEngine(
        BacktestConfig(
            initial_cash=100_000.0, adjust_prices=True, corporate_actions=[action]
        )
    ).run(_BuyAndHold(), bars)

    # 买入价 ≈ 50（100 的一半）而不是 100
    assert result.fills[0]["price"] == pytest.approx(50.0, rel=0.01)
    assert result.report["adjustments"]["applied"] is True


# ── 分钟级数据（同一天多根 bar）────────────────────────────────
#
# 回归用例：`_apply_price_adjustments` 曾按**每根 bar** 建 DatetimeIndex，
# 分钟级下同一天多根 bar → 索引重复 → `prices.loc[key]` 返回 Series 而非标量，
# `float()` 直接抛 TypeError，开了 adjust_prices 的分钟级回测必崩。

def test_intraday_bars_with_dividend_do_not_crash_on_duplicate_session_index() -> None:
    # Arrange: 3 天 × 每天 4 根分钟 bar，第 3 天除权分红
    bars = [
        Bar(
            time=datetime(2024, 1, 2 + d, 10 + m, 0, tzinfo=UTC),
            symbol="AAPL", market=Market.US, frequency=Frequency.MIN_1,
            open=100.0, high=101.0, low=99.0, close=100.0 + m, volume=1_000,
        )
        for d in range(3)
        for m in range(4)
    ]
    cfg = BacktestConfig(
        initial_cash=1_000_000.0, market=Market.US, adjust_prices=True,
        corporate_actions=[
            CorporateAction(symbol="AAPL", ex_date=date(2024, 1, 4),
                            kind="dividend", amount=1.0)
        ],
    )

    # Act
    adjusted, dividend_cash = _apply_price_adjustments(bars, cfg)

    # Assert
    assert len(adjusted) == len(bars)
    assert dividend_cash == {date(2024, 1, 4): 1.0}


def test_turnover_is_rescaled_consistently_with_price_and_volume() -> None:
    # Arrange: 1 拆 2 —— 价格减半、股数翻倍，真实成交额应保持不变
    bars = [
        Bar(
            time=datetime(2024, 1, d, tzinfo=UTC), symbol="AAPL", market=Market.HK,
            frequency=Frequency.DAY_1, open=100.0, high=101.0, low=99.0,
            close=100.0, volume=1_000, turnover=100_000.0,
        )
        for d in (2, 3, 4)
    ]
    cfg = BacktestConfig(
        initial_cash=1_000_000.0, market=Market.HK, adjust_prices=True,
        corporate_actions=[
            CorporateAction(symbol="AAPL", ex_date=date(2024, 1, 4),
                            kind="split", ratio=2.0)
        ],
    )

    # Act
    adjusted, _ = _apply_price_adjustments(bars, cfg)

    # Assert: 复权后每根 bar 的 turnover 仍等于 close × volume
    for bar in adjusted:
        assert bar.turnover is not None
        assert bar.turnover == pytest.approx(bar.close * bar.volume, rel=1e-9)
