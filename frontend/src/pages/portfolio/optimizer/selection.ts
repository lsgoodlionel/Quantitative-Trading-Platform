import { MARKET_DEFAULTS } from "./options"
import type { Market } from "@/types"

/** 从标的文本解析为大写代码数组 */
export function parseSymbols(text: string): string[] {
  return text
    .split(/[,\s\n]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
}

/**
 * 从 URL 读取选股器带过来的标的（V3 G3：`?symbols=A,B,C&market=US`）。
 *
 * 「URL 即状态」：链接可分享、可收藏、刷新不丢。缺省时回落到市场默认篮子。
 */
export function readInitialSelection(search: string): { symbolsText: string; market: Market } {
  const params = new URLSearchParams(search)
  const marketParam = (params.get("market") ?? "").toUpperCase()
  const market: Market = marketParam in MARKET_DEFAULTS ? (marketParam as Market) : "US"
  const symbols = parseSymbols(params.get("symbols") ?? "")
  return {
    symbolsText: symbols.length > 0
      ? symbols.join(", ")
      : (MARKET_DEFAULTS[market] ?? MARKET_DEFAULTS.US).join(", "),
    market,
  }
}
