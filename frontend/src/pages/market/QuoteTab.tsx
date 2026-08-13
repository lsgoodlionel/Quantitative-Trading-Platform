// 行情查询 Tab：自选市场/标的/周期查 K 线 + 技术指标副图。
// 内容由原 pages/Market.tsx 的 QueryPanel 原样迁入（V3 · H1）。
import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { format, subMonths } from "date-fns"
import { CandleChart } from "@/components/charts/CandleChart"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import { useBars } from "@/hooks/useMarketData"
import { useIndicators, type IndicatorKey } from "@/hooks/useIndicators"
import type { Market, Frequency } from "@/types"
import { IndicatorPanel } from "./IndicatorPanel"
import {
  FREQUENCY_LABELS, INDICATOR_OPTIONS, MARKET_CONFIGS,
  fmtChange, fmtPrice, oneYearAgo, sixMonthsAgo, today,
  type MarketConfig,
} from "./config"

// ── 行情查询面板（受外部 symbol/market 控制） ─────────────────
interface QuoteTabProps {
  initialSymbol: string
  initialMarket: Market
}

export function QuoteTab({ initialSymbol, initialMarket }: QuoteTabProps) {
  const navigate = useNavigate()
  const initialCfg = MARKET_CONFIGS.find(c => c.value === initialMarket) ?? MARKET_CONFIGS[0]
  const [marketCfg, setMarketCfg] = useState<MarketConfig>(initialCfg)
  const [symbol, setSymbol] = useState(initialSymbol)
  const [frequency, setFrequency] = useState<Frequency>(initialCfg.defaultFreq)
  const [startDate, setStartDate] = useState(sixMonthsAgo())
  const [endDate, setEndDate] = useState(today())
  const [query, setQuery] = useState<null | {
    symbol: string; market: Market; frequency: Frequency
    start_date: string; end_date: string
  }>(null)
  const [selectedIndicator, setSelectedIndicator] = useState<IndicatorKey>("rsi")

  const { data, isLoading, error } = useBars(query)
  const { data: indData } = useIndicators(
    query
      ? {
          symbol: query.symbol,
          market: query.market,
          frequency: query.frequency,
          start: query.start_date,
          end: query.end_date,
          indicators: [selectedIndicator],
        }
      : null,
  )

  function handleMarketChange(market: Market) {
    const cfg = MARKET_CONFIGS.find((c) => c.value === market) ?? MARKET_CONFIGS[0]
    setMarketCfg(cfg)
    setSymbol(cfg.defaultSymbol)
    setFrequency(cfg.defaultFreq)
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setQuery({
      symbol: symbol.toUpperCase().trim(),
      market: marketCfg.value,
      frequency,
      start_date: startDate,
      end_date: endDate,
    })
  }

  const bars = data?.bars ?? []
  const last = bars.length > 0 ? bars[bars.length - 1] : null
  const prev = bars.length > 1 ? bars[bars.length - 2] : null
  const chg = last && prev ? fmtChange(last.close, prev.close) : null

  return (
    <div>
      {/* Toolbar */}
      <form onSubmit={handleSearch} className="flex flex-wrap gap-3 mb-5 items-end">
        <div>
          <label className="label">市场</label>
          <select
            className="select mt-1"
            value={marketCfg.value}
            onChange={(e) => handleMarketChange(e.target.value as Market)}
          >
            {MARKET_CONFIGS.map((c) => (
              <option key={c.value} value={c.value}>{c.label} ({c.value})</option>
            ))}
          </select>
        </div>

        <div>
          <label className="label">
            标的代码
            <span className="text-[#6e7681] ml-2 text-xs">
              {marketCfg.value === "A" ? "如 000001 / 600519" :
               marketCfg.value === "HK" ? "如 00700 / 09988" : "如 AAPL / MSFT"}
            </span>
          </label>
          <input
            className="input w-36 mt-1 font-mono uppercase"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder={marketCfg.defaultSymbol}
          />
        </div>

        <div>
          <label className="label">周期</label>
          <select
            className="select mt-1"
            value={frequency}
            onChange={(e) => setFrequency(e.target.value as Frequency)}
          >
            {marketCfg.allowedFreqs.map((f) => (
              <option key={f} value={f}>{FREQUENCY_LABELS[f]}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-1.5 self-end">
          {[
            { label: "3月", fn: () => format(subMonths(new Date(), 3), "yyyy-MM-dd") },
            { label: "6月", fn: () => format(subMonths(new Date(), 6), "yyyy-MM-dd") },
            { label: "1年", fn: oneYearAgo },
          ].map(({ label, fn }) => (
            <button
              key={label}
              type="button"
              className="btn btn-ghost text-xs px-2 py-1"
              onClick={() => setStartDate(fn())}
            >
              {label}
            </button>
          ))}
        </div>

        <div>
          <label className="label">开始</label>
          <input
            className="input mt-1"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        <div>
          <label className="label">结束</label>
          <input
            className="input mt-1"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>

        <button type="submit" className="btn btn-primary self-end" disabled={isLoading}>
          {isLoading ? <Spinner size="sm" /> : "查询"}
        </button>
      </form>

      {/* 价格信息 */}
      {last && (
        <div className="flex flex-wrap gap-6 mb-4 items-baseline">
          <span className="font-mono text-3xl font-bold text-[#e6edf3]">
            {fmtPrice(last.close, marketCfg.currency)}
          </span>
          {chg && (
            <span className={`font-mono text-lg font-semibold ${chg.up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {chg.label}
            </span>
          )}
          <span className="text-[#6e7681] text-sm">
            {data?.symbol} · {marketCfg.label} · {FREQUENCY_LABELS[frequency]}
          </span>
          {/* 快捷操作 */}
          {data?.symbol && (
            <div className="ml-auto flex gap-2">
              <button
                onClick={() => navigate(`/backtest?symbol=${data.symbol}&market=${marketCfg.value}`)}
                className="px-3 py-1 rounded text-xs border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                🔬 回测此标的
              </button>
              <button
                onClick={() => navigate(`/trading?tab=live&symbol=${data.symbol}&market=${marketCfg.value}`)}
                className="px-3 py-1 rounded text-xs border border-[#3fb950]/30 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors">
                ▶ 模拟此标的
              </button>
            </div>
          )}
        </div>
      )}

      {/* MA 图例 */}
      {bars.length > 0 && (
        <div className="flex gap-4 mb-2 text-xs">
          <span><span className="inline-block w-3 h-0.5 bg-[#f0a500] mr-1.5 align-middle" />MA5</span>
          <span><span className="inline-block w-3 h-0.5 bg-[#58a6ff] mr-1.5 align-middle" />MA20</span>
          <span><span className="inline-block w-3 h-0.5 bg-[#bc8cff] mr-1.5 align-middle" />MA60</span>
        </div>
      )}

      {/* 图表 */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-96">
            <Spinner size="lg" />
          </div>
        ) : error ? (
          <EmptyState title="加载失败" description={(error as Error).message} />
        ) : bars.length === 0 ? (
          <EmptyState title="请选择市场和标的代码后点击查询" />
        ) : (
          <CandleChart bars={bars} height={420} showMA showVolume />
        )}
      </div>

      {/* 技术指标面板 */}
      {bars.length > 0 && (
        <div className="card mt-4">
          <div className="flex flex-wrap gap-1.5 mb-3">
            {INDICATOR_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                onClick={() => setSelectedIndicator(opt.key)}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  selectedIndicator === opt.key
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                    : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3] hover:border-[#58a6ff]/20"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {indData ? (
            <IndicatorPanel
              indicatorData={indData as Record<string, (number | null)[]>}
              times={indData.time as string[]}
              selectedIndicator={selectedIndicator}
            />
          ) : (
            <div className="h-24 flex items-center justify-center">
              <Spinner size="sm" />
            </div>
          )}
        </div>
      )}

      {/* OHLCV 数据 */}
      {last && (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-4">
          {[
            { label: "开盘", value: fmtPrice(last.open, marketCfg.currency) },
            { label: "最高", value: fmtPrice(last.high, marketCfg.currency) },
            { label: "最低", value: fmtPrice(last.low, marketCfg.currency) },
            { label: "收盘", value: fmtPrice(last.close, marketCfg.currency) },
            { label: "成交量", value: last.volume.toLocaleString() },
            { label: "K线数量", value: bars.length.toLocaleString() },
          ].map(({ label, value }) => (
            <div key={label} className="card py-2 px-3">
              <p className="text-xs text-[#6e7681]">{label}</p>
              <p className="font-mono text-sm text-[#e6edf3] mt-0.5">{value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
