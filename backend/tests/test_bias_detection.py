"""前视 / 递归偏差检测单元测试（C3）

覆盖：
- detect_lookahead 能识别人为注入未来数据（peek 下一根 close）的策略
- 干净策略（仅用当前及历史 bar）不误报前视偏差
- detect_recursive 能识别起点敏感（成交随可见历史长度漂移）的策略
- run_bias_check 汇总结论与 notes 文案
- 数据不足时递归检测被跳过
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.bias_detection import (
    detect_lookahead,
    detect_recursive,
    run_bias_check,
)


# ── 测试辅助 ─────────────────────────────────────────────────────

def _make_bars(n: int, base_price: float = 100.0) -> list[Bar]:
    """单调递增价格日线（保证每根都触发上涨信号，剔除随机性）。"""
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
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
        diff = detect_lookahead(lookahead_fills, bars, cut_ratio=0.7)

        # Assert: 截断未来数据后，截断点前的成交发生变化
        assert diff.changed_signals > 0
        assert diff.checked_signals > 0

    def test_clean_strategy_not_flagged(self):
        # Arrange
        bars = _make_bars(40)

        # Act
        diff = detect_lookahead(clean_fills, bars, cut_ratio=0.7)

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
        outcome = run_bias_check(clean_fills, bars, startup_candles=[5, 10])

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
        outcome = run_bias_check(lookahead_fills, bars, startup_candles=[5, 10])

        # Assert
        assert outcome.has_lookahead_bias is True
        assert outcome.lookahead.changed_signals > 0
        assert any("检测到前视偏差" in n for n in outcome.notes)

    def test_recursive_strategy_reports_recursive_bias(self):
        # Arrange
        bars = _make_bars(60)

        # Act
        outcome = run_bias_check(recursive_fills, bars, startup_candles=[5, 10])

        # Assert
        assert outcome.has_recursive_bias is True
        assert any("检测到递归偏差" in n for n in outcome.notes)

    def test_insufficient_data_skips_recursive(self):
        # Arrange: startup 全部越界 → recursive 为空
        bars = _make_bars(30)

        # Act
        outcome = run_bias_check(clean_fills, bars, startup_candles=[500])

        # Assert
        assert outcome.recursive == []
        assert outcome.has_recursive_bias is False
        assert any("跳过递归偏差检测" in n for n in outcome.notes)
