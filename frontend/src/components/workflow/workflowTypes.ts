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

// ── 辅助：根据市场状态推荐策略 ─────────────────────────────

export function buildStrategyOptions(condition: MarketCondition): StrategyOption[] {
  const ALL: Record<string, Omit<StrategyOption, "suitability" | "reason" | "warning">> = {
    double_ma: {
      id: "double_ma",
      name: "双均线趋势",
      description: "快线金叉买入，死叉卖出",
      params: { fast_period: 10, slow_period: 30, ma_type: "sma" },
    },
    macd: {
      id: "macd",
      name: "MACD动量",
      description: "MACD线金叉买入，死叉卖出",
      params: { fast: 12, slow: 26, signal: 9 },
    },
    bollinger: {
      id: "bollinger",
      name: "布林带均值回归",
      description: "触及下轨买入，触及中轨或上轨卖出",
      params: { period: 20, std_dev: 2.0 },
    },
    rsi_mean_reversion: {
      id: "rsi_mean_reversion",
      name: "RSI均值回归",
      description: "RSI<30超卖买入，>70超买卖出",
      params: { period: 14, oversold: 30, overbought: 70 },
    },
  }

  const MAP: Record<MarketCondition, StrategyOption[]> = {
    strong_bull: [
      { ...ALL.double_ma, suitability: "excellent", reason: "强趋势市场中，均线顺势最有效，快速捕捉持续上涨", warning: "若趋势突然反转，均线反应稍慢" },
      { ...ALL.macd,      suitability: "good",      reason: "MACD动量指标与强趋势高度契合", warning: undefined },
      { ...ALL.bollinger, suitability: "fair",       reason: "均值回归在强趋势中收益有限", warning: "强趋势中价格可能长期贴近上轨" },
      { ...ALL.rsi_mean_reversion, suitability: "poor", reason: "超买超卖信号在强趋势中频繁触发假信号", warning: "强趋势中RSI长期维持在高位" },
    ],
    mild_bull: [
      { ...ALL.double_ma, suitability: "good",      reason: "温和趋势适合均线跟随，稳定获取趋势收益", warning: "温和趋势中可能有较多震荡" },
      { ...ALL.macd,      suitability: "good",      reason: "MACD在趋势形成期给出较清晰信号", warning: undefined },
      { ...ALL.bollinger, suitability: "good",       reason: "温和涨势中布林带既能捕捉趋势又能管理回调", warning: undefined },
      { ...ALL.rsi_mean_reversion, suitability: "fair", reason: "轻微趋势下均值回归有一定效果", warning: "注意趋势期间RSI信号准确率下降" },
    ],
    neutral: [
      { ...ALL.bollinger, suitability: "excellent", reason: "震荡市场最适合均值回归，价格在上下轨之间来回", warning: "若震荡市突破变为趋势，需及时调整" },
      { ...ALL.rsi_mean_reversion, suitability: "excellent", reason: "震荡市中RSI超买超卖信号最准确", warning: undefined },
      { ...ALL.double_ma, suitability: "fair",      reason: "震荡市中均线频繁金叉死叉，手续费损耗大", warning: "震荡市中均线策略会产生大量假信号" },
      { ...ALL.macd,      suitability: "fair",       reason: "震荡市中MACD柱频繁正负切换，信号噪声大", warning: "建议搭配其他过滤条件使用" },
    ],
    mild_bear: [
      { ...ALL.bollinger, suitability: "good",      reason: "下跌中布林带下轨买入捕捉反弹，但需设好止损", warning: "若下跌趋势持续，均值回归可能失效" },
      { ...ALL.rsi_mean_reversion, suitability: "good", reason: "下跌中RSI超卖信号可捕捉反弹机会", warning: "仅适合短线反弹，不适合持有" },
      { ...ALL.double_ma, suitability: "fair",      reason: "下跌趋势中可做空信号，但平台默认做多", warning: "温和下跌可能在短期内反转" },
      { ...ALL.macd,      suitability: "poor",       reason: "下跌趋势中MACD频繁死叉，不适合入场做多", warning: "不建议在下跌趋势中做多" },
    ],
    strong_bear: [
      { ...ALL.rsi_mean_reversion, suitability: "fair", reason: "RSI极度超卖(<20)可能出现大级别反弹，但风险极高", warning: "⚠️ 强势下跌建议观望，不宜轻易入场" },
      { ...ALL.bollinger, suitability: "fair",      reason: "价格极度偏离下轨时可小仓试探反弹", warning: "⚠️ 强势下跌中反弹幅度有限且持续时间短" },
      { ...ALL.double_ma, suitability: "poor",      reason: "强势下跌中不适合做多", warning: "⚠️ 强烈建议等待趋势明确后再入场" },
      { ...ALL.macd,      suitability: "poor",       reason: "强势下跌中MACD一直处于负值区，不宜入场", warning: "⚠️ 切勿逆势操作" },
    ],
  }

  return MAP[condition]
}
