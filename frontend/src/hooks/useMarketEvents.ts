import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── 新闻类型（对齐后端 news_calendar_models.py）──────────────────

export interface CompanyNewsItem {
  published_at: string | null
  title: string
  publisher: string | null
  author: string | null
  summary: string | null
  url: string | null
  thumbnail: string | null
  symbols: string[]
}

export interface CompanyNewsResponse {
  symbol: string
  market: string
  count: number
  items: CompanyNewsItem[]
  warnings: string[]
}

// ── 财报日历类型 ─────────────────────────────────────────────────

export interface EarningsEvent {
  report_date: string | null
  symbol: string | null
  name: string | null
  period: string | null
  eps_estimate: number | null
  eps_actual: number | null
  eps_previous: number | null
  surprise_percent: number | null
  is_upcoming: boolean
}

export interface EarningsCalendarResponse {
  symbol: string
  market: string
  count: number
  events: EarningsEvent[]
  warnings: string[]
}

// ── 分红日历类型 ─────────────────────────────────────────────────

export interface DividendEvent {
  ex_dividend_date: string | null
  symbol: string | null
  name: string | null
  amount: number | null
  record_date: string | null
  payment_date: string | null
  declaration_date: string | null
  dividend_yield: number | null
  period: string | null
  is_upcoming: boolean
}

export interface DividendCalendarResponse {
  symbol: string
  market: string
  count: number
  events: DividendEvent[]
  warnings: string[]
}

// ── 期权类型（对齐后端 options_models.py）────────────────────────

export interface OptionsExpirationsResponse {
  symbol: string
  underlying_price: number | null
  expirations: string[]
  warnings: string[]
}

export interface OptionContract {
  contract_symbol: string | null
  option_type: "call" | "put"
  expiration: string | null
  dte: number | null
  strike: number
  last_price: number | null
  bid: number | null
  ask: number | null
  mark: number | null
  change: number | null
  percent_change: number | null
  volume: number | null
  open_interest: number | null
  implied_volatility: number | null
  in_the_money: boolean | null
  delta: number | null
  gamma: number | null
  theta: number | null
  vega: number | null
}

export interface OptionsChainResponse {
  symbol: string
  underlying_price: number | null
  expiration: string | null
  risk_free_rate: number
  calls: OptionContract[]
  puts: OptionContract[]
  warnings: string[]
}

// ── Hooks ────────────────────────────────────────────────────────

/** 公司新闻流（US/HK；A 股无源，返回空 + warning）。新闻更新快，staleTime 5 分钟。 */
export function useCompanyNews(symbol: string, market: Market, limit = 20) {
  return useQuery<CompanyNewsResponse>({
    queryKey: ["company-news", market, symbol, limit],
    queryFn: () =>
      api.get<CompanyNewsResponse>(
        `/api/v1/news/${encodeURIComponent(symbol)}?market=${market}&limit=${limit}`,
      ),
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}

/** 财报日历（按标的）。事件变动慢，staleTime 1 小时。 */
export function useEarningsCalendar(symbol: string, market: Market, limit = 12) {
  return useQuery<EarningsCalendarResponse>({
    queryKey: ["earnings-calendar", market, symbol, limit],
    queryFn: () =>
      api.get<EarningsCalendarResponse>(
        `/api/v1/calendar/earnings?symbol=${encodeURIComponent(symbol)}&market=${market}&limit=${limit}`,
      ),
    enabled: !!symbol,
    staleTime: 60 * 60 * 1000,
  })
}

/** 分红日历（按标的）。 */
export function useDividendCalendar(symbol: string, market: Market, limit = 12) {
  return useQuery<DividendCalendarResponse>({
    queryKey: ["dividend-calendar", market, symbol, limit],
    queryFn: () =>
      api.get<DividendCalendarResponse>(
        `/api/v1/calendar/dividends?symbol=${encodeURIComponent(symbol)}&market=${market}&limit=${limit}`,
      ),
    enabled: !!symbol,
    staleTime: 60 * 60 * 1000,
  })
}

/** 期权到期日列表（仅美股）。enabled 由调用方按市场控制。 */
export function useOptionExpirations(symbol: string, enabled: boolean) {
  return useQuery<OptionsExpirationsResponse>({
    queryKey: ["option-expirations", symbol],
    queryFn: () =>
      api.get<OptionsExpirationsResponse>(
        `/api/v1/options/${encodeURIComponent(symbol)}/expirations`,
      ),
    enabled: enabled && !!symbol,
    staleTime: 10 * 60 * 1000,
  })
}

/** 期权链 + Greeks（仅美股）。需 symbol + expiration 均就绪。 */
export function useOptionChain(
  symbol: string,
  expiration: string | null,
  enabled: boolean,
) {
  return useQuery<OptionsChainResponse>({
    queryKey: ["option-chain", symbol, expiration],
    queryFn: () =>
      api.get<OptionsChainResponse>(
        `/api/v1/options/${encodeURIComponent(symbol)}/chain?expiration=${encodeURIComponent(expiration ?? "")}`,
      ),
    enabled: enabled && !!symbol && !!expiration,
    staleTime: 60 * 1000,
  })
}
