// 选股器夹具。形状来源见 fixtures/README.md。
//
//   ScreenerRunResponse / CandidateOut → backend/.../screener.py:93 / :65
//   PresetOut                          → backend/.../screener.py:101（criteria 是自由 dict）
//   GET /sectors → list[str]           → backend/app/data/screener_meta.py:14 的 SECTORS
//   MoversResponse                     → backend/.../screener.py:108

/**
 * 后端 `CandidateOut`：除 symbol/market/name/sector 外**全部可空**。
 * 夹具给满值，但类型保留可空性，免得以后往里塞 null 时 tsc 不报错。
 */
export interface CandidateFixture {
  symbol: string
  market: string
  name: string
  sector: string
  price: number | null
  change_pct: number | null
  pe: number | null
  pb: number | null
  market_cap: number | null
  market_cap_yi: number | null
  dividend_yield: number | null
  volume: number | null
  turnover: number | null
  turnover_rate: number | null
}

function candidate(
  symbol: string,
  name: string,
  sector: string,
  price: number,
  changePct: number,
  pe: number,
): CandidateFixture {
  const marketCap = price * 1.6e9
  return {
    symbol,
    market: "US",
    name,
    sector,
    price,
    change_pct: changePct,
    pe,
    pb: Number((pe / 5).toFixed(2)),
    market_cap: marketCap,
    // 后端算法：round(market_cap / 1e8, 2)（screener.py:83）
    market_cap_yi: Number((marketCap / 1e8).toFixed(2)),
    dividend_yield: 0.62,
    volume: 42_000_000,
    turnover: price * 42_000_000,
    turnover_rate: 1.24,
  }
}

/** 三只：足够触发「送组合优化」的 ≥2 门槛，也够点选出多选动作条。 */
export const SCREENER_CANDIDATES: CandidateFixture[] = [
  candidate("AAPL", "Apple Inc.", "科技", 189.5, 2.11, 31.4),
  candidate("MSFT", "Microsoft Corp.", "科技", 432.1, 1.05, 36.2),
  candidate("NVDA", "NVIDIA Corp.", "半导体", 906.8, -1.76, 68.9),
]

/** POST /api/v1/screener/run */
export const SCREENER_RUN = {
  market: "US",
  generated_at: "2025-06-02T20:05:00+00:00",
  universe_size: 33,
  count: SCREENER_CANDIDATES.length,
  candidates: SCREENER_CANDIDATES,
}

/**
 * GET /api/v1/screener/presets
 *
 * 取自 backend/app/data/screener_meta.py:67 的前两条，字段与 `PresetOut` 一致。
 * `criteria` 在后端是无 schema 的 dict，前端当作 `Partial<ScreenerFilter>` 用。
 */
export const SCREENER_PRESETS = [
  {
    id: "value_blue_chip",
    name: "低估值蓝筹",
    desc: "大市值 + 低市盈率，稳健价值风格",
    criteria: { min_market_cap_yi: 1000, max_pe: 20, sort_by: "market_cap", sort_dir: "desc" },
  },
  {
    id: "momentum",
    name: "强势动量",
    desc: "当日涨幅 ≥ 2%，追踪强势标的",
    criteria: { min_change_pct: 2, sort_by: "change_pct", sort_dir: "desc" },
  },
]

/** GET /api/v1/screener/sectors —— 后端 SECTORS 的子集。 */
export const SCREENER_SECTORS = ["科技", "半导体", "金融", "消费", "医药"]

/** GET /api/v1/screener/movers */
export const SCREENER_MOVERS = {
  market: "US",
  generated_at: "2025-06-02T20:05:00+00:00",
  gainers: [SCREENER_CANDIDATES[0], SCREENER_CANDIDATES[1]],
  losers: [SCREENER_CANDIDATES[2]],
}
