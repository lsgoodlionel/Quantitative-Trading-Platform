"""公式因子 → 策略适配器测试（V3 Wave A-a / G1）

对应契约 docs/contracts/waveAa-factor-strategy-adapter.md §四 验收 1/2/4。
"""

from __future__ import annotations

import math
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
from app.engine.framework import (
    EqualWeightingPCM,
    FormulaFactorAlphaModel,
    FrameworkStrategy,
    ImmediateExecutionModel,
    InsightDirection,
    InsightWeightingPCM,
    OptimizerPCM,
)
from app.strategy.context import PortfolioContext
from app.strategy.factor_strategy import (
    MIN_HISTORY,
    PORTFOLIO_METHODS,
    FactorStrategySpec,
    build_factor_strategy,
)

START = datetime(2024, 1, 2, tzinfo=UTC)
DAY = timedelta(days=1)
SYMBOLS = ["AAPL", "AMZN", "MSFT", "NVDA", "TSLA"]


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(symbol: str, price: float, ts: datetime) -> Bar:
    return Bar(
        time=ts,
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=price,
        high=price * 1.001,
        low=price * 0.999,
        close=price,
        volume=500_000,
    )


def _universe_bars(n: int = 180) -> dict[str, list[Bar]]:
    """5 个标的、走势各异，足以让 60 期滚动算子与协方差估计有意义。"""
    return {
        symbol: [
            _bar(
                symbol,
                round(100.0 + 8 * math.sin((i + k * 7) / 11.0) + i * (0.02 + 0.015 * k), 4),
                START + i * DAY,
            )
            for i in range(n)
        ]
        for k, symbol in enumerate(SYMBOLS)
    }


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([START + i * DAY for i in range(len(closes))])
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [500_000.0] * len(closes),
        },
        index=index,
    )


def _context(histories: dict[str, pd.DataFrame]) -> PortfolioContext:
    """由各标的历史构造一个「最后一根 bar」上下文。"""
    prices = {s: float(h["close"].iloc[-1]) for s, h in histories.items()}
    symbols = sorted(prices)
    broker = PortfolioBroker(
        initial_cash=1_000_000.0,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
    )
    bars = {s: _bar(s, prices[s], START) for s in symbols}
    broker.process_bars(START, bars)
    return PortfolioContext(
        time=START,
        bars=bars,
        symbols=symbols,
        broker=broker,
        histories=histories,
        market=Market.US,
    )


def _histories(n: int = 180) -> dict[str, pd.DataFrame]:
    return {
        symbol: _frame([b.close for b in bars])
        for symbol, bars in _universe_bars(n).items()
    }


def _spec(**overrides) -> FactorStrategySpec:
    base = {"formula": "MOM20", "universe": SYMBOLS}
    return FactorStrategySpec(**(base | overrides))


def _run(spec: FactorStrategySpec, bars=None, *, allow_short: bool = False):
    config = PortfolioBacktestConfig(
        initial_cash=1_000_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        commission_model=_ZeroCommission(),
        allow_short=allow_short,
    )
    return PortfolioBacktestEngine(config).run(
        build_factor_strategy(spec), bars if bars is not None else _universe_bars()
    )


# ── spec 序列化往返 ───────────────────────────────────────────────


class TestSpecSerialization:
    def test_round_trip_is_lossless(self) -> None:
        spec = _spec(
            formula="MOM20 ATR_RATIO DIV",
            long_quantile=0.3,
            short_quantile=0.25,
            rebalance_days=7,
            portfolio_method="hrp",
            max_positions=3,
        )

        restored = FactorStrategySpec.from_dict(spec.to_dict())

        assert restored == spec

    def test_to_dict_is_json_serializable(self) -> None:
        import json

        payload = json.dumps(_spec().to_dict())

        assert FactorStrategySpec.from_dict(json.loads(payload)) == _spec()

    def test_from_dict_fills_defaults(self) -> None:
        restored = FactorStrategySpec.from_dict(
            {"formula": "MOM20", "universe": ["aapl", "msft"]}
        )

        assert restored.long_quantile == pytest.approx(0.2)
        assert restored.short_quantile is None
        assert restored.rebalance_days == 5
        assert restored.portfolio_method == "equal_weight"
        assert restored.max_positions is None

    def test_from_dict_rejects_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            FactorStrategySpec.from_dict({"formula": "MOM20", "universe": SYMBOLS, "boom": 1})

    def test_universe_is_normalized_and_deduplicated(self) -> None:
        spec = FactorStrategySpec(formula="MOM20", universe=[" aapl ", "AAPL", "msft"])

        assert spec.universe == ("AAPL", "MSFT")

    def test_tokens_split_from_formula(self) -> None:
        assert _spec(formula="  MOM20   ATR_RATIO  DIV ").tokens == [
            "MOM20",
            "ATR_RATIO",
            "DIV",
        ]

    def test_spec_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            _spec().formula = "RET1"      # type: ignore[misc]


# ── spec 校验 ────────────────────────────────────────────────────


class TestSpecValidation:
    def test_empty_formula_rejected(self) -> None:
        with pytest.raises(ValueError, match="公式"):
            _spec(formula="   ")

    def test_unknown_token_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知 token"):
            _spec(formula="MOM20 NOT_A_TOKEN")

    def test_unbalanced_formula_rejected(self) -> None:
        with pytest.raises(ValueError, match="不平衡"):
            _spec(formula="MOM20 ATR_RATIO")

    def test_universe_below_two_rejected(self) -> None:
        with pytest.raises(ValueError, match="标的池"):
            _spec(universe=["AAPL"])

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.1])
    def test_long_quantile_out_of_range_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="long_quantile"):
            _spec(long_quantile=bad)

    def test_negative_short_quantile_rejected(self) -> None:
        with pytest.raises(ValueError, match="short_quantile"):
            _spec(short_quantile=-0.1)

    def test_overlapping_long_short_bands_rejected(self) -> None:
        # 多头取前 60%、空头取后 60% ⇒ 两段重叠，同一标的会同时被判多空
        with pytest.raises(ValueError, match="重叠"):
            _spec(long_quantile=0.6, short_quantile=0.6)

    @pytest.mark.parametrize("bad", [0, -3])
    def test_non_positive_rebalance_days_rejected(self, bad: int) -> None:
        with pytest.raises(ValueError, match="rebalance_days"):
            _spec(rebalance_days=bad)

    def test_unknown_portfolio_method_rejected(self) -> None:
        with pytest.raises(ValueError, match="portfolio_method"):
            _spec(portfolio_method="mystery_method")

    def test_non_positive_max_positions_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_positions"):
            _spec(max_positions=0)


# ── 装配 ─────────────────────────────────────────────────────────


class TestBuildFactorStrategy:
    def test_builds_framework_strategy_with_formula_alpha(self) -> None:
        strategy = build_factor_strategy(_spec())

        assert isinstance(strategy, FrameworkStrategy)
        assert isinstance(strategy.alpha.factor, FormulaFactorAlphaModel)

    def test_alpha_is_throttled_to_the_rebalance_cadence(self) -> None:
        """节奏内的第二根 bar 不该再出观点，否则 rebalance_days 形同虚设。"""
        strategy = build_factor_strategy(_spec(rebalance_days=5))
        ctx = _context(_histories())

        first = strategy.alpha.update(ctx)
        second = strategy.alpha.update(ctx)

        assert first
        assert not second

    def test_spec_is_carried_on_params_for_replay(self) -> None:
        spec = _spec(portfolio_method="hrp")
        strategy = build_factor_strategy(spec)

        assert all(strategy.param(k) == v for k, v in spec.to_dict().items())

    def test_equal_weight_uses_equal_weighting_pcm(self) -> None:
        assert isinstance(build_factor_strategy(_spec()).pcm, EqualWeightingPCM)

    def test_insight_weight_uses_insight_weighting_pcm(self) -> None:
        strategy = build_factor_strategy(_spec(portfolio_method="insight_weight"))

        assert isinstance(strategy.pcm, InsightWeightingPCM)

    @pytest.mark.parametrize("method", ["hrp", "risk_parity", "min_volatility"])
    def test_optimizer_methods_use_optimizer_pcm(self, method: str) -> None:
        assert isinstance(build_factor_strategy(_spec(portfolio_method=method)).pcm, OptimizerPCM)

    def test_rebalance_days_drives_pcm_period(self) -> None:
        strategy = build_factor_strategy(_spec(rebalance_days=9))

        assert strategy.pcm.rebalance_period == timedelta(days=9)

    def test_every_declared_method_is_buildable(self) -> None:
        for method in PORTFOLIO_METHODS:
            assert build_factor_strategy(_spec(portfolio_method=method)) is not None


# ── 各参数确实生效 ────────────────────────────────────────────────


def _insights(spec: FactorStrategySpec, histories: dict[str, pd.DataFrame]):
    strategy = build_factor_strategy(spec)
    return strategy.alpha.update(_context(histories))


class TestParametersTakeEffect:
    def test_long_quantile_controls_how_many_go_long(self) -> None:
        histories = _histories()

        wide = _insights(_spec(long_quantile=0.8), histories)
        narrow = _insights(_spec(long_quantile=0.2), histories)

        n_wide = sum(1 for i in wide if i.direction is InsightDirection.UP)
        n_narrow = sum(1 for i in narrow if i.direction is InsightDirection.UP)
        assert n_wide > n_narrow >= 1

    def test_long_quantile_is_a_top_fraction_not_a_cut_point(self) -> None:
        """spec 的 long_quantile 是「取前百分之几」：5 个标的取前 20% ⇒ 恰好 1 个多头。"""
        insights = _insights(_spec(long_quantile=0.2), _histories())

        assert sum(1 for i in insights if i.direction is InsightDirection.UP) == 1

    def test_short_quantile_none_means_long_only(self) -> None:
        insights = _insights(_spec(short_quantile=None), _histories())

        assert all(i.direction is not InsightDirection.DOWN for i in insights)

    def test_short_quantile_produces_short_insights(self) -> None:
        insights = _insights(_spec(long_quantile=0.2, short_quantile=0.2), _histories())

        assert any(i.direction is InsightDirection.DOWN for i in insights)

    def test_rebalance_days_drives_insight_period(self) -> None:
        insights = _insights(_spec(rebalance_days=3), _histories())

        assert all(i.period == timedelta(days=3) for i in insights)

    def test_longer_rebalance_period_trades_less(self) -> None:
        frequent = _run(_spec(rebalance_days=2))
        rare = _run(_spec(rebalance_days=30))

        assert len(rare.fills) < len(frequent.fills)

    def test_optimizer_method_weights_sum_to_one(self) -> None:
        spec = _spec(portfolio_method="hrp", long_quantile=1.0)
        strategy = build_factor_strategy(spec)
        ctx = _context(_histories())

        percents = strategy.pcm.determine_target_percent(ctx, strategy.alpha.update(ctx))

        assert sum(abs(w) for w in percents.values()) == pytest.approx(1.0)

    def test_max_positions_caps_the_number_of_targets(self) -> None:
        spec = _spec(long_quantile=1.0, max_positions=2)
        strategy = build_factor_strategy(spec)
        ctx = _context(_histories())

        percents = strategy.pcm.determine_target_percent(ctx, strategy.alpha.update(ctx))

        assert sum(1 for w in percents.values() if w != 0.0) == 2
        assert sum(abs(w) for w in percents.values()) == pytest.approx(1.0)

    def test_max_positions_keeps_the_highest_weights(self) -> None:
        unlimited = build_factor_strategy(_spec(long_quantile=1.0, portfolio_method="insight_weight"))
        capped = build_factor_strategy(
            _spec(long_quantile=1.0, portfolio_method="insight_weight", max_positions=2)
        )
        ctx = _context(_histories())

        full = unlimited.pcm.determine_target_percent(ctx, unlimited.alpha.update(ctx))
        few = capped.pcm.determine_target_percent(ctx, capped.alpha.update(ctx))

        best = sorted(full, key=lambda s: -abs(full[s]))[:2]
        assert sorted(s for s, w in few.items() if w != 0.0) == sorted(best)

    def test_max_positions_above_universe_is_a_noop(self) -> None:
        spec = _spec(long_quantile=1.0, max_positions=99)
        strategy = build_factor_strategy(spec)
        ctx = _context(_histories())

        percents = strategy.pcm.determine_target_percent(ctx, strategy.alpha.update(ctx))

        assert sum(1 for w in percents.values() if w != 0.0) == len(SYMBOLS)


# ── 端到端：公式 → 净值曲线 ───────────────────────────────────────


class TestEndToEndBacktest:
    def test_formula_produces_trades_and_finite_metrics(self) -> None:
        result = _run(_spec(formula="MOM20 ATR_RATIO DIV", long_quantile=0.4))

        assert result.fills, "因子策略没有产生任何成交"
        assert len(result.equity_curve) > MIN_HISTORY
        assert result.final_value > 0
        for name in ("total_return", "sharpe_ratio", "max_drawdown"):
            value = getattr(result.metrics, name)
            assert value is not None, f"{name} 缺失"
            assert math.isfinite(value), f"{name} 不是有限值"

    def test_optimizer_backed_spec_runs_end_to_end(self) -> None:
        result = _run(_spec(portfolio_method="hrp", long_quantile=0.6))

        assert result.fills
        assert math.isfinite(result.metrics.total_return)

    def test_long_short_spec_opens_short_positions(self) -> None:
        result = _run(
            _spec(long_quantile=0.2, short_quantile=0.2, rebalance_days=3),
            allow_short=True,
        )

        assert any(f["direction"] == "short" for f in result.fills)

    def test_equity_curve_has_no_nan(self) -> None:
        result = _run(_spec())

        assert not result.equity_curve.isna().any()


# ── rebalance_days 必须真的改变换手 ────────────────────────────────
#
# 回归用例：`FormulaFactorAlphaModel` 每根 bar 都出观点，而 `FrameworkStrategy`
# 一见新观点就调仓 —— `PCM.should_rebalance` 的节奏根本轮不到生效。
# 实现前实测 rebalance_days=2 与 =30 的成交数**完全相同**，该参数形同虚设。
#
# 单测已锁住节流器本身（同一节奏内第二次 update 不出观点），这里再从端到端
# 确认它真的传导到了成交上 —— 「节流器存在」与「换手真的变了」不是一回事。

def test_rebalance_days_actually_changes_turnover() -> None:
    # Arrange / Act：同一份数据、同一个公式，只改调仓节奏
    frequent = _run(_spec(rebalance_days=2))
    infrequent = _run(_spec(rebalance_days=30))

    # Assert：低频调仓的成交数必须显著更少
    assert len(frequent.fills) > 0, "样本本身要有成交，否则这个断言没有意义"
    assert len(infrequent.fills) < len(frequent.fills)


# ── 因子库条目直接可用（无需 expr → RPN 转译）────────────────────
#
# A-a 交付时把「因子库页签的回测按钮」列为未实现，理由是因子库的 expr
# （`($close-$open)/$open` 这类 Qlib 风格标注）与 RPN 词表（MOM20/ATR_RATIO）
# 是两套语言、没有可靠映射。
#
# 但 FactorSpec 携带的 `compute` 本身就是可调用对象 —— 根本不需要解析 expr。
# LibraryFactorAlphaModel 直接调它，复用同一套「分位 → 观点 → 权重」逻辑。

class TestLibraryFactorAlpha:
    @staticmethod
    def _spec_named(name: str):
        from app.quant.factor_lib.loader import generate_factor_library

        return next(s for s in generate_factor_library() if s.name == name)

    def test_library_spec_produces_insights(self) -> None:
        # Arrange
        from app.engine.framework.factor_alpha import LibraryFactorAlphaModel

        alpha = LibraryFactorAlphaModel(self._spec_named("KMID"), min_history=30)

        # Act
        insights = alpha.update(_context(_histories()))

        # Assert：每个标的一条观点，且至少有一条非 FLAT
        assert len(insights) == len(SYMBOLS)
        assert any(i.direction is not InsightDirection.FLAT for i in insights)

    def test_library_factor_runs_a_full_portfolio_backtest(self) -> None:
        # Arrange：这是 A-a 说做不到的那条链路
        from app.engine.framework.factor_alpha import LibraryFactorAlphaModel

        strategy = FrameworkStrategy(
            alpha=LibraryFactorAlphaModel(self._spec_named("KMID"), min_history=30),
            portfolio_construction=EqualWeightingPCM(),
            execution=ImmediateExecutionModel(),
        )
        config = PortfolioBacktestConfig(
            initial_cash=1_000_000.0,
            market=Market.US,
            slippage_model=NoSlippage(),
            commission_model=_ZeroCommission(),
        )

        # Act
        result = PortfolioBacktestEngine(config).run(strategy, _universe_bars())

        # Assert
        assert result.fills, "因子库条目应当能产生真实成交"
        assert math.isfinite(result.final_value)

    def test_min_history_defaults_from_the_factor_window(self) -> None:
        """带窗口的因子在历史不足时不该拿一堆 NaN 建仓。"""
        from app.engine.framework.factor_alpha import LibraryFactorAlphaModel
        from app.quant.factor_lib.loader import generate_factor_library

        spec = self._spec_named("KMID")
        windowed = next(s for s in generate_factor_library() if s.window >= 20)

        assert LibraryFactorAlphaModel(spec)._min_history >= 60
        assert LibraryFactorAlphaModel(windowed)._min_history >= windowed.window * 2
