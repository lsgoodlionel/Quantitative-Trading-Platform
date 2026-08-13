// 自选行情 Tab：左侧自选池（localStorage 持久化 + 30 秒轮询价格），右侧 K 线。
// 内容由原 pages/Market.tsx 的 WatchRow / WatchlistTab 原样迁入（V3 · H1）。
import { useCallback, useState } from "react"
import { CandleChart } from "@/components/charts/CandleChart"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import { useBars, useWatchlistLatest } from "@/hooks/useMarketData"
import {
  loadWatchlistOrSeed,
  saveWatchlist,
  type WatchlistItem,
} from "@/lib/watchlist"
import type { Market, Frequency } from "@/types"
import {
  DEFAULT_WATCHLIST, FREQUENCY_LABELS, MARKET_CONFIGS,
  fmtChange, fmtPrice, sixMonthsAgo, today,
} from "./config"

// ── Watchlist 行（带实时价格轮询）─────────────────────────────
interface WatchItem {
  symbol: string
  market: Market
  name: string
}

const VALID_MARKETS: readonly Market[] = ["US", "HK", "A"]

/**
 * 持久化条目 -> 页面条目。
 *
 * `WatchlistItem.market` 是 string（localStorage 里的东西不可信：可能是旧版本
 * 写的、也可能被手工改过），这里**丢弃**无法识别的市场而不是硬转类型 ——
 * 一个 market 非法的条目会让行情请求整条链路报错。
 */
function toWatchItems(items: readonly WatchlistItem[]): WatchItem[] {
  return items
    .filter((item): item is WatchItem =>
      VALID_MARKETS.includes(item.market as Market),
    )
    .map(({ symbol, market, name }) => ({ symbol, market, name }))
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
interface WatchlistTabProps {
  initialSymbol: string
  initialMarket: Market
}

export function WatchlistTab({ initialSymbol, initialMarket }: WatchlistTabProps) {
  // 从**实际加载的**自选池里挑初始选中项。用户可能已经删掉了默认标的，
  // 仍从 DEFAULT_WATCHLIST 里挑会选中一个列表里根本不存在的条目。
  const [initialList] = useState<WatchItem[]>(() =>
    toWatchItems(loadWatchlistOrSeed(DEFAULT_WATCHLIST)),
  )
  const initialItem =
    initialList.find(w => w.symbol === initialSymbol && w.market === initialMarket)
    ?? initialList[0]
    ?? DEFAULT_WATCHLIST[0]

  // 自选池持久化：选股器「加自选池」写入的标的在这里读回来。
  // 首次进入用 DEFAULT_WATCHLIST 播种；用户清空后不再被播种回去（见 lib/watchlist.ts）。
  const [watchlist, setWatchlist] = useState<WatchItem[]>(initialList)
  const [selected, setSelected] = useState<WatchItem>(initialItem)
  const [addSymbol, setAddSymbol] = useState("")
  const [addMarket, setAddMarket] = useState<Market>("US")
  const [addName, setAddName] = useState("")
  const [showAdd, setShowAdd] = useState(false)
  const [chartQuery, setChartQuery] = useState<{
    symbol: string; market: Market; frequency: Frequency
    start_date: string; end_date: string
  }>({
    symbol: initialItem.symbol,
    market: initialItem.market,
    frequency: "1d",
    start_date: sixMonthsAgo(),
    end_date: today(),
  })

  const { data: prices } = useWatchlistLatest(
    watchlist.map(({ symbol, market }) => ({ symbol, market })),
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
    setWatchlist((prev) =>
      saveWatchlist(prev.filter((w) => w.symbol !== item.symbol || w.market !== item.market)),
    )
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
    setWatchlist((prev) => saveWatchlist([...prev, newItem]))
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

        <p className="text-[10px] text-[#6e7681] text-center">
          价格每 30 秒自动刷新
        </p>
      </div>

      {/* 右侧: K 线图 */}
      <div className="flex-1 flex flex-col min-w-0">
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

        {bars.length > 0 && (
          <div className="flex gap-4 mb-1 text-xs text-[#6e7681]">
            <span><span className="inline-block w-3 h-0.5 bg-[#f0a500] mr-1 align-middle" />MA5</span>
            <span><span className="inline-block w-3 h-0.5 bg-[#58a6ff] mr-1 align-middle" />MA20</span>
            <span><span className="inline-block w-3 h-0.5 bg-[#bc8cff] mr-1 align-middle" />MA60</span>
          </div>
        )}

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
