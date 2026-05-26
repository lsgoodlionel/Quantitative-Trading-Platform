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
  total_return_pct: number
  annual_return_pct: number
  sharpe_ratio: number
  sortino_ratio: number
  max_drawdown_pct: number
  calmar_ratio: number
  win_rate_pct: number
  profit_factor: number
  total_trades: number
  volatility_pct: number
  trading_days: number
}

export interface EquityPoint { time: string; value: number }

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
  fills: Fill[]
  generated_at: string
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
