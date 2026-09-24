// 仪表盘夹具。
//
// 仪表盘不属于三条 Playbook，但登录成功后就落在这里 —— 不挂桩的话首页会飘一串
// 404，把 trace 里真正有用的信息淹掉。所以只补它启动时那几个查询，
// 数据都取「空/默认」，不做任何断言。
//
//   GET /orders               → list[OrderResponse]，空列表是合法响应
//   GET /orders/trading-mode  → backend/.../orders.py:120 的 return 字典
//   GET /risk/summary         → backend/app/risk/engine.py:228 的 daily_summary()

/** GET /api/v1/orders —— 空委托列表。 */
export const ORDERS: unknown[] = []

/** GET /api/v1/orders/trading-mode —— 未接券商时的真实取值组合。 */
export const TRADING_MODE = {
  configured: false,
  paper_mode: true,
  mode_label: "本地纸面交易（模拟）",
  gateway_type: "local_paper",
  base_url: "",
}

/** GET /api/v1/risk/summary —— RiskEngine.daily_summary() 的字段。 */
export const RISK_SUMMARY = {
  date: "2025-06-02",
  orders_today: 0,
  realized_pnl_today: 0.0,
  peak_portfolio_value: 100_430.0,
}
