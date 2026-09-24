// 行情页共享配置：市场/周期定义、默认自选池、指标选项与价格格式化。
// 三个 Tab（行情查询 / 自选行情 / 事件期权）都要用，故单独成文件。
import { format, subMonths, subYears } from "date-fns"
import type { IndicatorKey } from "@/hooks/useIndicators"
import type { Market, Frequency } from "@/types"

// ── 市场配置 ──────────────────────────────────────────────────
export interface MarketConfig {
  value: Market
  label: string
  currency: string
  defaultSymbol: string
  defaultFreq: Frequency
  allowedFreqs: Frequency[]
}

export const MARKET_CONFIGS: MarketConfig[] = [
  {
    value: "US",
    label: "美股",
    currency: "$",
    defaultSymbol: "AAPL",
    defaultFreq: "1d",
    allowedFreqs: ["1m", "5m", "15m", "1h", "1d", "1w"],
  },
  {
    value: "HK",
    label: "港股",
    currency: "HK$",
    defaultSymbol: "00700",
    defaultFreq: "1d",
    allowedFreqs: ["1d", "1w"],
  },
  {
    value: "A",
    label: "A股",
    currency: "¥",
    defaultSymbol: "000001",
    defaultFreq: "1d",
    allowedFreqs: ["1d", "1w"],
  },
]

export const FREQUENCY_LABELS: Record<Frequency, string> = {
  "1m": "1分钟",
  "5m": "5分钟",
  "15m": "15分钟",
  "1h": "1小时",
  "1d": "日线",
  "1w": "周线",
}

// ── 默认自选列表 ──────────────────────────────────────────────
export const DEFAULT_WATCHLIST: { symbol: string; market: Market; name: string }[] = [
  { symbol: "AAPL", market: "US", name: "苹果" },
  { symbol: "MSFT", market: "US", name: "微软" },
  { symbol: "NVDA", market: "US", name: "英伟达" },
  { symbol: "TSLA", market: "US", name: "特斯拉" },
  { symbol: "000001", market: "A", name: "平安银行" },
  { symbol: "600519", market: "A", name: "贵州茅台" },
  { symbol: "000858", market: "A", name: "五粮液" },
  { symbol: "00700", market: "HK", name: "腾讯" },
]

// ── 技术指标配置 ──────────────────────────────────────────────
export const INDICATOR_OPTIONS: { key: IndicatorKey; label: string; group: string }[] = [
  { key: "rsi",       label: "RSI",       group: "震荡" },
  { key: "macd",      label: "MACD",      group: "震荡" },
  { key: "stoch",     label: "KDJ",       group: "震荡" },
  { key: "cci",       label: "CCI",       group: "震荡" },
  { key: "williams_r",label: "威廉斯%R",   group: "震荡" },
  { key: "mfi",       label: "MFI",       group: "震荡" },
  { key: "roc",       label: "ROC",       group: "震荡" },
  { key: "adx",       label: "ADX",       group: "趋势" },
  { key: "atr",       label: "ATR",       group: "波动" },
  { key: "bb",        label: "布林带",     group: "叠加" },
  { key: "donchian",  label: "唐奇安",     group: "叠加" },
  { key: "keltner",   label: "凯尔特纳",   group: "叠加" },
  { key: "obv",       label: "OBV",       group: "量价" },
  { key: "vwap",      label: "VWAP",      group: "叠加" },
]

export function today() { return format(new Date(), "yyyy-MM-dd") }
export function sixMonthsAgo() { return format(subMonths(new Date(), 6), "yyyy-MM-dd") }
export function oneYearAgo() { return format(subYears(new Date(), 1), "yyyy-MM-dd") }

// ── 价格格式化工具 ─────────────────────────────────────────────
export function fmtPrice(v: number | undefined | null, currency: string): string {
  if (v == null) return "—"
  return `${currency}${v.toFixed(2)}`
}

export function fmtChange(cur: number, prev: number) {
  const diff = cur - prev
  const pct = (diff / prev) * 100
  const sign = diff >= 0 ? "+" : ""
  return { diff, pct, label: `${sign}${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`, up: diff >= 0 }
}
