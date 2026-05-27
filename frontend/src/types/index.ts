// ── 行情 ──────────────────────────────────────────────────────
export interface Bar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export type Market = "US" | "HK" | "A"
export type Frequency = "1m" | "5m" | "15m" | "1h" | "1d" | "1w"

// ── 回测 ──────────────────────────────────────────────────────
export interface BacktestRequest {
  strategy_name: string
  symbol: string
  market: Market
  frequency: Frequency
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
}

export interface BacktestMetrics {
  // 收益
  total_return_pct: number
  annual_return_pct: number
  volatility_pct: number
  trading_days: number
  // 风险调整
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  omega_ratio: number
  // 回撤
  max_drawdown_pct: number
  max_drawdown_duration: number
  // 交易统计
  total_trades: number
  win_rate_pct: number
  profit_factor: number
  expectancy: number
  avg_win: number
  avg_loss: number
  avg_trade_return: number
  sqn: number
  // 连胜连败
  max_consecutive_wins: number
  max_consecutive_losses: number
  // 基准
  buy_hold_return_pct: number
}

export interface EquityPoint { time: string; value: number }

export interface DrawdownPoint { time: string; value: number }
export interface PnlBin { range: string; count: number; positive: boolean }
export interface MonthlyReturns { [year: string]: { [month: string]: number } }

export interface BacktestResult {
  backtest_id: string
  strategy_name: string
  symbol: string
  market: string
  start_date: string
  end_date: string
  initial_cash: number
  final_value: number
  metrics: BacktestMetrics
  equity_curve: EquityPoint[]
  drawdown_series: DrawdownPoint[]
  monthly_returns: MonthlyReturns
  pnl_distribution: PnlBin[]
  fills: Fill[]
  generated_at: string
}

export interface OptimizeRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  param_grid: Record<string, number[]>
  optimize_target: string
  max_combinations: number
}

export interface OptimizeResultItem {
  params: Record<string, number>
  score: number
  total_return_pct: number
  annual_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  total_trades: number
}

export interface OptimizeResult {
  best_params: Record<string, number>
  best_score: number
  optimize_target: string
  total_combinations: number
  evaluated_combinations: number
  results: OptimizeResultItem[]
}

export interface MonteCarloResult {
  n_simulations: number
  original_return_pct: number
  original_sharpe: number
  original_max_drawdown_pct: number
  p5_return_pct: number
  p25_return_pct: number
  p50_return_pct: number
  p75_return_pct: number
  p95_return_pct: number
  p5_sharpe: number
  p95_sharpe: number
  p5_max_drawdown_pct: number
  p95_max_drawdown_pct: number
  prob_positive: number
  prob_beat_market: number
  envelope: { time: string; p5: number; p25: number; p50: number; p75: number; p95: number }[]
}

// ── 订单 ──────────────────────────────────────────────────────
export type OrderSide = "BUY" | "SELL"
export type OrderType = "MARKET" | "LIMIT"
export type OrderStatus =
  | "pending_submit" | "submitted" | "partial"
  | "filled" | "cancelled" | "rejected" | "expired"

export interface LiveOrder {
  order_id: string
  broker_order_id: string | null
  strategy_id: string | null
  symbol: string
  market: string
  side: OrderSide
  qty: number
  order_type: OrderType
  limit_price: number | null
  status: OrderStatus
  filled_qty: number
  avg_fill_price: number | null
  commission: number
  reject_reason: string | null
  created_at: string
  filled_at: string | null
}

export interface Fill {
  order_id: string
  symbol: string
  market: string
  side: OrderSide
  qty: number
  price: number
  commission: number
  filled_at: string
  realized_pnl: number
}

// ── 持仓 ──────────────────────────────────────────────────────
export interface Position {
  symbol: string
  market: string
  qty: number
  avg_cost: number
  current_price: number | null
  market_value: number | null
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
}

export interface Account {
  account_id: string
  currency: string
  cash: number
  buying_power: number
  portfolio_value: number
}

// ── 组合优化 ──────────────────────────────────────────────────
export type PortfolioOptMethod =
  | "max_sharpe" | "min_volatility" | "risk_parity" | "min_cvar" | "equal_weight"

export interface PortfolioOptRequest {
  symbols: string[]
  market: Market
  start_date: string
  end_date: string
  method: PortfolioOptMethod
  include_frontier: boolean
}

export interface PortfolioFrontierPoint {
  vol: number
  ret: number
  sharpe: number
}

export interface PortfolioOptResult {
  method: string
  weights: Record<string, number>
  expected_return: number
  expected_volatility: number
  sharpe_ratio: number
  cvar_95: number
  frontier: PortfolioFrontierPoint[]
  risk_contributions: Record<string, number>
}

// ── 策略 ──────────────────────────────────────────────────────
export interface Strategy {
  name: string
  description: string
}

// ── 风控 ──────────────────────────────────────────────────────
export interface RiskRule {
  rule_type: string
  value: number | string | string[]
  enabled: boolean
  severity: "warning" | "block" | "halt"
}

export interface RiskConfig {
  name: string
  rules: RiskRule[]
  is_active: boolean
}

export interface RiskViolation {
  rule_type: string
  severity: string
  message: string
  value_actual: number | null
  value_limit: number | null
  timestamp: string
}
