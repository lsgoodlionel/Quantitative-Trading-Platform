// 行情夹具。形状来源见 fixtures/README.md 的对照表。
//
//   MarketOverviewResponse / MarketOverviewItem → backend/.../bars.py:74 / :64
//   SpotQuotesResponse / SpotQuote              → backend/.../bars.py:218 / :202
//   BarsListResponse / BarResponse              → backend/.../bars.py:47 / :25
//   /bars/indicators                            → backend/.../bars.py:451（无 response_model，
//                                                  形状是 handler 里的 result 字典）

/** 后端 `MarketOverviewItem`：name_zh / price / prev_close / change_pct 均可为 null。 */
interface OverviewItem {
  symbol: string
  market: string
  name: string
  name_zh: string | null
  price: number | null
  prev_close: number | null
  change_pct: number | null
}

function overviewItem(
  symbol: string,
  market: string,
  name: string,
  nameZh: string | null,
  price: number,
  prevClose: number,
): OverviewItem {
  return {
    symbol,
    market,
    name,
    name_zh: nameZh,
    price,
    prev_close: prevClose,
    change_pct: Number((((price - prevClose) / prevClose) * 100).toFixed(2)),
  }
}

/** GET /api/v1/bars/market-overview */
export const MARKET_OVERVIEW = {
  A: [
    overviewItem("000001", "A", "Ping An Bank", "平安银行", 11.42, 11.2),
    overviewItem("600519", "A", "Kweichow Moutai", "贵州茅台", 1638.0, 1655.5),
  ],
  HK: [
    overviewItem("00700", "HK", "Tencent", "腾讯控股", 382.6, 376.4),
  ],
  US: [
    overviewItem("AAPL", "US", "Apple Inc.", "苹果", 189.5, 186.2),
    overviewItem("MSFT", "US", "Microsoft Corp.", "微软", 432.1, 428.9),
    overviewItem("NVDA", "US", "NVIDIA Corp.", "英伟达", 906.8, 921.3),
  ],
}

/**
 * GET /api/v1/bars/spot
 *
 * ⚠️ `source` 必须是 "realtime" / "delayed" / "daily" 之一才会被 MarketPage 合并进概览
 * （`mergedOverview` 会跳过 `source === "demo"`）。后端默认值就是 "demo"，
 * 这里刻意给 "delayed" 以走「有实时价」的分支。
 */
export const SPOT_QUOTES = {
  A: [
    {
      symbol: "000001", market: "A", name: "Ping An Bank", name_zh: "平安银行",
      price: 11.45, prev_close: 11.2, change_pct: 2.23, change: 0.25,
      volume: 91_233_400, high: 11.5, low: 11.18,
      source: "delayed", updated_at: "2025-06-02T07:00:00+00:00",
    },
  ],
  HK: [
    {
      symbol: "00700", market: "HK", name: "Tencent", name_zh: "腾讯控股",
      price: 383.2, prev_close: 376.4, change_pct: 1.81, change: 6.8,
      volume: 12_004_500, high: 384.0, low: 377.2,
      source: "delayed", updated_at: "2025-06-02T07:00:00+00:00",
    },
  ],
  US: [
    {
      symbol: "AAPL", market: "US", name: "Apple Inc.", name_zh: "苹果",
      price: 190.12, prev_close: 186.2, change_pct: 2.11, change: 3.92,
      volume: 54_120_900, high: 190.8, low: 186.5,
      source: "delayed", updated_at: "2025-06-02T20:00:00+00:00",
    },
    {
      symbol: "MSFT", market: "US", name: "Microsoft Corp.", name_zh: "微软",
      price: 433.4, prev_close: 428.9, change_pct: 1.05, change: 4.5,
      volume: 21_004_100, high: 434.0, low: 429.1,
      source: "delayed", updated_at: "2025-06-02T20:00:00+00:00",
    },
    {
      symbol: "NVDA", market: "US", name: "NVIDIA Corp.", name_zh: "英伟达",
      price: 905.1, prev_close: 921.3, change_pct: -1.76, change: -16.2,
      volume: 41_882_300, high: 923.0, low: 901.4,
      source: "delayed", updated_at: "2025-06-02T20:00:00+00:00",
    },
  ],
}

/** 后端 `BarResponse`：time 是 ISO 字符串，volume 是 int，vwap 可为 null。 */
export interface BarFixture {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  vwap: number | null
}

const BAR_COUNT = 40

/**
 * 生成一段确定性的 K 线（正弦 + 线性漂移）。
 *
 * 用固定公式而非随机数：随机的夹具会让「昨天绿今天红」重演，
 * 而那正是 §2.1 要避免的东西。
 */
export function makeBars(count: number = BAR_COUNT, base = 180): BarFixture[] {
  return Array.from({ length: count }, (_, i) => {
    const drift = base + i * 0.4 + Math.sin(i / 3) * 3
    const open = Number(drift.toFixed(2))
    const close = Number((drift + Math.cos(i / 4) * 1.2).toFixed(2))
    return {
      time: new Date(Date.UTC(2025, 0, 2 + i)).toISOString(),
      open,
      high: Number((Math.max(open, close) + 1.1).toFixed(2)),
      low: Number((Math.min(open, close) - 1.1).toFixed(2)),
      close,
      volume: 30_000_000 + i * 100_000,
      vwap: Number(((open + close) / 2).toFixed(2)),
    }
  })
}

/** GET /api/v1/bars —— 后端 `BarsListResponse`（注意有 `count`，前端目前没读）。 */
export function barsResponse(symbol: string, market: string, frequency = "1d") {
  const bars = makeBars()
  return { symbol, market, frequency, count: bars.length, bars }
}

/** GET /api/v1/bars/latest —— 后端返回单个 `BarResponse` 或 null。 */
export function latestBar(close = 189.5): BarFixture {
  return {
    time: "2025-06-02T00:00:00+00:00",
    open: Number((close - 1.4).toFixed(2)),
    high: Number((close + 2.1).toFixed(2)),
    low: Number((close - 2.6).toFixed(2)),
    close,
    volume: 48_221_000,
    vwap: close,
  }
}

/**
 * GET /api/v1/bars/indicators
 *
 * 后端按请求的 indicators 动态拼键：`{"time": [...], "rsi": [...], ...}`，
 * 值是 `list[float | None]`，与 time 等长。这里只给 QuoteTab 默认选中的 rsi。
 */
export const INDICATORS = (() => {
  const bars = makeBars()
  return {
    time: bars.map((b) => b.time),
    rsi: bars.map((_, i) => (i < 14 ? null : Number((50 + Math.sin(i / 5) * 18).toFixed(4)))),
  }
})()

/** GET /api/v1/bars/symbols/search —— 后端 `SymbolSearchResult`。 */
export const SYMBOL_SEARCH = [
  {
    symbol: "AAPL", market: "US", name: "Apple Inc.", name_zh: "苹果",
    exchange: "NASDAQ", currency: "USD",
  },
]
