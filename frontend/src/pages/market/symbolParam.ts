import { MARKET_CONFIGS } from "./config"
import type { Market } from "@/types"

export interface SymbolSelection {
  symbol: string
  market: Market
}

const VALID_MARKETS: readonly Market[] = ["US", "HK", "A"]

/**
 * 把 URL 上的 market 归一化到白名单内。
 *
 * 外部输入一律不可信：不在白名单里就回落到 US，
 * 否则一个手改过的链接会把非法市场直接传给行情接口。
 */
export function normalizeMarket(raw: string | null | undefined): Market {
  const upper = (raw ?? "").toUpperCase()
  return VALID_MARKETS.includes(upper as Market) ? (upper as Market) : "US"
}

/**
 * 从 URL 读取选股器/站内链接送来的标的（`?symbol=AAPL&market=US`）。
 */
export function readSymbolSelection(search: string): SymbolSelection {
  const params = new URLSearchParams(search)
  const market = normalizeMarket(params.get("market"))
  const symbol = (params.get("symbol") ?? "").trim().toUpperCase()

  return {
    symbol: symbol || (MARKET_CONFIGS.find((c) => c.value === market)?.defaultSymbol ?? "AAPL"),
    market,
  }
}
