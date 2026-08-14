"""启动时的纸面交易模拟（`/live-strategies` 端点的即时结果）

⚠️ **已知技术债（Wave L-d 契约 §七，本期不做）**：本模块与回测引擎**没有任何
共享** —— 没有滑点/佣金模型、没有订单类型、没有 T+1 与涨跌停，因此它给出的
模拟结果与真实回测**不可比**。收编进 `PortfolioBacktestEngine` 是正确方向，
但会改变现有端点返回的模拟数值，属于用户可见的行为变更，应单独立项并配迁移
说明。实盘路径（`StrategyEngine.start_portfolio_strategy`）已经不经过这里。

从 `engine.py` 原样搬出，只为让那个文件回到可读的体量；逻辑一行未改。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.data.models import Bar
from app.engine.backtest.engine import _bars_to_df
from app.strategy.context import StrategyContext
from app.strategy.precompute import indicator_spec_of, view_from_history

logger = logging.getLogger(__name__)

PAPER_SIM_DAYS = 60          # 模拟最近 N 天历史数据
PAPER_INITIAL_CASH = 100_000.0


# ── 纸面交易数据结构 ───────────────────────────────────────────

@dataclass
class PaperTrade:
    timestamp: str
    side: str          # BUY / SELL
    price: float
    qty: int
    value: float       # price * qty
    realized_pnl: float = 0.0
    signal_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "side": self.side,
            "price": self.price,
            "qty": self.qty,
            "value": self.value,
            "realized_pnl": self.realized_pnl,
            "signal_reason": self.signal_reason,
        }


@dataclass
class PaperPortfolio:
    """纸面交易组合状态。"""
    initial_cash: float = PAPER_INITIAL_CASH
    cash: float = PAPER_INITIAL_CASH
    position: int = 0
    avg_cost: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)   # [{time, value, pnl_pct}]
    trades: list[PaperTrade] = field(default_factory=list)

    # 汇总指标（模拟完成后计算）
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    buy_hold_return_pct: float = 0.0
    sim_start: str = ""
    sim_end: str = ""
    sim_days: int = PAPER_SIM_DAYS   # 本次模拟使用的天数

    def current_equity(self, price: float) -> float:
        return self.cash + self.position * price

    def buy(self, price: float, qty: int, timestamp: str) -> None:
        cost = price * qty
        if cost > self.cash:
            qty = int(self.cash / price)
            cost = price * qty
        if qty <= 0:
            return
        # 更新平均成本
        total_qty = self.position + qty
        self.avg_cost = (self.avg_cost * self.position + price * qty) / total_qty if total_qty else price
        self.cash -= cost
        self.position += qty
        self.trades.append(PaperTrade(
            timestamp=timestamp, side="BUY", price=price, qty=qty,
            value=round(cost, 2), signal_reason="策略买入信号",
        ))

    def sell(self, price: float, qty: int, timestamp: str) -> None:
        qty = min(qty, self.position)
        if qty <= 0:
            return
        realized_pnl = (price - self.avg_cost) * qty
        self.cash += price * qty
        self.position -= qty
        if self.position == 0:
            self.avg_cost = 0.0
        self.trades.append(PaperTrade(
            timestamp=timestamp, side="SELL", price=price, qty=qty,
            value=round(price * qty, 2), realized_pnl=round(realized_pnl, 2),
            signal_reason="策略卖出信号",
        ))

    def sell_all(self, price: float, timestamp: str) -> None:
        self.sell(price, self.position, timestamp)

    def snapshot(self, timestamp: str, price: float) -> None:
        equity = self.current_equity(price)
        pnl_pct = (equity - self.initial_cash) / self.initial_cash * 100
        self.equity_curve.append({
            "time": timestamp,
            "value": round(equity, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    def compute_metrics(self, initial_price: float, final_price: float) -> None:
        """计算汇总绩效指标。"""
        curve = self.equity_curve
        if not curve:
            return

        values = [p["value"] for p in curve]
        final_equity = values[-1]
        self.total_return_pct = round((final_equity - self.initial_cash) / self.initial_cash * 100, 2)
        self.buy_hold_return_pct = round((final_price - initial_price) / initial_price * 100, 2)
        self.total_trades = len(self.trades)

        # Sharpe（简化日收益率）
        if len(values) > 1:
            rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
            mean_r = sum(rets) / len(rets)
            std_r = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
            self.sharpe_ratio = round((mean_r / std_r * (252 ** 0.5)) if std_r > 1e-10 else 0.0, 3)

        # 最大回撤
        peak = self.initial_cash
        max_dd = 0.0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
        self.max_drawdown_pct = round(-max_dd * 100, 2)

        # 胜率 & 盈亏比
        sell_trades = [t for t in self.trades if t.side == "SELL"]
        if sell_trades:
            wins = [t for t in sell_trades if t.realized_pnl > 0]
            losses = [t for t in sell_trades if t.realized_pnl <= 0]
            self.win_rate_pct = round(len(wins) / len(sell_trades) * 100, 1)
            total_win = sum(t.realized_pnl for t in wins)
            total_loss = abs(sum(t.realized_pnl for t in losses))
            self.profit_factor = round(total_win / total_loss, 2) if total_loss > 1e-6 else (
                99.0 if total_win > 0 else 0.0
            )

    def to_dict(self) -> dict:
        return {
            "initial_cash": self.initial_cash,
            "cash": round(self.cash, 2),
            "position": self.position,
            "avg_cost": round(self.avg_cost, 2),
            "equity_curve": self.equity_curve,
            "trades": [t.to_dict() for t in self.trades],
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "buy_hold_return_pct": self.buy_hold_return_pct,
            "sim_start": self.sim_start,
            "sim_end": self.sim_end,
            "sim_days": self.sim_days,
        }


# ── 纸面交易代理 Broker（供 StrategyContext 使用） ─────────────────

class _PaperPosition:
    """SimulatedBroker.positions.get() 返回的 Position 兼容对象。"""

    def __init__(self, qty: int, avg_cost: float) -> None:
        self.qty = qty
        self.avg_cost = avg_cost
        self.market_value = 0.0  # 兼容字段


class _PaperPositions:
    """StrategyContext 要求 broker.positions.get(sym) → Position-like."""

    def __init__(self, portfolio: PaperPortfolio) -> None:
        self._p = portfolio

    def get(self, symbol: str) -> _PaperPosition | None:
        if self._p.position <= 0:
            return None
        return _PaperPosition(qty=self._p.position, avg_cost=self._p.avg_cost)


class PaperBroker:
    """
    SimulatedBroker 接口兼容的纸面 Broker。

    ⚠️ 已知技术债（Wave L-d 契约 §七，本期不做）见**模块 docstring**：
    这套撮合与回测引擎无共享，模拟结果与真实回测不可比。

    StrategyContext 期望：
      - broker.cash          → float
      - broker.positions.get(sym) → Position-like with .qty
      - broker.portfolio_value(prices) → float
      - broker.buy(symbol, qty, market=None)
      - broker.sell(symbol, qty, market=None)
    """

    def __init__(self, portfolio: PaperPortfolio) -> None:
        self._p = portfolio
        self._current_bar: Bar | None = None
        self.positions = _PaperPositions(portfolio)

    def set_bar(self, bar: Bar) -> None:
        self._current_bar = bar

    @property
    def cash(self) -> float:
        return self._p.cash

    def portfolio_value(self, prices: dict) -> float:
        if self._current_bar:
            price = prices.get(self._current_bar.symbol, self._current_bar.close)
        else:
            price = 0.0
        return self._p.cash + self._p.position * price

    def _ts(self) -> str:
        if self._current_bar:
            t = self._current_bar.time
            return t.isoformat() if hasattr(t, "isoformat") else str(t)
        return ""

    def buy(self, symbol: str, qty: int, market=None) -> None:  # noqa: ARG002
        if self._current_bar and qty > 0:
            self._p.buy(self._current_bar.close, qty, self._ts())

    def sell(self, symbol: str, qty: int, market=None) -> None:  # noqa: ARG002
        if self._current_bar and qty > 0:
            self._p.sell(self._current_bar.close, qty, self._ts())


# ── 纸面交易模拟函数 ───────────────────────────────────────────

def run_paper_simulation(
    strategy_cls,
    params: dict,
    all_bars: list[Bar],
    sim_days: int = PAPER_SIM_DAYS,
) -> PaperPortfolio:
    """
    在最近 sim_days 天的历史数据上运行策略模拟。

    前面的数据用作指标预热，最后 sim_days 天计入 PnL 和净值曲线。
    返回填充好的 PaperPortfolio。
    """
    if not all_bars:
        return PaperPortfolio()

    portfolio = PaperPortfolio()
    broker = PaperBroker(portfolio)

    strategy_obj = strategy_cls(params=params)

    # 确定回测窗口：最后 sim_days 天
    last_bar_time = all_bars[-1].time
    sim_cutoff = last_bar_time.date() - timedelta(days=sim_days) if hasattr(last_bar_time, "date") else date.today() - timedelta(days=sim_days)
    sim_bars = [b for b in all_bars if (b.time.date() if hasattr(b.time, "date") else b.time) >= sim_cutoff]

    if not sim_bars:
        sim_bars = all_bars[-min(30, len(all_bars)):]

    portfolio.sim_start = str(sim_bars[0].time)[:10]
    portfolio.sim_end   = str(sim_bars[-1].time)[:10]
    portfolio.sim_days  = sim_days

    # 初始化策略（用全部历史做 on_start）
    full_df = _bars_to_df(all_bars)
    # ★ 这里的 full_df 含**模拟窗口之后**的 bar，相对逐 bar 循环是未来 ——
    #   所以刻意不给 on_start 挂指标视图。策略在 on_start 里碰 ctx.ind 会拿到
    #   一条明确的报错，而不是一份偷看了未来的指标。
    init_ctx = StrategyContext(bar=all_bars[-1], history=full_df, broker=None)
    try:
        strategy_obj.on_start(init_ctx)
    except Exception:
        pass

    # E-a：声明解析一次；`history_df` 是逐 bar 增长的前缀，无前视风险
    spec = indicator_spec_of(strategy_obj)

    initial_price = sim_bars[0].close

    # 逐 bar 运行策略
    list(all_bars)
    sim_start_idx = len(all_bars) - len(sim_bars)

    for i, bar in enumerate(sim_bars):
        global_idx = sim_start_idx + i
        history_slice = all_bars[:global_idx + 1]
        history_df = _bars_to_df(history_slice)

        broker.set_bar(bar)
        ctx = StrategyContext(
            bar=bar,
            history=history_df,
            broker=broker,    # 使用纸面 broker
            indicators=view_from_history(spec, history_df),
        )
        try:
            strategy_obj.on_bar(ctx)
        except Exception as e:
            logger.debug("Paper sim on_bar error: %s", e)

        ts = str(bar.time)[:10]
        portfolio.snapshot(ts, bar.close)

    # 期末平仓（记录未实现盈亏）
    if portfolio.position > 0 and sim_bars:
        final_price = sim_bars[-1].close
        ts = str(sim_bars[-1].time)[:10]
        portfolio.sell_all(final_price, ts)

    portfolio.compute_metrics(initial_price, sim_bars[-1].close if sim_bars else initial_price)
    return portfolio

