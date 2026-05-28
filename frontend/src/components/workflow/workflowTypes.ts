// ── 量化交易智能引导工作流 — 类型定义 ───────────────────────────

import type { BacktestResult } from "@/types"
import type { SpotQuote } from "@/hooks/useSpotQuotes"
import type { KellyResult } from "@/hooks/useQuant"

// ── 步骤状态 ──────────────────────────────────────────────────

export type StepStatus =
  | "pending"            // 等待前序步骤完成
  | "running"            // 正在自动执行
  | "waiting_decision"   // 等待用户决策
  | "done"               // 已完成
  | "error"              // 执行出错
  | "skipped"            // 已跳过

// ── 市场状态分类 ──────────────────────────────────────────────

export type MarketCondition =
  | "strong_bull"     // 强势上涨 (>+3%)
  | "mild_bull"       // 温和上涨 (+1%~+3%)
  | "neutral"         // 震荡中性 (-1%~+1%)
  | "mild_bear"       // 温和下跌 (-3%~-1%)
  | "strong_bear"     // 强势下跌 (<-3%)

// ── 策略推荐选项 ──────────────────────────────────────────────

export interface StrategyOption {
  id: string
  name: string
  description: string
  suitability: "excellent" | "good" | "fair" | "poor"
  reason: string          // 推荐理由
  warning?: string        // 潜在风险提示
  params: Record<string, number | string>
}

// ── 回测评估结论 ──────────────────────────────────────────────

export type BacktestVerdict = "pass" | "warn" | "fail"

// ── 工作流全局数据（贯穿所有步骤）────────────────────────────

export interface WorkflowData {
  // Step 1: 股票选择
  symbol: string
  market: string

  // Step 2: 行情快照
  spotQuote: SpotQuote | null

  // Step 3: 技术分析
  condition: MarketCondition | null
  conditionLabel: string
  rsiEstimate: number | null  // 简单估算
  macdDirection: "bullish" | "bearish" | "neutral" | null

  // Step 4: 策略选择 (Decision)
  strategyOptions: StrategyOption[]
  selectedStrategy: StrategyOption | null

  // Step 5: 回测
  backtestResult: BacktestResult | null

  // Step 6: 回测评估 (Decision)
  backtestVerdict: BacktestVerdict | null
  userAcceptedBacktest: boolean

  // Step 7: 凯利/风险计算
  kellyResult: KellyResult | null
  recommendedPositionPct: number  // 0~1

  // Step 8: 仓位确认 (Decision)
  confirmedPositionPct: number  // 0~1

  // Step 9: 模拟盘启动 (Decision)
  paperStrategyId: string | null

  // Step 10: 实盘确认 (Decision) – 仅在用户手动触发时
  liveConfirmed: boolean
}

// ── 单个工作流步骤 ────────────────────────────────────────────

export interface WorkflowStep {
  id: string
  stepNumber: number
  title: string
  type: "auto" | "decision"
  status: StepStatus
  errorMsg?: string
  summary?: string  // 完成后的一句话摘要
}

// ── 工作流完整状态 ────────────────────────────────────────────

export interface WorkflowState {
  phase: "idle" | "running" | "completed" | "abandoned"
  currentStepIndex: number
  steps: WorkflowStep[]
  data: WorkflowData
}

// ── 辅助：初始化 WorkflowData ──────────────────────────────

export function initWorkflowData(): WorkflowData {
  return {
    symbol: "",
    market: "US",
    spotQuote: null,
    condition: null,
    conditionLabel: "",
    rsiEstimate: null,
    macdDirection: null,
    strategyOptions: [],
    selectedStrategy: null,
    backtestResult: null,
    backtestVerdict: null,
    userAcceptedBacktest: false,
    kellyResult: null,
    recommendedPositionPct: 0.1,
    confirmedPositionPct: 0.1,
    paperStrategyId: null,
    liveConfirmed: false,
  }
}

// ── 辅助：根据涨跌幅判断市场状态 ───────────────────────────

export function classifyCondition(changePct: number | null): {
  condition: MarketCondition
  label: string
  color: string
} {
  const pct = changePct ?? 0
  if (pct >= 3)  return { condition: "strong_bull",  label: "强势上涨",  color: "#3fb950" }
  if (pct >= 1)  return { condition: "mild_bull",    label: "温和上涨",  color: "#56d364" }
  if (pct >= -1) return { condition: "neutral",      label: "震荡中性",  color: "#e3b341" }
  if (pct >= -3) return { condition: "mild_bear",    label: "温和下跌",  color: "#f85149" }
  return           { condition: "strong_bear",       label: "强势下跌",  color: "#ff7b72" }
}

// ── 辅助：根据市场状态推荐策略（共 16 种策略）─────────────────

export function buildStrategyOptions(condition: MarketCondition): StrategyOption[] {
  type S = Omit<StrategyOption, "suitability" | "reason" | "warning">

  const ALL: Record<string, S> = {
    double_ma: {
      id: "double_ma", name: "双均线趋势",
      description: "快线金叉买入，死叉卖出",
      params: { fast_period: 10, slow_period: 30, ma_type: "sma" },
    },
    triple_ma: {
      id: "triple_ma", name: "三均线顺势",
      description: "快/中/慢三线全排列确认趋势方向",
      params: { fast_period: 5, mid_period: 13, slow_period: 34 },
    },
    macd: {
      id: "macd", name: "MACD动量",
      description: "MACD线金叉买入，死叉卖出",
      params: { fast: 12, slow: 26, signal: 9 },
    },
    supertrend: {
      id: "supertrend", name: "Supertrend",
      description: "ATR自适应趋势通道，方向翻转即交易",
      params: { period: 10, multiplier: 3.0 },
    },
    adx_trend: {
      id: "adx_trend", name: "ADX趋势过滤",
      description: "只在趋势强度（ADX>25）时执行均线信号",
      params: { fast_period: 10, slow_period: 30, adx_period: 14, adx_threshold: 25 },
    },
    bollinger: {
      id: "bollinger", name: "布林带均值回归",
      description: "触及下轨买入，回到中轨卖出",
      params: { period: 20, std_dev: 2.0 },
    },
    rsi_mean_reversion: {
      id: "rsi_mean_reversion", name: "RSI均值回归",
      description: "RSI<30超卖买入，>70超买卖出",
      params: { period: 14, oversold: 30, overbought: 70 },
    },
    stochastic: {
      id: "stochastic", name: "随机指标KD",
      description: "%K从超卖区上穿%D触发买卖信号",
      params: { k_period: 14, d_period: 3, oversold: 20, overbought: 80 },
    },
    vwap_reversion: {
      id: "vwap_reversion", name: "VWAP均值回归",
      description: "价格偏离VWAP超2%时逆向交易",
      params: { period: 20, dev_threshold: 0.02 },
    },
    donchian_breakout: {
      id: "donchian_breakout", name: "唐奇安突破",
      description: "创N日新高买入，跌破M日新低平仓",
      params: { period: 20, exit_period: 10 },
    },
    keltner_breakout: {
      id: "keltner_breakout", name: "凯尔特纳突破",
      description: "ATR通道突破，假突破率低于布林带",
      params: { ema_period: 20, atr_period: 10, multiplier: 2.0 },
    },
    atr_breakout: {
      id: "atr_breakout", name: "ATR波动率突破",
      description: "N日区间 + ATR倍数的动态突破门槛",
      params: { channel_period: 20, atr_period: 14, multiplier: 0.5 },
    },
    momentum: {
      id: "momentum", name: "价格动量",
      description: "过去N日收益率为正且超门槛时买入",
      params: { lookback: 20, threshold: 0.03 },
    },
    multi_factor: {
      id: "multi_factor", name: "多因子综合",
      description: "动量+RSI+MACD三因子加权评分进出场",
      params: {
        momentum_lookback: 20, rsi_period: 14, rsi_low: 40, rsi_high: 60,
        macd_fast: 12, macd_slow: 26, macd_signal: 9, threshold: 0.6,
      },
    },
    grid_trading: {
      id: "grid_trading", name: "网格交易",
      description: "等间距挂单自动低买高卖，适合震荡行情",
      params: { grid_count: 10, grid_range: 0.1, qty_per_grid: 10 },
    },
    pairs_trading: {
      id: "pairs_trading", name: "配对套利",
      description: "基于价差Z分数的统计套利",
      params: { entry_z: 2.0, exit_z: 0.5, lookback: 60, hedge_ratio: 1.0 },
    },
  }

  const MAP: Record<MarketCondition, StrategyOption[]> = {

    // ── 强势上涨：趋势跟踪策略优先 ─────────────────────────────
    strong_bull: [
      { ...ALL.adx_trend,         suitability: "excellent", reason: "ADX确认趋势强度，只在强趋势中才入场，假信号极少" },
      { ...ALL.double_ma,         suitability: "excellent", reason: "经典趋势跟踪，强势单边市效果最佳", warning: "若趋势突然反转，均线反应稍慢" },
      { ...ALL.triple_ma,         suitability: "excellent", reason: "三线全排列确认，信号质量比双均线更高" },
      { ...ALL.supertrend,        suitability: "good",      reason: "ATR自适应，强势价格方向翻转即追入" },
      { ...ALL.macd,              suitability: "good",      reason: "MACD动量指标与强趋势高度契合" },
      { ...ALL.momentum,          suitability: "good",      reason: "强势延续效应最明显，动量信号准确" },
      { ...ALL.donchian_breakout, suitability: "fair",      reason: "创新高突破在强趋势中有效", warning: "回撤控制依赖止损纪律" },
      { ...ALL.bollinger,         suitability: "poor",      reason: "均值回归在单边上涨中收益有限", warning: "强趋势中价格可能长期贴近上轨" },
    ],

    // ── 温和上涨：趋势与回归均有效 ─────────────────────────────
    mild_bull: [
      { ...ALL.double_ma,          suitability: "good", reason: "温和趋势适合均线跟随，稳定获取趋势收益", warning: "可能有较多震荡噪声" },
      { ...ALL.macd,               suitability: "good", reason: "MACD在趋势形成期给出较清晰信号" },
      { ...ALL.keltner_breakout,   suitability: "good", reason: "ATR通道假突破率低，温和趋势中胜率更稳" },
      { ...ALL.adx_trend,          suitability: "good", reason: "ADX过滤后的均线信号更干净" },
      { ...ALL.bollinger,          suitability: "good", reason: "温和涨势兼顾趋势与回调，通道宽度恰当" },
      { ...ALL.triple_ma,          suitability: "good", reason: "三线顺排确认，减少频繁交易" },
      { ...ALL.momentum,           suitability: "fair", reason: "温和趋势动量信号有效但不突出" },
      { ...ALL.rsi_mean_reversion, suitability: "fair", reason: "轻微趋势下均值回归有一定效果", warning: "注意趋势期间RSI信号准确率下降" },
    ],

    // ── 震荡中性：均值回归策略首选 ─────────────────────────────
    neutral: [
      { ...ALL.bollinger,          suitability: "excellent", reason: "震荡市最适合均值回归，价格在通道间往返", warning: "若震荡市突破变趋势，需及时调整" },
      { ...ALL.rsi_mean_reversion, suitability: "excellent", reason: "震荡市中RSI超买超卖信号最准确" },
      { ...ALL.stochastic,         suitability: "excellent", reason: "KD指标在震荡区间内效果极佳" },
      { ...ALL.vwap_reversion,     suitability: "good",      reason: "VWAP是机构重要参考价，偏离后易回归" },
      { ...ALL.grid_trading,       suitability: "good",      reason: "等间距自动挂单，震荡行情利润最大化" },
      { ...ALL.atr_breakout,       suitability: "fair",      reason: "偶发的小突破也能捕捉" },
      { ...ALL.double_ma,          suitability: "fair",      reason: "震荡市中均线频繁金叉死叉，手续费损耗大", warning: "会产生大量假信号" },
      { ...ALL.macd,               suitability: "fair",      reason: "震荡市中MACD柱频繁正负切换，噪声大", warning: "建议搭配其他过滤条件" },
    ],

    // ── 温和下跌：防御性策略为主 ───────────────────────────────
    mild_bear: [
      { ...ALL.bollinger,          suitability: "good", reason: "下跌中布林带下轨买入捕捉反弹", warning: "若下跌趋势持续，均值回归可能失效" },
      { ...ALL.rsi_mean_reversion, suitability: "good", reason: "下跌中RSI超卖信号可捕捉反弹机会", warning: "仅适合短线反弹，不适合持有" },
      { ...ALL.stochastic,         suitability: "good", reason: "超卖区域KD交叉辅助判断反弹时机" },
      { ...ALL.vwap_reversion,     suitability: "good", reason: "价格极度偏离VWAP后的均值回归机会" },
      { ...ALL.double_ma,          suitability: "fair", reason: "顺势做空逻辑成立，但平台默认做多方向", warning: "温和下跌可能在短期内反转" },
      { ...ALL.donchian_breakout,  suitability: "fair", reason: "谨慎使用，跌破新低时触发止损", warning: "做多方向在跌势中需要严格风控" },
      { ...ALL.macd,               suitability: "poor", reason: "下跌趋势中频繁死叉，不适合入场做多", warning: "不建议在下跌趋势中做多" },
      { ...ALL.momentum,           suitability: "poor", reason: "负动量不触发买入，有效避开下跌" },
    ],

    // ── 强势下跌：建议观望，谨慎试探 ──────────────────────────
    strong_bear: [
      { ...ALL.rsi_mean_reversion, suitability: "fair", reason: "RSI极度超卖(<20)可能有大级别反弹", warning: "⚠️ 强势下跌建议观望，不宜轻易入场" },
      { ...ALL.bollinger,          suitability: "fair", reason: "价格极度偏离下轨时可小仓试探", warning: "⚠️ 强势下跌中反弹幅度有限且持续时间短" },
      { ...ALL.stochastic,         suitability: "fair", reason: "极度超卖可能有技术性反弹信号", warning: "⚠️ 需配合严格止损" },
      { ...ALL.vwap_reversion,     suitability: "fair", reason: "极端偏离后回归概率较高", warning: "⚠️ 仓位须极轻" },
      { ...ALL.multi_factor,       suitability: "fair", reason: "多因子综合评分可降低假信号", warning: "⚠️ 多重确认后谨慎操作" },
      { ...ALL.double_ma,          suitability: "poor", reason: "强势下跌中不适合做多", warning: "⚠️ 强烈建议等待趋势明确后再入场" },
      { ...ALL.macd,               suitability: "poor", reason: "强势下跌中MACD持续处于负值区", warning: "⚠️ 切勿逆势操作" },
      { ...ALL.momentum,           suitability: "poor", reason: "负动量极强，不适合买入" },
    ],
  }

  return MAP[condition]
}
