import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { CandleChart } from "@/components/charts/CandleChart"
import { useBars } from "@/hooks/useMarketData"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import type { Market, Frequency } from "@/types"
import { format, subMonths } from "date-fns"

const MARKETS: Market[] = ["US", "HK"]
const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "1d", label: "日线" },
  { value: "1h", label: "1小时" },
  { value: "15m", label: "15分钟" },
  { value: "5m", label: "5分钟" },
  { value: "1m", label: "1分钟" },
]

function today() { return format(new Date(), "yyyy-MM-dd") }
function sixMonthsAgo() { return format(subMonths(new Date(), 6), "yyyy-MM-dd") }

export function Market() {
  const [symbol, setSymbol] = useState("AAPL")
  const [market, setMarket] = useState<Market>("US")
  const [frequency, setFrequency] = useState<Frequency>("1d")
  const [startDate, setStartDate] = useState(sixMonthsAgo())
  const [endDate, setEndDate] = useState(today())
  const [query, setQuery] = useState<null | {
    symbol: string; market: Market; frequency: Frequency
    start_date: string; end_date: string
  }>(null)

  const { data, isLoading, error } = useBars(query)

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setQuery({ symbol: symbol.toUpperCase(), market, frequency, start_date: startDate, end_date: endDate })
  }

  const bars = data?.bars ?? []
  const last = bars.length > 0 ? bars[bars.length - 1] : null
  const prev = bars.length > 1 ? bars[bars.length - 2] : null
  const change = last && prev ? last.close - prev.close : null
  const changePct = last && prev ? ((last.close - prev.close) / prev.close) * 100 : null

  return (
    <AppShell title="行情">
      {/* Toolbar */}
      <form onSubmit={handleSearch} className="flex flex-wrap gap-3 mb-6 items-end">
        <div>
          <label className="label">标的代码</label>
          <input
            className="input w-32 mt-1 font-mono uppercase"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="AAPL"
          />
        </div>
        <div>
          <label className="label">市场</label>
          <select className="select mt-1" value={market} onChange={(e) => setMarket(e.target.value as Market)}>
            {MARKETS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div>
          <label className="label">周期</label>
          <select className="select mt-1" value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
            {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div>
          <label className="label">开始日期</label>
          <input className="input mt-1" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>
        <div>
          <label className="label">结束日期</label>
          <input className="input mt-1" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </div>
        <button type="submit" className="btn btn-primary" disabled={isLoading}>
          {isLoading ? <Spinner size="sm" /> : "查询"}
        </button>
      </form>

      {/* Price Info */}
      {last && (
        <div className="flex flex-wrap gap-6 mb-4 items-baseline">
          <span className="font-mono text-3xl font-bold text-[#e6edf3]">${last.close.toFixed(2)}</span>
          {change !== null && changePct !== null && (
            <span className={`font-mono text-lg font-semibold ${change >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(2)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
            </span>
          )}
          <span className="text-[#6e7681] text-sm">{data?.symbol} · {data?.frequency}</span>
        </div>
      )}

      {/* Chart */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-96">
            <Spinner size="lg" />
          </div>
        ) : error ? (
          <EmptyState title="加载失败" description={error.message} />
        ) : bars.length === 0 ? (
          <EmptyState title="请输入标的代码并点击查询" />
        ) : (
          <CandleChart bars={bars} height={420} />
        )}
      </div>

      {/* OHLCV */}
      {last && (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-4">
          {[
            { label: "开盘", value: last.open },
            { label: "最高", value: last.high },
            { label: "最低", value: last.low },
            { label: "收盘", value: last.close },
            { label: "成交量", value: last.volume, noPrefix: true },
            { label: "K线数", value: bars.length, noPrefix: true },
          ].map(({ label, value, noPrefix }) => (
            <div key={label} className="card py-2 px-3">
              <p className="text-xs text-[#6e7681]">{label}</p>
              <p className="font-mono text-sm text-[#e6edf3] mt-0.5">
                {noPrefix ? value.toLocaleString() : `$${value.toFixed(2)}`}
              </p>
            </div>
          ))}
        </div>
      )}
    </AppShell>
  )
}
