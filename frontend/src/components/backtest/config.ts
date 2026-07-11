import { format, subYears } from "date-fns"
import type { Market, Frequency } from "@/types"

// ── 市场/频率配置 ─────────────────────────────────────────────
export interface MarketCfg {
  value: Market
  label: string
  allowedFreqs: Frequency[]
  defaultFreq: Frequency
}

export const MARKET_CFGS: MarketCfg[] = [
  { value: "US", label: "美股", allowedFreqs: ["1d", "1h", "15m", "5m", "1m"], defaultFreq: "1d" },
  { value: "HK", label: "港股", allowedFreqs: ["1d", "1w"], defaultFreq: "1d" },
  { value: "A",  label: "A股", allowedFreqs: ["1d", "1w"],  defaultFreq: "1d" },
]

export const FREQ_LABELS: Record<string, string> = {
  "1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "1h": "1小时", "1d": "日线", "1w": "周线",
}

export function today() {
  return format(new Date(), "yyyy-MM-dd")
}

export function yearsAgo(n: number) {
  return format(subYears(new Date(), n), "yyyy-MM-dd")
}
