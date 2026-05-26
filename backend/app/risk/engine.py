"""
风控引擎

分两层：
1. pre_trade_check()  — 下单前同步调用，校验单笔订单是否合规
2. portfolio_check()  — 实时检查整体组合状态，检测日亏损/回撤/集中度

设计原则:
- 无副作用：只读取状态，返回违规列表，不直接阻止或平仓
- 调用方（OMS / 策略引擎）根据违规的 severity 决定行为

参考: refs/vnpy/vnpy/trader/engine.py RiskManager
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from app.risk.models import (
    RiskConfig,
    RiskRule,
    RiskViolation,
    RuleType,
    ViolationSeverity,
    default_risk_config,
)

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    风控引擎。

    使用方法:
        engine = RiskEngine(config)
        violations = engine.pre_trade_check(order_context)
        if any(v.severity == ViolationSeverity.BLOCK for v in violations):
            reject order
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self._config = config or default_risk_config()
        # 每日计数器（按日期重置）
        self._daily_order_counts: dict[str, int] = {}   # date_str → count
        self._daily_pnl: dict[str, float] = {}           # date_str → realized_pnl
        self._peak_portfolio_value: float = 0.0

    def update_config(self, new_config: RiskConfig) -> None:
        """热更新风控配置。"""
        self._config = new_config
        logger.info("Risk config updated: %s", new_config.name)

    @property
    def config(self) -> RiskConfig:
        return self._config

    # ── 前置风控 ──────────────────────────────────────────────

    def pre_trade_check(
        self,
        symbol: str,
        market: str,
        side: str,               # BUY / SELL
        qty: int,
        price: float,
        portfolio_value: float,
        current_symbol_value: float = 0.0,  # 当前该标的的市值
    ) -> list[RiskViolation]:
        """
        前置风控检查，在下单前调用。
        返回所有违规列表（可能为空）。
        """
        violations: list[RiskViolation] = []
        if not self._config.is_active:
            return violations

        order_value = qty * price

        # 1. 单笔订单金额上限
        rule = self._config.get_rule(RuleType.MAX_ORDER_VALUE)
        if rule and order_value > rule.value:
            violations.append(RiskViolation(
                rule_type=RuleType.MAX_ORDER_VALUE,
                severity=rule.severity,
                message=(
                    f"Order value {order_value:,.0f} exceeds limit {rule.value:,.0f}"
                ),
                value_actual=order_value,
                value_limit=rule.value,
            ))

        # 2. 单标的仓位占比（仅买入时检查）
        if side == "BUY" and portfolio_value > 0:
            rule = self._config.get_rule(RuleType.MAX_POSITION_PCT)
            if rule:
                new_symbol_value = current_symbol_value + order_value
                new_pct = new_symbol_value / portfolio_value
                if new_pct > rule.value:
                    violations.append(RiskViolation(
                        rule_type=RuleType.MAX_POSITION_PCT,
                        severity=rule.severity,
                        message=(
                            f"Position in {symbol} would reach "
                            f"{new_pct:.1%} of portfolio (limit: {rule.value:.1%})"
                        ),
                        value_actual=round(new_pct, 4),
                        value_limit=rule.value,
                    ))

        # 3. 每日下单频率
        rule = self._config.get_rule(RuleType.MAX_DAILY_ORDERS)
        if rule:
            today = date.today().isoformat()
            count = self._daily_order_counts.get(today, 0)
            if count >= rule.value:
                violations.append(RiskViolation(
                    rule_type=RuleType.MAX_DAILY_ORDERS,
                    severity=rule.severity,
                    message=(
                        f"Daily order count {count} reached limit {rule.value}"
                    ),
                    value_actual=count,
                    value_limit=rule.value,
                ))

        # 4. 市场白名单
        rule = self._config.get_rule(RuleType.ALLOWED_MARKETS)
        if rule and rule.value:
            allowed = [m.upper() for m in (rule.value if isinstance(rule.value, list) else [rule.value])]
            if market.upper() not in allowed:
                violations.append(RiskViolation(
                    rule_type=RuleType.ALLOWED_MARKETS,
                    severity=rule.severity,
                    message=f"Market '{market}' not in allowed list: {allowed}",
                    value_actual=market,
                    value_limit=rule.value,
                ))

        return violations

    def on_order_submitted(self) -> None:
        """每次成功提交订单后调用，更新日计数器。"""
        today = date.today().isoformat()
        self._daily_order_counts[today] = self._daily_order_counts.get(today, 0) + 1

    def on_fill(self, realized_pnl: float) -> None:
        """成交后更新当日已实现盈亏。"""
        today = date.today().isoformat()
        self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + realized_pnl

    # ── 实时组合检查 ──────────────────────────────────────────

    def portfolio_check(
        self,
        portfolio_value: float,
        initial_value: float,
        positions: list[dict],   # [{symbol, market_value, ...}]
    ) -> list[RiskViolation]:
        """
        实时组合风控检查。
        portfolio_value: 当前组合总净值
        initial_value:   账户初始净值（或开盘时净值）
        positions:       当前持仓列表（含 market_value 字段）
        """
        violations: list[RiskViolation] = []
        if not self._config.is_active or portfolio_value <= 0:
            return violations

        # 更新历史最高净值（用于计算最大回撤）
        if portfolio_value > self._peak_portfolio_value:
            self._peak_portfolio_value = portfolio_value

        # 1. 最大回撤
        rule = self._config.get_rule(RuleType.MAX_DRAWDOWN)
        if rule and self._peak_portfolio_value > 0:
            drawdown = (self._peak_portfolio_value - portfolio_value) / self._peak_portfolio_value
            if drawdown > rule.value:
                violations.append(RiskViolation(
                    rule_type=RuleType.MAX_DRAWDOWN,
                    severity=rule.severity,
                    message=(
                        f"Portfolio drawdown {drawdown:.1%} exceeds limit {rule.value:.1%}. "
                        "Consider reducing positions."
                    ),
                    value_actual=round(drawdown, 4),
                    value_limit=rule.value,
                ))

        # 2. 当日亏损限制
        rule = self._config.get_rule(RuleType.DAILY_LOSS_LIMIT)
        if rule and initial_value > 0:
            today = date.today().isoformat()
            daily_pnl = self._daily_pnl.get(today, 0.0)
            # 未实现 + 已实现亏损
            unrealized_loss = min(portfolio_value - initial_value, 0.0)
            total_daily_loss_pct = abs(min(daily_pnl + unrealized_loss, 0.0)) / initial_value
            if total_daily_loss_pct > rule.value:
                violations.append(RiskViolation(
                    rule_type=RuleType.DAILY_LOSS_LIMIT,
                    severity=rule.severity,
                    message=(
                        f"Daily loss {total_daily_loss_pct:.1%} exceeds limit {rule.value:.1%}"
                    ),
                    value_actual=round(total_daily_loss_pct, 4),
                    value_limit=rule.value,
                ))

        # 3. 单标的持仓集中度
        rule = self._config.get_rule(RuleType.POSITION_CONCENTRATION)
        if rule and positions:
            for pos in positions:
                mv = pos.get("market_value") or 0.0
                concentration = mv / portfolio_value
                if concentration > rule.value:
                    violations.append(RiskViolation(
                        rule_type=RuleType.POSITION_CONCENTRATION,
                        severity=rule.severity,
                        message=(
                            f"{pos.get('symbol', '?')} concentration {concentration:.1%} "
                            f"exceeds limit {rule.value:.1%}"
                        ),
                        value_actual=round(concentration, 4),
                        value_limit=rule.value,
                    ))

        return violations

    def daily_summary(self) -> dict:
        today = date.today().isoformat()
        return {
            "date": today,
            "orders_today": self._daily_order_counts.get(today, 0),
            "realized_pnl_today": round(self._daily_pnl.get(today, 0.0), 2),
            "peak_portfolio_value": round(self._peak_portfolio_value, 2),
        }


# 全局单例
_engine: RiskEngine | None = None


def get_risk_engine() -> RiskEngine:
    global _engine
    if _engine is None:
        _engine = RiskEngine()
    return _engine


def init_risk_engine(config: RiskConfig | None = None) -> RiskEngine:
    global _engine
    _engine = RiskEngine(config)
    return _engine
