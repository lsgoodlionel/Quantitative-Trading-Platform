import { useState, useCallback } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { CandleChart } from "@/components/charts/CandleChart"
import { useBars, useWatchlistLatest } from "@/hooks/useMarketData"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import type { Market, Frequency } from "@/types"
import { format, subMonths, subYears } from "date-fns"

// ── 市场配置 ──────────────────────────────────────────────────
interface MarketConfig {
  value: Market
  label: string
  currency: string
  defaultSymbol: string
  defaultFreq: Frequency
  allowedFreqs: Frequency[]
}

const MARKET_CONFIGS: MarketConfig[] = [
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

const FREQUENCY_LABELS: Record<Frequency, string> = {
  "1m": "1分钟",
  "5m": "5分钟",
  "15m": "15分钟",
  "1h": "1小时",
  "1d": "日线",
  "1w": "周线",
}

// ── 默认自选列表 ──────────────────────────────────────────────
const DEFAULT_WATCHLIST: { symbol: string; market: Market; name: string }[] = [
  { symbol: "AAPL", market: "US", name: "苹果" },
  { symbol: "MSFT", market: "US", name: "微软" },
  { symbol: "NVDA", market: "US", name: "英伟达" },
  { symbol: "TSLA", market: "US", name: "特斯拉" },
  { symbol: "000001", market: "A", name: "平安银行" },
  { symbol: "600519", market: "A", name: "贵州茅台" },
  { symbol: "000858", market: "A", name: "五粮液" },
  { symbol: "00700", market: "HK", name: "腾讯" },
]

function today() { return format(new Date(), "yyyy-MM-dd") }
function sixMonthsAgo() { return format(subMonths(new Date(), 6), "yyyy-MM-dd") }
function oneYearAgo() { return format(subYears(new Date(), 1), "yyyy-MM-dd") }

// ── 价格格式化工具 ─────────────────────────────────────────────
function fmtPrice(v: number | undefined | null, currency: string): string {
  if (v == null) return "—"
  return `${currency}${v.toFixed(2)}`
}

function fmtChange(cur: number, prev: number) {
  const diff = cur - prev
  const pct = (diff / prev) * 100
  const sign = diff >= 0 ? "+" : ""
  return { diff, pct, label: `${sign}${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`, up: diff >= 0 }
}

// ── Tab: 行情查询 ─────────────────────────────────────────────
function QueryTab() {
  const [marketCfg, setMarketCfg] = useState<MarketConfig>(MARKET_CONFIGS[0])
  const [symbol, setSymbol] = useState(MARKET_CONFIGS[0].defaultSymbol)
  const [frequency, setFrequency] = useState<Frequency>(MARKET_CONFIGS[0].defaultFreq)
  const [startDate, setStartDate] = useState(sixMonthsAgo())
  const [endDate, setEndDate] = useState(today())
  const [query, setQuery] = useState<null | {
    symbol: string; market: Market; frequency: Frequency
    start_date: string; end_date: string
  }>(null)

  const { data, isLoading, error } = useBars(query)

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
        {/* 市场 */}
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

        {/* 代码 */}
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

        {/* 周期 */}
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

        {/* 快捷日期 */}
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

        {/* 日期范围 */}
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

// ── Watchlist 行（带实时价格轮询）─────────────────────────────
interface WatchItem {
  symbol: string
  market: Market
  name: string
}

interface WatchRowProps {
  item: WatchItem
  price: number | null | undefined
  isSelected: boolean
  onSelect: () => void
  onRemove: () => void
}

function WatchRow({ item, price, isSelected, onSelect, onRemove }: WatchRowProps) {
  const currency = MARKET_CONFIGS.find((c) => c.value === item.market)?.currency ?? "$"

  return (
    <button
      onClick={onSelect}
      className={`w-full flex items-center justify-between px-3 py-2.5 rounded-md text-sm transition-colors text-left ${
        isSelected ? "bg-[#1f6feb]/20 text-[#e6edf3]" : "hover:bg-[#21262d] text-[#8b949e]"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold text-xs truncate">{item.symbol}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
            item.market === "A" ? "border-[#d29922] text-[#d29922]" :
            item.market === "HK" ? "border-[#bc8cff] text-[#bc8cff]" :
            "border-[#58a6ff] text-[#58a6ff]"
          }`}>{item.market}</span>
        </div>
        <div className="text-[11px] text-[#6e7681] truncate">{item.name}</div>
      </div>
      <div className="flex items-center gap-2 shrink-0 ml-2">
        <span className="font-mono text-xs">
          {price != null ? `${currency}${price.toFixed(2)}` : "—"}
        </span>
        <span
          className="text-[#6e7681] hover:text-[#f85149] text-xs px-1"
          onClick={(e) => { e.stopPropagation(); onRemove() }}
          title="移除"
        >
          ×
        </span>
      </div>
    </button>
  )
}

// ── Tab: 自选行情 ─────────────────────────────────────────────
function WatchlistTab() {
  const [watchlist, setWatchlist] = useState<WatchItem[]>(DEFAULT_WATCHLIST)
  const [selected, setSelected] = useState<WatchItem>(DEFAULT_WATCHLIST[0])
  const [addSymbol, setAddSymbol] = useState("")
  const [addMarket, setAddMarket] = useState<Market>("US")
  const [addName, setAddName] = useState("")
  const [showAdd, setShowAdd] = useState(false)
  const [chartQuery, setChartQuery] = useState<{
    symbol: string; market: Market; frequency: Frequency
    start_date: string; end_date: string
  }>({
    symbol: DEFAULT_WATCHLIST[0].symbol,
    market: DEFAULT_WATCHLIST[0].market,
    frequency: "1d",
    start_date: sixMonthsAgo(),
    end_date: today(),
  })

  // 批量获取最新价格
  const { data: prices } = useWatchlistLatest(
    watchlist.map(({ symbol, market }) => ({ symbol, market }))
  )

  const handleSelect = useCallback((item: WatchItem) => {
    setSelected(item)
    setChartQuery({
      symbol: item.symbol,
      market: item.market,
      frequency: "1d",
      start_date: sixMonthsAgo(),
      end_date: today(),
    })
  }, [])

  const handleRemove = useCallback((item: WatchItem) => {
    setWatchlist((prev) => prev.filter((w) => w.symbol !== item.symbol || w.market !== item.market))
    if (selected.symbol === item.symbol && selected.market === item.market) {
      const remaining = watchlist.filter((w) => w.symbol !== item.symbol || w.market !== item.market)
      if (remaining.length > 0) handleSelect(remaining[0])
    }
  }, [selected, watchlist, handleSelect])

  function handleAdd() {
    const sym = addSymbol.toUpperCase().trim()
    if (!sym) return
    const exists = watchlist.some((w) => w.symbol === sym && w.market === addMarket)
    if (exists) return
    const newItem: WatchItem = { symbol: sym, market: addMarket, name: addName || sym }
    setWatchlist((prev) => [...prev, newItem])
    handleSelect(newItem)
    setAddSymbol("")
    setAddName("")
    setShowAdd(false)
  }

  const { data: chartData, isLoading: chartLoading } = useBars(chartQuery)
  const bars = chartData?.bars ?? []
  const last = bars.length > 0 ? bars[bars.length - 1] : null
  const prev = bars.length > 1 ? bars[bars.length - 2] : null
  const chg = last && prev ? fmtChange(last.close, prev.close) : null
  const selectedCfg = MARKET_CONFIGS.find((c) => c.value === selected.market) ?? MARKET_CONFIGS[0]

  return (
    <div className="flex gap-4 h-[calc(100vh-12rem)] min-h-0">
      {/* 左侧: 自选列表 */}
      <div className="w-52 lg:w-64 shrink-0 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-[#6e7681] uppercase tracking-wider">自选 ({watchlist.length})</span>
          <button
            className="text-xs text-[#58a6ff] hover:text-[#79c0ff] transition-colors"
            onClick={() => setShowAdd((v) => !v)}
          >
            {showAdd ? "取消" : "+ 添加"}
          </button>
        </div>

        {/* 添加自选表单 */}
        {showAdd && (
          <div className="card p-3 space-y-2">
            <select
              className="select w-full text-xs"
              value={addMarket}
              onChange={(e) => setAddMarket(e.target.value as Market)}
            >
              {MARKET_CONFIGS.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
            <input
              className="input w-full text-xs font-mono uppercase"
              placeholder="代码，如 600519"
              value={addSymbol}
              onChange={(e) => setAddSymbol(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <input
              className="input w-full text-xs"
              placeholder="名称（可选）"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <button className="btn btn-primary w-full text-xs py-1.5" onClick={handleAdd}>
              确认添加
            </button>
          </div>
        )}

        {/* 自选列表 */}
        <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
          {watchlist.map((item) => (
            <WatchRow
              key={`${item.market}:${item.symbol}`}
              item={item}
              price={prices?.[`${item.market}:${item.symbol}`]?.close}
              isSelected={selected.symbol === item.symbol && selected.market === item.market}
              onSelect={() => handleSelect(item)}
              onRemove={() => handleRemove(item)}
            />
          ))}
          {watchlist.length === 0 && (
            <p className="text-center text-xs text-[#6e7681] py-6">暂无自选，点击"+ 添加"</p>
          )}
        </div>

        {/* 刷新说明 */}
        <p className="text-[10px] text-[#6e7681] text-center">
          价格每 30 秒自动刷新
        </p>
      </div>

      {/* 右侧: K 线图 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 标题 + 价格 */}
        <div className="flex flex-wrap items-baseline gap-4 mb-2">
          <div>
            <span className="font-mono font-bold text-[#e6edf3]">{selected.symbol}</span>
            <span className="text-[#6e7681] ml-2 text-sm">{selected.name}</span>
            <span className={`ml-2 text-[11px] px-1.5 py-0.5 rounded border ${
              selected.market === "A" ? "border-[#d29922] text-[#d29922]" :
              selected.market === "HK" ? "border-[#bc8cff] text-[#bc8cff]" :
              "border-[#58a6ff] text-[#58a6ff]"
            }`}>{selected.market}</span>
          </div>
          {last && (
            <>
              <span className="font-mono text-2xl font-bold text-[#e6edf3]">
                {fmtPrice(last.close, selectedCfg.currency)}
              </span>
              {chg && (
                <span className={`font-mono font-semibold ${chg.up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                  {chg.label}
                </span>
              )}
            </>
          )}
          {/* 周期选择 */}
          <div className="ml-auto flex gap-1">
            {selectedCfg.allowedFreqs.filter((f) => ["1d", "1w"].includes(f)).map((f) => (
              <button
                key={f}
                className={`text-xs px-2 py-1 rounded transition-colors ${
                  chartQuery.frequency === f
                    ? "bg-[#58a6ff] text-[#0d1117]"
                    : "text-[#6e7681] hover:text-[#e6edf3] hover:bg-[#21262d]"
                }`}
                onClick={() => setChartQuery((q) => ({ ...q, frequency: f as Frequency }))}
              >
                {FREQUENCY_LABELS[f as Frequency]}
              </button>
            ))}
          </div>
        </div>

        {/* MA 图例 */}
        {bars.length > 0 && (
          <div className="flex gap-4 mb-1 text-xs text-[#6e7681]">
            <span><span className="inline-block w-3 h-0.5 bg-[#f0a500] mr-1 align-middle" />MA5</span>
            <span><span className="inline-block w-3 h-0.5 bg-[#58a6ff] mr-1 align-middle" />MA20</span>
            <span><span className="inline-block w-3 h-0.5 bg-[#bc8cff] mr-1 align-middle" />MA60</span>
          </div>
        )}

        {/* 图表 */}
        <div className="card p-0 overflow-hidden flex-1 min-h-0">
          {chartLoading ? (
            <div className="flex items-center justify-center h-full">
              <Spinner size="lg" />
            </div>
          ) : bars.length === 0 ? (
            <EmptyState title="暂无数据" description="数据加载中或代码不存在" />
          ) : (
            <CandleChart bars={bars} height={380} showMA showVolume />
          )}
        </div>

        {/* OHLCV 快览 */}
        {last && (
          <div className="flex flex-wrap gap-3 mt-3">
            {[
              { label: "开", v: fmtPrice(last.open, selectedCfg.currency) },
              { label: "高", v: fmtPrice(last.high, selectedCfg.currency) },
              { label: "低", v: fmtPrice(last.low, selectedCfg.currency) },
              { label: "收", v: fmtPrice(last.close, selectedCfg.currency) },
              { label: "量", v: last.volume.toLocaleString() },
            ].map(({ label, v }) => (
              <span key={label} className="text-xs text-[#6e7681]">
                {label}&nbsp;<span className="font-mono text-[#e6edf3]">{v}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────
type Tab = "query" | "watchlist"

export function Market() {
  const [tab, setTab] = useState<Tab>("query")

  return (
    <AppShell title="行情">
      {/* Tab 切换 */}
      <div className="flex gap-1 mb-5 border-b border-[#21262d]">
        {[
          { key: "query" as Tab, label: "📊 行情查询" },
          { key: "watchlist" as Tab, label: "⭐ 自选行情" },
        ].map(({ key, label }) => (
          <button
            key={key}
            className={`px-4 py-2 text-sm border-b-2 transition-colors -mb-px ${
              tab === key
                ? "border-[#58a6ff] text-[#58a6ff]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "query" ? <QueryTab /> : <WatchlistTab />}
    </AppShell>
  )
}
