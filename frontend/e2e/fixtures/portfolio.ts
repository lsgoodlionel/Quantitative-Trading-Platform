// 组合夹具。形状来源见 fixtures/README.md。
//
//   PortfolioOptResponse   → backend/.../portfolio_opt.py:111
//   frontier[]             → backend/app/engine/portfolio/optimizer.py:224（{vol, ret, sharpe}，均为 %）
//   RebalancePreviewResponse / RebalanceLegSchema → backend/.../rebalance.py:82 / :60
//   PositionResponse / AccountResponse           → backend/.../positions.py:13 / :24
//   /orders/attribution（无 response_model）      → backend/.../orders.py:304

/**
 * POST /api/v1/portfolio/optimize
 *
 * ⚠️ weights 之和必须**精确为 1**：RebalancePanel 在发预览请求前会自己校验
 * `|sum - 1| > 1e-4` 并就地报错（RebalancePanel.tsx:168），
 * 权重凑不齐 1 的话「预览调仓」根本不会发出请求。
 *
 * 单位说明（与后端 optimizer.py:375 一致）：
 * expected_return / expected_volatility / cvar_95 / risk_contributions 都是**百分数**，
 * 只有 weights 是 0~1 小数。
 */
export const OPTIMIZE_RESULT = {
  method: "max_sharpe",
  weights: { AAPL: 0.4212, MSFT: 0.3564, NVDA: 0.2224 },
  expected_return: 18.42,
  expected_volatility: 21.07,
  sharpe_ratio: 0.779,
  cvar_95: -3.18,
  frontier: [
    { vol: 16.2, ret: 11.4, sharpe: 0.58 },
    { vol: 18.7, ret: 15.1, sharpe: 0.7 },
    { vol: 21.07, ret: 18.42, sharpe: 0.779 },
    { vol: 25.4, ret: 21.8, sharpe: 0.74 },
  ],
  risk_contributions: { AAPL: 38.6, MSFT: 32.1, NVDA: 29.3 },
  risk_model: "sample_cov",
  expected_returns_method: "mean_historical",
  // 非 BL / 非 HRP 路径下后端仍会返回这些字段的默认值
  bl_prior_returns: {},
  bl_posterior_returns: {},
  bl_risk_aversion: null,
  bl_views: [],
  linkage_method: null,
  cvar_beta: null,
}

/**
 * POST /api/v1/portfolio/rebalance/preview
 *
 * `reason` 必须落在 open/close/increase/decrease 之内 —— RebalancePanel 的
 * REASON_LABEL 是按这四个键查表的（后端 schema 上只是宽松的 str）。
 */
export const REBALANCE_PREVIEW = {
  market: "US",
  portfolio_value: 100_430.0,
  legs: [
    {
      symbol: "AAPL", current_qty: 50, target_qty: 223, delta_qty: 173,
      price: 189.5, delta_value: 32_783.5, reason: "increase", side: "BUY",
    },
    {
      symbol: "MSFT", current_qty: 30, target_qty: 82, delta_qty: 52,
      price: 432.1, delta_value: 22_469.2, reason: "increase", side: "BUY",
    },
    {
      symbol: "NVDA", current_qty: 10, target_qty: 24, delta_qty: 14,
      price: 906.8, delta_value: 12_695.2, reason: "increase", side: "BUY",
    },
    {
      symbol: "TSLA", current_qty: 20, target_qty: 0, delta_qty: -20,
      price: 177.3, delta_value: -3_546.0, reason: "close", side: "SELL",
    },
  ],
  total_buy_value: 67_947.9,
  total_sell_value: 3_546.0,
  estimated_commission: 21.44,
  warnings: ["TSLA 不在目标权重内，将被清仓"],
  confirm_token: "e2e-confirm-token",
  expires_in_seconds: 60,
}

/** GET /api/v1/positions?market=US —— 与后端 _DEMO_POSITIONS 同构。 */
export const POSITIONS = [
  {
    symbol: "AAPL", market: "US", qty: 50, avg_cost: 178.2,
    current_price: 189.5, market_value: 9_475.0,
    unrealized_pnl: 565.0, unrealized_pnl_pct: 0.0634,
  },
  {
    symbol: "MSFT", market: "US", qty: 30, avg_cost: 415.8,
    current_price: 432.1, market_value: 12_963.0,
    unrealized_pnl: 489.0, unrealized_pnl_pct: 0.0392,
  },
  {
    symbol: "NVDA", market: "US", qty: 10, avg_cost: 875.4,
    current_price: 906.8, market_value: 9_068.0,
    unrealized_pnl: 314.0, unrealized_pnl_pct: 0.0359,
  },
  {
    symbol: "TSLA", market: "US", qty: 20, avg_cost: 195.6,
    current_price: 177.3, market_value: 3_546.0,
    unrealized_pnl: -366.0, unrealized_pnl_pct: -0.0936,
  },
]

/** GET /api/v1/positions/account?market=US */
export const ACCOUNT = {
  account_id: "DEMO-001",
  currency: "USD",
  cash: 85_430.0,
  buying_power: 170_860.0,
  portfolio_value: 100_430.0,
}

/**
 * GET /api/v1/orders/attribution
 *
 * 未接 OMS 时后端返回 `{"positions": [], "totals": {}}`；接上后是下面这个结构。
 * 夹具给非空版本，好让持仓页的归因区块真的渲染出来。
 */
export const ATTRIBUTION = {
  positions: [
    {
      symbol: "AAPL", market: "US", buy_qty: 150, sell_qty: 100, net_qty: 50,
      buy_value: 26_730.0, sell_value: 18_950.0, avg_buy_cost: 178.2,
      commission: 3.0, realized_pnl: 1_127.0, trade_count: 4,
    },
    {
      symbol: "TSLA", market: "US", buy_qty: 40, sell_qty: 20, net_qty: 20,
      buy_value: 7_824.0, sell_value: 3_546.0, avg_buy_cost: 195.6,
      commission: 2.0, realized_pnl: -368.0, trade_count: 3,
    },
  ],
  totals: {
    realized_pnl: 759.0,
    commission: 5.0,
    trade_count: 7,
    symbol_count: 2,
  },
}
