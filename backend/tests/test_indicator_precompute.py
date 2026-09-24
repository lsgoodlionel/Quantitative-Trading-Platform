"""指标预算的框架保证（V3 Wave E-a / J2 重定向）

这份用例守的是**一条红线**：预算指标在全量帧上计算，策略绝不能借此拿到未来值。

覆盖：
- `ctx.ind` 的公开接口里没有任何能交出完整序列的方法（反射 + 行为双重断言）
- `series(name, n)` 的上界是**当前游标**，不是帧尾
- 热身期 `value()` 返回 None 而不是 NaN / 0
- `value(name, offset=-1)` 被拒（负偏移就是取未来）
- 非因果指标（`close.shift(-1)`）在声明时就被因果抽检拦下
- 躲过抽检的非因果指标被 `bias_detection` 兜住 ——
  一个从没报过警的检测器，和没有检测器是一回事
- 未覆盖 `declare_indicators` 的策略走原路径，上下文里连指标视图都不构造
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.errors import StrategyContractError
from app.data.models import Bar, Frequency, Market
from app.engine.backtest.bias_detection import detect_lookahead, run_bias_check
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import sma
from app.strategy.precompute import IndicatorView, view_from_history

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
N_BARS = 200
INITIAL_CASH = 100_000.0

#: `IndicatorView` 允许的公开接口。**新增一个方法就要在这里显式加一行** ——
#: 这份集合的作用不是描述现状，是逼着任何扩展都被审一次「它会不会交出未来」。
_ALLOWED_PUBLIC_API = {
    "value",       # 标量，游标处
    "series",      # 有界窗口，上界是游标
    "crossed_up",  # 布尔
    "crossed_down",  # 布尔
    "has",         # 布尔
    "names",       # 指标名，不含取值
    "bars_seen",   # 已见 bar 数，不含取值
}


# ── 数据 ──────────────────────────────────────────────────────


def make_bars(n: int = N_BARS, seed: int = 7) -> list[Bar]:
    """确定性的震荡行情：足够触发金叉死叉，且不依赖任何全局随机状态。"""
    rng = np.random.Generator(np.random.PCG64(seed))
    closes = 100.0 * np.exp(np.cumsum(0.0004 + 0.02 * rng.standard_normal(n)))
    return [
        Bar(
            time=BASE_TIME + timedelta(days=i),
            symbol="AAPL",
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=float(closes[i]),
            high=float(closes[i]) * 1.01,
            low=float(closes[i]) * 0.99,
            close=float(closes[i]),
            volume=1_000_000,
        )
        for i in range(n)
    ]


def run_backtest(strategy: StrategyBase, bars: list[Bar]):
    engine = BacktestEngine(BacktestConfig(initial_cash=INITIAL_CASH, market=Market.US))
    return engine.run(strategy, bars, strategy_id="precompute-test")


# ── 探针策略 ──────────────────────────────────────────────────


class _ProbeStrategy(StrategyBase):
    """不下单，只把每根 bar 上看到的指标视图记下来供断言。"""

    name = "probe"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.samples: list[dict] = []

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("fast", sma, 5)
        spec.add("slow", sma, 20)

    def on_bar(self, ctx: StrategyContext) -> None:
        ind = ctx.ind
        self.samples.append(
            {
                "bar_time": ctx.bar.time,
                "bars_seen": ind.bars_seen,
                "history_len": len(ctx.history),
                "fast": ind.value("fast"),
                "window": ind.series("fast", 5),
            }
        )


class _LegacyStrategy(StrategyBase):
    """不声明指标 —— 代表仓库外的用户策略，必须一行不改地继续跑。"""

    name = "legacy"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.saw_indicators: list[object] = []

    def on_bar(self, ctx: StrategyContext) -> None:
        self.saw_indicators.append(ctx.indicators)
        df = ctx.history
        if len(df) < 21:
            return
        if float(sma(df, 5).iloc[-1]) > float(sma(df, 20).iloc[-1]) and ctx.qty == 0:
            ctx.buy(10)
        elif float(sma(df, 5).iloc[-1]) < float(sma(df, 20).iloc[-1]) and ctx.qty > 0:
            ctx.sell_all()


# ── §2.1(1) 公开接口不得返回完整序列 ─────────────────────────


def test_indicator_view_public_api_is_frozen() -> None:
    """反射断言公开方法集：多出任何一个接口都要被这条用例逼着审一次。"""
    public = {name for name in dir(IndicatorView) if not name.startswith("_")}
    assert public == _ALLOWED_PUBLIC_API, (
        f"IndicatorView 的公开接口发生变化：多出 {sorted(public - _ALLOWED_PUBLIC_API)}，"
        f"少了 {sorted(_ALLOWED_PUBLIC_API - public)}。"
        f"新增接口前请确认它不可能交出当前游标之后的数据。"
    )


def test_no_public_method_can_return_an_unbounded_series() -> None:
    """
    行为断言：`series` 是唯一返回序列的方法，且它的 `n` **没有默认值**。

    一个 `series(name)` 的重载就等于把整条指标交出去 —— 光靠方法名白名单
    拦不住这种退化，所以这里直接盯签名。
    """
    signature = inspect.signature(IndicatorView.series)
    assert signature.parameters["n"].default is inspect.Parameter.empty, (
        "series(name, n) 的 n 不能有默认值：省略 n 就等于返回完整序列"
    )

    view = view_from_history(_ProbeStrategy().indicator_spec(), _frame(make_bars(50)))
    assert view is not None
    for name in _ALLOWED_PUBLIC_API - {"series"}:
        member = getattr(view, name)
        result = member if not callable(member) else _call_probe(member)
        assert not isinstance(result, (pd.Series, pd.DataFrame, np.ndarray)), (
            f"IndicatorView.{name} 返回了序列类型 {type(result).__name__}"
        )


def _call_probe(member):
    """用最宽松的参数调一次成员，只关心返回类型。"""
    try:
        return member("fast")
    except TypeError:
        return member("fast", "slow")


def _frame(bars: list[Bar]) -> pd.DataFrame:
    from app.engine.backtest.engine import _bars_to_df

    return _bars_to_df(bars)


# ── §2.1(2) series 的上界是当前游标，不是帧尾 ────────────────


def test_series_upper_bound_is_the_cursor_not_the_last_bar() -> None:
    bars = make_bars()
    strategy = _ProbeStrategy()
    run_backtest(strategy, bars)

    assert len(strategy.samples) == len(bars)
    last_bar_time = bars[-1].time

    for sample in strategy.samples:
        window = sample["window"]
        assert window.index[-1] == sample["bar_time"], (
            f"series() 的末位是 {window.index[-1]}，不是当前 bar {sample['bar_time']} "
            f"—— 回测中途这两者完全不同"
        )
        assert len(window) <= 5

    # 中途的窗口末位绝不能等于最后一根 bar
    midway = strategy.samples[len(strategy.samples) // 2]
    assert midway["window"].index[-1] != last_bar_time


def test_bars_seen_matches_history_length() -> None:
    """`bars_seen` 是热身期守卫的依据，必须与 `len(ctx.history)` 同源同值。"""
    strategy = _ProbeStrategy()
    run_backtest(strategy, make_bars())
    assert all(s["bars_seen"] == s["history_len"] for s in strategy.samples)


def test_series_snapshot_is_not_shared_with_the_store() -> None:
    """策略改了拿到的窗口，不能污染后续时点的取值。"""
    view = view_from_history(_ProbeStrategy().indicator_spec(), _frame(make_bars(60)))
    assert view is not None
    before = view.value("fast")
    window = view.series("fast", 5)
    window.iloc[:] = -999.0
    assert view.value("fast") == before


# ── §2.1(4) 热身期返回 None ──────────────────────────────────


def test_value_returns_none_during_warmup_not_nan_or_zero() -> None:
    strategy = _ProbeStrategy()
    run_backtest(strategy, make_bars())

    # slow=20 ⇒ 前 19 根 bar 上 fast(5) 已就绪，但前 4 根一定还没有
    warmup = strategy.samples[:4]
    assert all(s["fast"] is None for s in warmup), (
        f"热身期 value() 应返回 None，实际 {[s['fast'] for s in warmup]}"
    )
    # 不能是 NaN，也不能是 0
    assert not any(isinstance(s["fast"], float) for s in warmup)

    ready = strategy.samples[30]
    assert isinstance(ready["fast"], float)
    assert ready["fast"] == ready["fast"]  # 不是 NaN


def test_negative_offset_is_rejected() -> None:
    """负偏移就是取未来值 —— 直接抛错，不静默夹到 0。"""
    view = view_from_history(_ProbeStrategy().indicator_spec(), _frame(make_bars(60)))
    assert view is not None
    with pytest.raises(ValueError, match="offset"):
        view.value("fast", -1)
    with pytest.raises(ValueError, match="n"):
        view.series("fast", 0)


def test_unknown_indicator_name_fails_loudly() -> None:
    view = view_from_history(_ProbeStrategy().indicator_spec(), _frame(make_bars(60)))
    assert view is not None
    with pytest.raises(KeyError, match="未声明的指标"):
        view.value("nope")


# ── §2.1(1)+(3) 非因果指标：抽检 + bias_detection 两道防线 ────


def _peek_next_close(df: pd.DataFrame) -> pd.Series:
    """整段偷看下一根 close —— 因果抽检必须在声明时就拦住它。"""
    return df["close"].shift(-1)


class _AlwaysPeekingStrategy(StrategyBase):
    name = "always_peeking"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("peek", _peek_next_close)

    def on_bar(self, ctx: StrategyContext) -> None:  # pragma: no cover - 起跑即抛
        pass


def test_non_causal_indicator_is_rejected_at_declaration() -> None:
    with pytest.raises(StrategyContractError, match="不是因果指标"):
        run_backtest(_AlwaysPeekingStrategy(), make_bars())


def test_indicator_with_wrong_length_is_rejected() -> None:
    """长度对不上会让游标整体错位 —— 必须炸，不能静默按位置对齐。"""

    class _Truncating(StrategyBase):
        name = "truncating"

        def declare_indicators(self, spec: IndicatorSpec) -> None:
            spec.add("short", lambda df: df["close"].iloc[:-3])

        def on_bar(self, ctx: StrategyContext) -> None:  # pragma: no cover
            pass

    with pytest.raises(StrategyContractError, match="长度"):
        run_backtest(_Truncating(), make_bars())


# 偷看窗口用**绝对时间**界定，刻意落在因果抽检的两个游标（0.55 / 0.85）之外、
# 且跨过 bias_detection 的截断点（0.70）。这样它能躲过抽检，被兜底那道抓住。
_PEEK_START = BASE_TIME + timedelta(days=int(N_BARS * 0.62))
_PEEK_END = BASE_TIME + timedelta(days=int(N_BARS * 0.78))
#: 往前看几根。1 根会被 next-bar 撮合语义吃掉（截断点前一根仍能看到它的下一根），
#: 检测器观察不到差异 —— 这个常量必须 > 1 才是一次真实的泄漏。
_PEEK_AHEAD = 5


def _windowed_peek(df: pd.DataFrame) -> pd.Series:
    """
    只在固定时间窗内偷看 `_PEEK_AHEAD` 根之后的 close。

    刻意设计成能躲过因果抽检：抽检打在 0.55 / 0.85 两个游标上，都在窗外，
    用前缀算与用全帧算完全一致。它存在的意义是证明 `bias_detection` 这道
    兜底防线真的会响。
    """
    close = df["close"]
    mask = (df.index >= _PEEK_START) & (df.index < _PEEK_END)
    return close.where(~mask, close.shift(-_PEEK_AHEAD))


class _WindowedPeekStrategy(StrategyBase):
    """在偷看窗内是个完美先知：知道未来涨就满仓，知道要跌就清仓。"""

    name = "windowed_peek"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("oracle", _windowed_peek)

    def on_bar(self, ctx: StrategyContext) -> None:
        future = ctx.ind.value("oracle")
        if future is None:
            return
        if future > ctx.bar.close and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)
        elif future < ctx.bar.close and ctx.qty > 0:
            ctx.sell_all()


def test_windowed_peek_slips_past_the_causality_probe() -> None:
    """先证明它确实躲过了第一道防线，否则下一条用例证明不了兜底防线有用。"""
    run_backtest(_WindowedPeekStrategy(), make_bars())   # 不抛错即为「躲过」


def test_bias_detection_catches_what_the_probe_missed() -> None:
    """
    兜底防线必须真的会响。

    这里用 `detect_lookahead` 而不是 `run_bias_check`：后者还会跑递归检测
    （前端裁掉 startup bar 重跑），而前端裁剪会把偷看窗推到因果抽检的游标上，
    于是变体运行直接被第一道防线拦下、抛错而非返回成交 —— 那证明的是另一件事。
    """
    def run_fills(bars: list[Bar]) -> list[dict]:
        return run_backtest(_WindowedPeekStrategy(), bars).fills

    diff = detect_lookahead(run_fills, make_bars())
    assert diff.changed_signals > 0, (
        f"预算路径的泄漏没有被 bias_detection 抓到：{diff.detail}"
    )


def test_bias_detection_stays_quiet_on_a_precomputed_causal_strategy() -> None:
    """预算本身不得引入偏差 —— 一个只会喊狼来了的检测器同样没用。"""
    from app.strategy.presets import DoubleMaStrategy

    def run_fills(bars: list[Bar]) -> list[dict]:
        return run_backtest(DoubleMaStrategy(), bars).fills

    outcome = run_bias_check(run_fills, make_bars(400, seed=11), startup_candles=[20, 40])
    assert not outcome.has_lookahead_bias, outcome.lookahead.detail
    assert outcome.total_signals > 0, "没有成交的回测证明不了检测器安静得有道理"
    # 不断言 `has_recursive_bias`：起点敏感是 double_ma 改造**前后都有**的固有性质
    # （换起始日 ⇒ 热身期守卫与持仓路径都变），与预算无关。逐笔一致性由
    # tests/test_preset_parity.py 直接对拍。


# ── §3.1 旧写法一行不变 ──────────────────────────────────────


def test_strategy_without_declaration_never_gets_an_indicator_view() -> None:
    bars = make_bars()
    strategy = _LegacyStrategy()
    result = run_backtest(strategy, bars)

    assert strategy.indicator_spec() is None
    assert strategy.saw_indicators, "策略没被调用过，这条用例什么也没证明"
    assert all(seen is None for seen in strategy.saw_indicators), (
        "未声明指标的策略拿到了指标视图 —— 预算路径不该对它做任何事"
    )
    assert result.fills, "旧写法的策略应当照常成交"


def test_ctx_ind_fails_loudly_when_nothing_was_declared() -> None:
    ctx = StrategyContext(bar=make_bars(2)[0], history=_frame(make_bars(2)), broker=None)
    with pytest.raises(StrategyContractError, match="declare_indicators"):
        _ = ctx.ind


def test_live_path_computes_from_the_prefix_only() -> None:
    """
    实盘走 `view_from_history`：`history` 本身就是前缀，结构上不可能有未来。

    这条路径的存在理由是「改用 ctx.ind 的策略必须能在实盘照跑」——
    否则预算就成了一条只在回测里成立的分叉语义。
    """
    bars = make_bars()
    spec = _ProbeStrategy().indicator_spec()

    cursor = 120
    live_view = view_from_history(spec, _frame(bars[:cursor]))
    assert live_view is not None
    assert live_view.bars_seen == cursor
    assert live_view.series("fast", 3).index[-1] == bars[cursor - 1].time

    # 与回测预算路径在同一时点取到同一个值
    backtest_probe = _ProbeStrategy()
    run_backtest(backtest_probe, bars)
    assert backtest_probe.samples[cursor - 1]["fast"] == pytest.approx(
        live_view.value("fast"), rel=1e-12
    )


def test_live_path_is_a_noop_without_declaration() -> None:
    assert view_from_history(None, _frame(make_bars(30))) is None
    assert view_from_history(_ProbeStrategy().indicator_spec(), None) is None


# ── 每一条会驱动 preset 的路径都必须给得出 ctx.ind ────────────
#
# 契约只写了回测引擎，但仓库里有**四条**独立构造上下文并调 `on_bar` 的路径。
# 漏掉任何一条，改造过的 preset 在那条路径上就直接抛
# StrategyContractError —— 而 double_ma / macd / bollinger 是注册表里的默认策略。
# 下面每条用例钉住一条路径。


@pytest.mark.parametrize("preset_name", ["double_ma", "macd", "bollinger"])
def test_converted_presets_run_in_paper_simulation(preset_name: str) -> None:
    """路径 2：`app/strategy/paper_sim.py` 自己建上下文，实盘启动时必跑。"""
    from app.strategy.paper_sim import run_paper_simulation
    from app.strategy.presets import STRATEGY_REGISTRY

    portfolio = run_paper_simulation(
        strategy_cls=STRATEGY_REGISTRY[preset_name],
        params={},
        all_bars=make_bars(300, seed=5),
        sim_days=120,
    )
    assert portfolio.sim_start
    assert portfolio.sim_end


@pytest.mark.parametrize("preset_name", ["double_ma", "macd", "bollinger"])
def test_converted_presets_run_through_the_legacy_alpha_adapter(preset_name: str) -> None:
    """路径 3：`LegacyStrategyAlphaAdapter` 把 preset 包成 AlphaModel 后自建上下文。"""
    from app.engine.backtest.portfolio_engine import (
        PortfolioBacktestConfig,
        PortfolioBacktestEngine,
    )
    from app.engine.framework import (
        EqualWeightingPCM,
        FrameworkStrategy,
        ImmediateExecutionModel,
        LegacyStrategyAlphaAdapter,
    )
    from app.strategy.presets import STRATEGY_REGISTRY

    strategy = FrameworkStrategy(
        alpha=LegacyStrategyAlphaAdapter(STRATEGY_REGISTRY[preset_name]),
        portfolio_construction=EqualWeightingPCM(),
        execution=ImmediateExecutionModel(),
    )
    engine = PortfolioBacktestEngine(
        PortfolioBacktestConfig(initial_cash=INITIAL_CASH, market=Market.US)
    )
    result = engine.run(strategy, {"AAPL": make_bars(250, seed=9)})
    assert result.equity_curve is not None   # 没抛 StrategyContractError 即为通过


def test_live_portfolio_book_matches_the_backtest_store_at_the_same_cursor() -> None:
    """
    路径 4：`LivePortfolioRunner` 的 `LiveIndicatorBook`。

    实盘在前缀上现算、回测在全帧上预算后按游标裁剪 —— 同一时点必须是同一个数，
    否则 `declare_indicators` 就成了一条只在回测里成立的分叉语义。
    """
    from app.strategy.precompute import LiveIndicatorBook, build_indicator_store

    bars = make_bars(240, seed=13)
    spec = _ProbeStrategy().indicator_spec()
    assert spec is not None

    full_frame = _frame(bars)
    store = build_indicator_store(spec, {"AAPL": full_frame})

    for cursor in (25, 90, 180, len(bars)):
        backtest_view = IndicatorView(store["AAPL"].values, store["AAPL"].index, cursor)
        live_view = LiveIndicatorBook(spec, {"AAPL": full_frame.iloc[:cursor]}).view("AAPL")

        assert live_view.bars_seen == backtest_view.bars_seen == cursor
        for name in ("fast", "slow"):
            assert live_view.value(name) == pytest.approx(
                backtest_view.value(name), rel=1e-12, nan_ok=False
            ) or (live_view.value(name) is None and backtest_view.value(name) is None)
