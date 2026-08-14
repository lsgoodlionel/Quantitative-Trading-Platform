// 回测夹具。形状来源见 fixtures/README.md。
//
//   PRESET_STRATEGIES     → backend/.../strategies.py:20（端点直接返回该字面量）
//   BacktestResponse      → backend/.../backtests.py:83
//   BacktestMetricsResponse → backend/.../backtests.py:53
//   equity_curve / drawdown_series → backend/app/engine/backtest/report.py:101 / :120
//                                     （均为 [{time, value}]，value 已 round）
//   monthly_returns  → backend/app/engine/backtest/metrics.py:185（{year: {"MM": pct}}）
//   pnl_distribution → backend/app/engine/backtest/report.py:140（[{range, count, positive}]）
//   fills            → backend/app/engine/backtest/engine.py:262（_fill_to_dict）

import { makeBars } from "./market"

/** GET /api/v1/strategies/presets —— 取后端 PRESET_STRATEGIES 的前四条。 */
export const STRATEGY_PRESETS = [
  { name: "double_ma", description: "双均线趋势 — 短期均线穿越长期均线触发买卖信号", category: "trend" },
  { name: "macd", description: "MACD 动量 — 利用 MACD 柱与信号线交叉捕捉趋势", category: "trend" },
  { name: "bollinger", description: "布林带均值回归 — 价格触碰通道边界时反向交易", category: "mean_reversion" },
  { name: "momentum", description: "价格动量 — 基于过去 N 日收益率的趋势延续效应", category: "momentum" },
]

const INITIAL_CASH = 100_000
const FINAL_VALUE = 134_820.55

const equityCurve = makeBars().map((bar, i) => ({
  time: bar.time,
  value: Number((INITIAL_CASH * (1 + (i / 39) * 0.348)).toFixed(2)),
}))

const drawdownSeries = makeBars().map((bar, i) => ({
  time: bar.time,
  // 后端存的是百分比（report.py 里 dd_series * 100），负值
  value: Number((-Math.abs(Math.sin(i / 6)) * 8.4).toFixed(4)),
}))

/**
 * 指标全部给「好看」的数：
 * `BacktestResultPanel` 用 sharpe ≥ 0.5 且 |回撤| < 30 且收益 > 0 决定 CTA 文案，
 * 走达标分支能让断言只对一种文案，不用在测试里复刻那套阈值逻辑。
 */
const METRICS = {
  // 收益
  total_return_pct: 34.8205,
  annual_return_pct: 16.2411,
  volatility_pct: 18.9032,
  trading_days: 504,
  // 风险调整
  sharpe_ratio: 1.284,
  sortino_ratio: 1.871,
  calmar_ratio: 1.932,
  omega_ratio: 1.412,
  // 回撤
  max_drawdown_pct: -8.4062,
  max_drawdown_duration: 37,
  // 交易统计
  total_trades: 42,
  win_rate_pct: 57.1429,
  profit_factor: 1.836,
  expectancy: 82.91,
  avg_win: 412.55,
  avg_loss: -238.17,
  avg_trade_return: 0.83,
  sqn: 2.31,
  // 连胜连败
  max_consecutive_wins: 6,
  max_consecutive_losses: 3,
  // 基准
  buy_hold_return_pct: 21.4,
}

/** POST /api/v1/backtests/run */
export const BACKTEST_RESULT = {
  backtest_id: "e2e-backtest-0001",
  strategy_name: "double_ma",
  symbol: "AAPL",
  market: "US",
  start_date: "2023-06-02",
  end_date: "2025-06-02",
  initial_cash: INITIAL_CASH,
  final_value: FINAL_VALUE,
  metrics: METRICS,
  equity_curve: equityCurve,
  drawdown_series: drawdownSeries,
  monthly_returns: {
    "2024": { "01": 2.41, "02": -1.12, "03": 3.87, "04": 0.94 },
    "2025": { "01": 1.63, "02": 2.05 },
  },
  pnl_distribution: [
    { range: "-500~-250", count: 6, positive: false },
    { range: "-250~0", count: 12, positive: false },
    { range: "0~250", count: 15, positive: true },
    { range: "250~500", count: 9, positive: true },
  ],
  fills: [
    {
      order_id: "e2e-fill-1", symbol: "AAPL", market: "US", side: "BUY",
      qty: 100, price: 181.24, commission: 1.0,
      filled_at: "2025-01-08T00:00:00+00:00", realized_pnl: 0.0,
      entry_tag: null, exit_reason: null, direction: "long", is_close: false,
    },
    {
      order_id: "e2e-fill-2", symbol: "AAPL", market: "US", side: "SELL",
      qty: 100, price: 189.5, commission: 1.0,
      filled_at: "2025-02-11T00:00:00+00:00", realized_pnl: 824.6,
      entry_tag: null, exit_reason: null, direction: "long", is_close: true,
    },
  ],
  generated_at: "2025-06-02T20:10:00",
}

/** 回测结果里几个会渲染成文本的数，供 spec 断言时复用，避免两处各写一遍。 */
export const BACKTEST_DISPLAY = {
  sharpe: METRICS.sharpe_ratio.toFixed(3),
  maxDrawdown: `${METRICS.max_drawdown_pct.toFixed(2)}%`,
  winRate: `${METRICS.win_rate_pct.toFixed(1)}%`,
}
