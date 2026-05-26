"""风控引擎单元测试"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.risk.engine import RiskEngine
from app.risk.models import (
    RiskConfig,
    RiskRule,
    RuleType,
    ViolationSeverity,
    default_risk_config,
)


def _make_engine(**rule_overrides) -> RiskEngine:
    """创建带自定义规则的引擎（只设置测试关心的规则）。"""
    rules = []
    rule_map = {
        "max_position_pct": (RuleType.MAX_POSITION_PCT, 0.20),
        "max_order_value": (RuleType.MAX_ORDER_VALUE, 100_000),
        "max_daily_orders": (RuleType.MAX_DAILY_ORDERS, 5),
        "daily_loss_limit": (RuleType.DAILY_LOSS_LIMIT, 0.05),
        "max_drawdown": (RuleType.MAX_DRAWDOWN, 0.15),
        "position_concentration": (RuleType.POSITION_CONCENTRATION, 0.30),
    }
    rule_map.update(rule_overrides)
    for name, (rule_type, value) in rule_map.items():
        if value is not None:
            rules.append(RiskRule(rule_type=rule_type, value=value))
    return RiskEngine(RiskConfig(rules=rules))


class TestPreTradeCheck:
    def test_passes_within_limits(self) -> None:
        engine = _make_engine()
        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="BUY",
            qty=10, price=100.0,
            portfolio_value=100_000, current_symbol_value=0,
        )
        assert len(violations) == 0

    def test_blocks_oversized_order(self) -> None:
        engine = _make_engine()
        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="BUY",
            qty=2000, price=100.0,      # 200,000 > limit 100,000
            portfolio_value=500_000, current_symbol_value=0,
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.MAX_ORDER_VALUE in rule_types

    def test_blocks_excess_position_pct(self) -> None:
        engine = _make_engine()
        # 现有仓位已占 15%，再买 10% → 超过 20% 限制
        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="BUY",
            qty=100, price=100.0,       # order_value = 10,000
            portfolio_value=100_000,
            current_symbol_value=15_000,  # 已有 15%
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.MAX_POSITION_PCT in rule_types

    def test_sell_skips_position_pct_check(self) -> None:
        engine = _make_engine()
        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="SELL",
            qty=1000, price=100.0,      # 卖出不受仓位占比限制
            portfolio_value=100_000, current_symbol_value=80_000,
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.MAX_POSITION_PCT not in rule_types

    def test_blocks_when_daily_order_limit_reached(self) -> None:
        engine = _make_engine()
        today = date.today().isoformat()
        engine._daily_order_counts[today] = 5  # 已达上限

        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="BUY",
            qty=1, price=100.0,
            portfolio_value=100_000, current_symbol_value=0,
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.MAX_DAILY_ORDERS in rule_types

    def test_market_whitelist_blocks_disallowed_market(self) -> None:
        rules = [RiskRule(RuleType.ALLOWED_MARKETS, ["US"])]
        engine = RiskEngine(RiskConfig(rules=rules))
        violations = engine.pre_trade_check(
            symbol="00700", market="HK", side="BUY",
            qty=100, price=300.0,
            portfolio_value=100_000, current_symbol_value=0,
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.ALLOWED_MARKETS in rule_types

    def test_market_whitelist_passes_allowed_market(self) -> None:
        rules = [RiskRule(RuleType.ALLOWED_MARKETS, ["US", "HK"])]
        engine = RiskEngine(RiskConfig(rules=rules))
        violations = engine.pre_trade_check(
            symbol="AAPL", market="US", side="BUY",
            qty=10, price=100.0,
            portfolio_value=100_000, current_symbol_value=0,
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.ALLOWED_MARKETS not in rule_types


class TestOnOrderSubmitted:
    def test_increments_daily_count(self) -> None:
        engine = _make_engine()
        today = date.today().isoformat()
        engine.on_order_submitted()
        engine.on_order_submitted()
        assert engine._daily_order_counts.get(today, 0) == 2


class TestOnFill:
    def test_accumulates_daily_pnl(self) -> None:
        engine = _make_engine()
        today = date.today().isoformat()
        engine.on_fill(500.0)
        engine.on_fill(-200.0)
        assert abs(engine._daily_pnl.get(today, 0.0) - 300.0) < 1e-9


class TestPortfolioCheck:
    def test_no_violations_within_limits(self) -> None:
        engine = _make_engine()
        engine._peak_portfolio_value = 100_000
        violations = engine.portfolio_check(
            portfolio_value=95_000,
            initial_value=100_000,
            positions=[{"symbol": "AAPL", "market_value": 20_000}],
        )
        assert len(violations) == 0

    def test_max_drawdown_violation(self) -> None:
        engine = _make_engine()
        engine._peak_portfolio_value = 100_000
        violations = engine.portfolio_check(
            portfolio_value=80_000,   # -20% 回撤，超过 15% 限制
            initial_value=100_000,
            positions=[],
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.MAX_DRAWDOWN in rule_types

    def test_concentration_violation(self) -> None:
        engine = _make_engine()
        engine._peak_portfolio_value = 100_000
        violations = engine.portfolio_check(
            portfolio_value=100_000,
            initial_value=100_000,
            positions=[
                {"symbol": "AAPL", "market_value": 40_000},  # 40% > 30% 限制
            ],
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.POSITION_CONCENTRATION in rule_types

    def test_daily_loss_violation(self) -> None:
        engine = _make_engine()
        engine._peak_portfolio_value = 100_000
        violations = engine.portfolio_check(
            portfolio_value=90_000,   # 当日亏损 10% > 5% 限制
            initial_value=100_000,
            positions=[],
        )
        rule_types = [v.rule_type for v in violations]
        assert RuleType.DAILY_LOSS_LIMIT in rule_types

    def test_updates_peak_value(self) -> None:
        engine = _make_engine()
        engine.portfolio_check(portfolio_value=110_000, initial_value=100_000, positions=[])
        assert engine._peak_portfolio_value == 110_000

    def test_skipped_when_inactive(self) -> None:
        config = RiskConfig(rules=[], is_active=False)
        engine = RiskEngine(config)
        violations = engine.portfolio_check(
            portfolio_value=50_000,
            initial_value=100_000,
            positions=[],
        )
        assert len(violations) == 0


class TestDefaultConfig:
    def test_default_config_has_rules(self) -> None:
        config = default_risk_config()
        assert len(config.rules) > 0

    def test_default_config_has_expected_rules(self) -> None:
        config = default_risk_config()
        rule_types = {r.rule_type for r in config.rules}
        assert RuleType.MAX_POSITION_PCT in rule_types
        assert RuleType.DAILY_LOSS_LIMIT in rule_types
        assert RuleType.MAX_DRAWDOWN in rule_types

    def test_hot_update_config(self) -> None:
        engine = RiskEngine()
        new_config = RiskConfig(rules=[RiskRule(RuleType.MAX_ORDER_VALUE, 1_000_000)])
        engine.update_config(new_config)
        assert engine.config.rules[0].value == 1_000_000
