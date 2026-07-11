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

// ── 组合优化：风险模型 & 预期收益估计 (D1) ──
export type RiskModel =
  | "sample_cov" | "ledoit_wolf" | "exp_cov" | "semicovariance"

export type ExpectedReturnsMethod =
  | "mean_historical" | "ema_historical" | "capm"

export interface PortfolioOptRequest {
  symbols: string[]
  market: Market
  start_date: string
  end_date: string
  method: PortfolioOptMethod
  include_frontier: boolean
  risk_model?: RiskModel                            // 新增，默认 "sample_cov"
  expected_returns_method?: ExpectedReturnsMethod   // 新增，默认 "mean_historical"
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
  risk_model?: string                 // 新增回显
  expected_returns_method?: string    // 新增回显
}

// ── 离散配置 (D2) ──
export type AllocationMethod = "greedy" | "lp"

export interface AllocateRequest {
  weights: Record<string, number>
  latest_prices: Record<string, number>
  total_value: number
  method: AllocationMethod
}

export interface AllocateResult {
  method: string
  shares: Record<string, number>
  leftover_cash: number
  allocated_value: number
  total_value: number
  allocation_weights: Record<string, number>
  rmse: number
  skipped: string[]
}

// ── 策略 ──────────────────────────────────────────────────────
export interface Strategy {
  name: string
  description: string
}

// ── 实盘策略 ──────────────────────────────────────────────────
export type LiveStrategyState = "idle" | "running" | "stopped" | "error"

export interface PaperTrade {
  timestamp: string
  side: "BUY" | "SELL"
  price: number
  qty: number
  value: number
  realized_pnl: number
  signal_reason: string
}

export interface PaperEquityPoint {
  time: string
  value: number
  pnl_pct: number
}

export interface PaperSimResult {
  initial_cash: number
  cash: number
  position: number
  avg_cost: number
  equity_curve: PaperEquityPoint[]
  trades: PaperTrade[]
  total_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  win_rate_pct: number
  profit_factor: number
  total_trades: number
  buy_hold_return_pct: number
  sim_start: string
  sim_end: string
  sim_days: number
}

export interface LiveStrategyInstance {
  instance_id: string
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  params: Record<string, unknown>
  state: LiveStrategyState
  error: string | null
  bars_processed: number
  orders_placed: number
  started_at: string | null
  stopped_at: string | null
  paper: PaperSimResult | null
}

export interface StartStrategyRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  params: Record<string, unknown>
  warmup_days: number
  sim_days: number
  instance_id?: string
}

// ── 市场概览 ──────────────────────────────────────────────────
export interface MarketOverviewItem {
  symbol: string
  market: string
  name: string
  name_zh: string | null
  price: number | null
  prev_close: number | null
  change_pct: number | null
}

export interface MarketOverview {
  A: MarketOverviewItem[]
  HK: MarketOverviewItem[]
  US: MarketOverviewItem[]
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

// ── 动态防护 / 熔断 ────────────────────────────────────────────
export type ProtectionType =
  | "stoploss_guard"
  | "cooldown_period"
  | "max_drawdown"
  | "low_profit_pairs"

export type LockScope = "global" | "symbol"

export interface ProtectionRuleConfig {
  type: ProtectionType
  enabled: boolean
  stop_duration_minutes: number
  lookback_minutes?: number
  trade_limit?: number
  required_profit?: number
  only_per_symbol?: boolean
  max_allowed_drawdown?: number
  min_profit_ratio?: number
  required_trades?: number
}

export interface ProtectionsConfig {
  is_active: boolean
  rules: ProtectionRuleConfig[]
}

export interface ActiveLock {
  id: string
  scope: LockScope
  symbol: string | null
  market: Market | null
  reason: string
  protection_type: ProtectionType
  locked_at: string
  until: string
  active: boolean
}

// ── 多渠道通知 ─────────────────────────────────────────────────
export type ChannelType = "telegram" | "webhook"
export type WebhookFormat = "json" | "form" | "raw"

export type NotifyEventType =
  | "trade_fill"
  | "order_reject"
  | "pnl_update"
  | "position"
  | "daily_summary"
  | "risk_alert"
  | "protection"

export interface TelegramChannelConfig {
  bot_token: string
  chat_id: string
  parse_mode: "HTML" | "Markdown"
}

export interface WebhookChannelConfig {
  url: string
  format: WebhookFormat
  timeout_seconds: number
  retries: number
  retry_delay_seconds: number
  secret_header?: string | null
  secret_value?: string | null
}

export interface ChannelConfig {
  id: string
  type: ChannelType
  name: string
  enabled: boolean
  events: NotifyEventType[]
  telegram?: TelegramChannelConfig | null
  webhook?: WebhookChannelConfig | null
}

export interface NotifyConfig {
  is_active: boolean
  channels: ChannelConfig[]
  min_pnl_notify_abs: number
  daily_summary_time: string
}

export interface TelegramChannelStatus {
  configured: boolean
  token_hint: string | null
  chat_id: string
  parse_mode: "HTML" | "Markdown"
}

export interface WebhookChannelStatus {
  url: string
  format: WebhookFormat
  timeout_seconds: number
  retries: number
  retry_delay_seconds: number
  has_secret: boolean
}

export interface ChannelStatus {
  id: string
  type: ChannelType
  name: string
  enabled: boolean
  events: NotifyEventType[]
  telegram?: TelegramChannelStatus | null
  webhook?: WebhookChannelStatus | null
}

export interface NotifyConfigStatus {
  is_active: boolean
  channels: ChannelStatus[]
  min_pnl_notify_abs: number
  daily_summary_time: string
}

export interface NotifyTestResponse {
  ok: boolean
  channel_id: string
  detail: string | null
  error: string | null
}
