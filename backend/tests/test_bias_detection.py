"""前视 / 递归偏差检测单元测试（C3）

覆盖：
- detect_lookahead 能识别人为注入未来数据（peek 下一根 close）的策略
- 干净策略（仅用当前及历史 bar）不误报前视偏差
- detect_recursive 能识别起点敏感（成交随可见历史长度漂移）的策略
- run_bias_check 汇总结论与 notes 文案
- 数据不足时递归检测被跳过
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.bias_detection import (
    detect_lookahead,
    detect_recursive,
    run_bias_check,
)

# ── 测试辅助 ─────────────────────────────────────────────────────

def _make_bars(n: int, base_price: float = 100.0) -> list[Bar]:
    """单调递增价格日线（保证每根都触发上涨信号，剔除随机性）。"""
    start = datetime(2024, 1, 2, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(n):
        price = base_price + i  # 严格单调递增
        bars.append(
            Bar(
                time=start + timedelta(days=i),
                symbol="AAPL",
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=100_000,
            )
        )
    return bars


def _iso(bar: Bar) -> str:
    return bar.time.isoformat()


# ── 策略成交生成器（run_fills 代理） ─────────────────────────────

#: 本文件所有 run_fills 桩都是**同 bar 成交**（决策与成交落在同一根 bar），
#: 而真实引擎是 next-bar 撮合。扰动法的比对窗口取决于这个约定，
#: 所以每个调用都显式传 `decision_to_fill_bars=0` ——
#: 用默认的 1 会把这些干净桩误判成有前视偏差。


def clean_fills(bars: list[Bar]) -> list[dict]:
    """干净策略：仅依赖当前 bar 自身属性决策（无未来、无历史起点依赖）。

    决策与数量都只看当前 bar，故同一时间点的成交指纹不随「未来截断」或
    「前端裁剪」而变化 —— 前视/递归检测都应判定为干净。
    """
    fills: list[dict] = []
    for b in bars:
        if int(round(b.close)) % 2 == 0:  # 仅取决于当前 bar 自身
            fills.append(
                {"filled_at": _iso(b), "side": "BUY", "qty": 1, "price": b.close}
            )
    return fills


def lookahead_fills(bars: list[Bar]) -> list[dict]:
    """前视策略：偷看下一根 close 决定当前是否成交（注入未来）。"""
    fills: list[dict] = []
    for i in range(len(bars) - 1):
        if bars[i + 1].close > bars[i].close:  # 偷看未来
            fills.append(
                {"filled_at": _iso(bars[i]), "side": "BUY", "qty": 1, "price": bars[i].close}
            )
    return fills


def recursive_fills(bars: list[Bar]) -> list[dict]:
    """起点敏感策略：成交数量随可见历史长度（局部索引）漂移。"""
    fills: list[dict] = []
    for i in range(len(bars)):
        # qty 依赖于当前切片中已见 bar 数 → 裁剪前端会改变同一时间点的指纹
        fills.append(
            {"filled_at": _iso(bars[i]), "side": "BUY", "qty": i + 1, "price": bars[i].close}
        )
    return fills


class TestDetectLookahead:
    """detect_lookahead 前视偏差。"""

    def test_lookahead_strategy_is_flagged(self):
        # Arrange
        bars = _make_bars(40)

        # Act
        diff = detect_lookahead(lookahead_fills, bars, cut_ratio=0.7, decision_to_fill_bars=0)

        # Assert: 截断未来数据后，截断点前的成交发生变化
        assert diff.changed_signals > 0
        assert diff.checked_signals > 0

    def test_clean_strategy_not_flagged(self):
        # Arrange
        bars = _make_bars(40)

        # Act
        diff = detect_lookahead(clean_fills, bars, cut_ratio=0.7, decision_to_fill_bars=0)

        # Assert: 干净策略截断前成交完全一致
        assert diff.changed_signals == 0
        assert diff.checked_signals > 0


class TestDetectRecursive:
    """detect_recursive 起点敏感/递归偏差。"""

    def test_start_point_sensitive_strategy_is_flagged(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        diffs = detect_recursive(recursive_fills, bars, startup_candles=[5, 10])

        # Assert: 每个裁剪长度都产生尾部成交漂移
        assert len(diffs) == 2
        assert all(d.changed_signals > 0 for d in diffs)
        assert {d.startup_candle for d in diffs} == {5, 10}

    def test_clean_strategy_not_flagged(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        diffs = detect_recursive(clean_fills, bars, startup_candles=[5, 10])

        # Assert: 重叠尾部成交在不同起点下保持一致
        assert len(diffs) == 2
        assert all(d.changed_signals == 0 for d in diffs)

    def test_out_of_range_startup_candles_yield_no_diffs(self):
        # Arrange: 裁剪量过大（>= n - 尾部保留），无有效检测点
        bars = _make_bars(20)

        # Act
        diffs = detect_recursive(clean_fills, bars, startup_candles=[500])

        # Assert
        assert diffs == []


class TestRunBiasCheck:
    """run_bias_check 汇总结论。"""

    def test_clean_strategy_reports_no_bias(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        outcome = run_bias_check(clean_fills, bars, startup_candles=[5, 10], decision_to_fill_bars=0)

        # Assert
        assert outcome.has_lookahead_bias is False
        assert outcome.has_recursive_bias is False
        assert outcome.total_signals > 0
        assert any("未发现前视偏差" in n for n in outcome.notes)
        assert any("未发现递归偏差" in n for n in outcome.notes)

    def test_lookahead_strategy_reports_lookahead_bias(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        outcome = run_bias_check(lookahead_fills, bars, startup_candles=[5, 10], decision_to_fill_bars=0)

        # Assert
        assert outcome.has_lookahead_bias is True
        assert outcome.lookahead.changed_signals > 0
        assert any("检测到前视偏差" in n for n in outcome.notes)

    def test_recursive_strategy_reports_recursive_bias(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        outcome = run_bias_check(recursive_fills, bars, startup_candles=[5, 10], decision_to_fill_bars=0)

        # Assert
        assert outcome.has_recursive_bias is True
        assert any("检测到递归偏差" in n for n in outcome.notes)

    def test_insufficient_data_skips_recursive(self):
        # Arrange: startup 全部越界 → recursive 为空
        bars = _make_bars(30)

        # Act
        outcome = run_bias_check(clean_fills, bars, startup_candles=[500], decision_to_fill_bars=0)

        # Assert
        assert outcome.recursive == []
        assert outcome.has_recursive_bias is False
        assert any("跳过递归偏差检测" in n for n in outcome.notes)


# ── 盲区回归：截断法单独用不够 ─────────────────────────────────


def _peek_fills_factory(horizon: int, delay: int):
    """构造一个偷看 `horizon` 根、隔 `delay` 根成交的策略桩。

    `delay=1` 复刻真实引擎的 next-bar 撮合 —— 这正是盲区所在。
    """

    def run_fills(bars: list[Bar]) -> list[dict]:
        fills: list[dict] = []
        for i in range(len(bars) - horizon - delay):
            if bars[i + horizon].close > bars[i].close:      # ← 偷看未来
                exec_bar = bars[i + delay]
                fills.append(
                    {
                        "filled_at": _iso(exec_bar),
                        "side": "BUY",
                        "qty": 1,
                        "price": exec_bar.close,
                    }
                )
        return fills

    return run_fills


@pytest.mark.parametrize("horizon", [1, 2, 3, 5])
def test_perturbation_catches_peeking_at_every_horizon(horizon: int) -> None:
    """偷看 1/2/3/5 根都必须被检出。

    **这条是盯着一个实测出来的盲区写的**：只用截断法时，
    偷看 1 根的策略在 next-bar 撮合下，那笔发散的成交恰好落在截断时刻，
    被「严格早于」的比对窗口排除 —— 实测 H=1 漏报、H=2 仅 1/97 个信号变化、
    H=3/5 均漏报。灵敏度约等于 H/N。

    补上的扰动法（未来价格变成别的值 + 只比对决策不比对价格）覆盖了这一片。
    如果有人把扰动那段删掉「简化」实现，这条会红。
    """
    bars = _make_bars(120)
    run_fills = _peek_fills_factory(horizon, delay=1)

    diff = detect_lookahead(run_fills, bars, cut_ratio=0.7, decision_to_fill_bars=1)

    assert diff.changed_signals > 0, (
        f"偷看 {horizon} 根未被检出 —— 盲区回来了：{diff.detail}"
    )


def test_clean_next_bar_strategy_is_not_flagged() -> None:
    """反面：next-bar 撮合的干净策略不能因为扰动而误报。

    扰动会改变未来 bar 的成交**价**，但不该改变任何**决策**。
    比对指纹若含价格，这条就会红 —— 那正是它要防的。
    """
    bars = _make_bars(120)

    def clean_next_bar(bs: list[Bar]) -> list[dict]:
        fills: list[dict] = []
        for i in range(len(bs) - 1):
            if int(round(bs[i].close)) % 2 == 0:      # 只看当前 bar
                fills.append(
                    {
                        "filled_at": _iso(bs[i + 1]),  # next-bar 成交
                        "side": "BUY",
                        "qty": 1,
                        "price": bs[i + 1].close,
                    }
                )
        return fills

    diff = detect_lookahead(clean_next_bar, bars, cut_ratio=0.7, decision_to_fill_bars=1)

    assert diff.changed_signals == 0, f"干净策略被误报：{diff.detail}"
