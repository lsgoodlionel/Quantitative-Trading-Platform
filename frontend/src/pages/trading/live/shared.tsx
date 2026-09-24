// 策略模拟盘的公共展示件：策略中文名、市场/周期选项、运行状态徽章与小格式化。
import type { LiveStrategyState, Market, Frequency } from "@/types"

// ── 策略中文名映射 ─────────────────────────────────────────────
/** 「调整重跑」回填启动表单的一组值：实例卡、后续操作页与主列表都要传它 */
export interface RerunValues {
  strategy_name: string
  symbol: string
  market: Market
  frequency: Frequency
  params: Record<string, unknown>
  sim_days: number
}

export const STRATEGY_LABELS: Record<string, string> = {
  double_ma:           "双均线交叉",
  triple_ma:           "三均线顺势",
  macd:                "MACD 信号",
  supertrend:          "Supertrend 趋势",
  adx_trend:           "ADX 趋势过滤",
  bollinger:           "布林带均值回归",
  rsi_mean_reversion:  "RSI 均值回归",
  stochastic:          "随机指标 KD",
  vwap_reversion:      "VWAP 均值回归",
  donchian_breakout:   "唐奇安突破",
  keltner_breakout:    "凯尔特纳突破",
  atr_breakout:        "ATR 波动率突破",
  momentum:            "价格动量",
  multi_factor:        "多因子模型",
  grid_trading:        "网格交易",
  pairs_trading:       "配对套利",
}

export const MARKETS: { value: Market; label: string }[] = [
  { value: "US", label: "🇺🇸 美股" },
  { value: "HK", label: "🇭🇰 港股" },
  { value: "A",  label: "🇨🇳 A股"  },
]

export const FREQS: { value: Frequency; label: string }[] = [
  { value: "1m",  label: "1分钟" },
  { value: "5m",  label: "5分钟" },
  { value: "15m", label: "15分钟" },
  { value: "1h",  label: "1小时" },
  { value: "1d",  label: "日线" },
]

export const STATE_CFG: Record<LiveStrategyState, { label: string; dot: string; text: string }> = {
  idle:    { label: "待机",   dot: "bg-[#30363d]",  text: "text-[#8b949e]" },
  running: { label: "运行中", dot: "bg-[#3fb950]",  text: "text-[#3fb950]" },
  stopped: { label: "已停止", dot: "bg-[#8b949e]",  text: "text-[#8b949e]" },
  error:   { label: "错误",   dot: "bg-[#f85149]",  text: "text-[#f85149]" },
}

// ── 工具 ──────────────────────────────────────────────────────
export function pct(v: number) {
  const color = v >= 0 ? "text-[#3fb950]" : "text-[#f85149]"
  return <span className={`font-mono font-bold ${color}`}>{v >= 0 ? "+" : ""}{v.toFixed(2)}%</span>
}

export function elapsed(startedAt: string | null): string {
  if (!startedAt) return "—"
  const diff = Date.now() - new Date(startedAt).getTime()
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

// ── 状态徽章 ──────────────────────────────────────────────────
export function StateBadge({ state }: { state: LiveStrategyState }) {
  const cfg = STATE_CFG[state]
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${cfg.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} ${state === "running" ? "animate-pulse" : ""}`} />
      {cfg.label}
    </span>
  )
}
