// 因子研究夹具。形状来源见 fixtures/README.md。
//
//   /quant/factor/list          → backend/app/quant/factor_analysis.py:249（AVAILABLE_FACTORS）
//   /quant/factor/analyze       → backend/.../quant.py:292 的 return 字典
//                                 序列元素形状见 FactorAnalysisResult（factor_analysis.py:96）：
//                                 factor_series [{time, value}] · ic_series [{time, ic}]
//                                 · cumulative_ic [{time, cum_ic}] · quantile_returns 是 number[]
//   /quant/factor/formula/meta  → backend/.../quant.py:321 = {features, operators, presets}
//   /quant/factor/formula       → 与 analyze 同构，额外带 tokens
//   /quant/factor/fitness       → backend/.../factor_processors.py:255 的 return 字典

const FORWARD_PERIODS = [5, 10, 20]
const POINT_COUNT = 60

function timeAt(i: number): string {
  return new Date(Date.UTC(2025, 0, 2 + i)).toISOString()
}

/** GET /api/v1/quant/factor/list —— AVAILABLE_FACTORS 的子集，字段名一致。 */
export const FACTOR_LIST = [
  { name: "momentum_20", label: "20日动量", group: "动量" },
  { name: "momentum_5", label: "5日动量", group: "动量" },
  { name: "rsi_14", label: "RSI(14)", group: "动量" },
  { name: "macd_hist", label: "MACD 柱", group: "趋势" },
  { name: "bb_position", label: "布林带位置", group: "均值回归" },
  { name: "atr_ratio", label: "ATR 比率（波动）", group: "波动率" },
]

function icSeries(seed: number) {
  return Array.from({ length: POINT_COUNT }, (_, i) => ({
    time: timeAt(i),
    ic: Number((Math.sin((i + seed) / 7) * 0.12).toFixed(4)),
  }))
}

function cumulativeIc(seed: number) {
  let acc = 0
  return icSeries(seed).map((p) => {
    acc += p.ic
    return { time: p.time, cum_ic: Number(acc.toFixed(4)) }
  })
}

/**
 * 各前瞻期的 IC 统计。
 *
 * 20 日一档刻意给「强有效」区间（|ic_mean| ≥ 0.05 且 |ic_ir| ≥ 0.5）：
 * FactorTab 用最长前瞻期的这两个数判定强度并生成结论文案，
 * 落在强有效分支能让断言只对一种文案。
 */
const IC_MEAN = { "5": 0.0182, "10": 0.0364, "20": 0.0713 }
const IC_STD = { "5": 0.0921, "10": 0.0884, "20": 0.0952 }
const IC_IR = { "5": 0.1976, "10": 0.4118, "20": 0.7489 }
const IC_POS_RATE = { "5": 0.5217, "10": 0.5833, "20": 0.6452 }
const IC_ABS_MEAN = { "5": 0.0743, "10": 0.0791, "20": 0.0868 }

/** quantile_returns 是 {period: [Q1..Q5]}，单调递增才会走「多空策略可行」分支。 */
const QUANTILE_RETURNS = {
  "5": [-0.42, -0.11, 0.15, 0.38, 0.71],
  "10": [-0.83, -0.21, 0.29, 0.74, 1.36],
  "20": [-1.54, -0.42, 0.51, 1.32, 2.48],
}

/** POST /api/v1/quant/factor/analyze */
export const FACTOR_ANALYSIS = {
  symbol: "AAPL",
  market: "US",
  factor_name: "momentum_20",
  forward_periods: FORWARD_PERIODS,
  factor_series: Array.from({ length: POINT_COUNT }, (_, i) => ({
    time: timeAt(i),
    value: Number((Math.sin(i / 9) * 4.2).toFixed(4)),
  })),
  ic_series: { "5": icSeries(0), "10": icSeries(3), "20": icSeries(6) },
  cumulative_ic: { "5": cumulativeIc(0), "10": cumulativeIc(3), "20": cumulativeIc(6) },
  ic_mean: IC_MEAN,
  ic_std: IC_STD,
  ic_ir: IC_IR,
  ic_positive_rate: IC_POS_RATE,
  ic_abs_mean: IC_ABS_MEAN,
  quantile_returns: QUANTILE_RETURNS,
}

/**
 * 供 spec 复用的展示值，避免断言里再抄一遍小数位。
 * 位数与组件里的格式化函数一一对应（FactorCharts 的 num()、FactorFitness 的 fmt()）。
 */
export const FACTOR_DISPLAY = {
  icMean20: IC_MEAN["20"].toFixed(4),
  icIr20: IC_IR["20"].toFixed(3),
  /** FactorFitness 用 fmt(result.fitness, 3) 渲染 */
  fitness: (0.412).toFixed(3),
}

/** POST /api/v1/quant/factor/formula —— 与 analyze 同构，多一个 tokens。 */
export const FORMULA_TOKENS = ["MOM20", "ATR_RATIO", "DIV"]

export const FORMULA_FACTOR = {
  ...FACTOR_ANALYSIS,
  factor_name: "MOM20 ATR_RATIO DIV",
  tokens: FORMULA_TOKENS,
}

/**
 * GET /api/v1/quant/factor/formula/meta
 *
 * operators 里的 `label` 在后端是 OpSpec 的 label（如「相除 x/y」），
 * `requires_panel` 只对 CS_* 为 true —— FormulaBuilder 据此把截面算子划掉禁用。
 */
export const FORMULA_META = {
  features: [
    { name: "MOM20", label: "20日动量", group: "动量" },
    { name: "RET1", label: "1日收益", group: "动量" },
    { name: "RSI14", label: "RSI(14)", group: "振荡" },
    { name: "ATR_RATIO", label: "ATR比率", group: "波动率" },
    { name: "ZERO", label: "常量0", group: "常量" },
  ],
  operators: [
    { name: "ADD", label: "相加 x+y", arity: 2, group: "算术", requires_panel: false },
    { name: "DIV", label: "相除 x/y", arity: 2, group: "算术", requires_panel: false },
    { name: "NEG", label: "取负 -x", arity: 1, group: "算术", requires_panel: false },
    { name: "GATE", label: "门控 c>0?x:y", arity: 3, group: "逻辑", requires_panel: false },
    { name: "CS_RANK", label: "截面分位排名(0,1]", arity: 1, group: "截面", requires_panel: true },
  ],
  presets: [
    {
      name: "动量/波动率",
      tokens: FORMULA_TOKENS,
      desc: "风险调整动量：20日动量除以波动率，高动量+低波动得分高",
    },
  ],
}

/**
 * GET /api/v1/factors/strategy/methods
 *
 * FactorStrategyDialog 常驻挂在因子 Tab 上（弹窗关着也会拉这个），不挂桩的话
 * 每次进因子页都会飘三条 404（react-query 默认重试 2 次）。
 * 取值来自 backend/app/strategy/factor_strategy.py:61 的 PORTFOLIO_METHODS。
 */
export const FACTOR_STRATEGY_METHODS = {
  methods: [
    "equal_weight",
    "insight_weight",
    "black_litterman",
    "hrp",
    "max_sharpe",
    "min_cdar",
    "min_cvar",
    "min_volatility",
    "risk_parity",
  ],
}

/**
 * POST /api/v1/quant/factor/fitness
 *
 * `fitness > 0` 且 `activity_gate_passed` 为 true → FactorFitness 走「扣成本后仍为正」
 * 的结论分支，这正是 Playbook 第 3 步「扣成本后适应度仍为正」的验收判据。
 */
export const FACTOR_FITNESS = {
  symbols: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"],
  market: "US",
  base_factor: "momentum_20",
  tokens: null,
  forward_period: 5,
  fitness: 0.412,
  mean_net_return: 0.0031,
  gross_return: 0.0048,
  total_cost: 0.0017,
  turnover: 2.4,
  avg_activity: 12.6,
  n_big_drawdowns: 0,
  activity_gate_passed: true,
  per_instrument_score: {
    AAPL: 0.612, MSFT: 0.481, GOOGL: 0.412, AMZN: 0.204, NVDA: -0.118,
  },
  config_used: {
    fee_rate: 0.001,
    max_impact: 0.02,
    trade_notional: 10000,
    entry_threshold: 0.85,
    drawdown_bar: 0.05,
    drawdown_penalty: 2,
    min_activity: 5,
  },
}
