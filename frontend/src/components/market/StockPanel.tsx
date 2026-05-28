import { useState, useMemo } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { MarketStockItem } from "./MarketStockItem"
import { useSymbolSearch } from "@/hooks/useMarketData"
import { useSpotQuotes } from "@/hooks/useSpotQuotes"
import type { Market, MarketOverview, MarketOverviewItem } from "@/types"

// ── 市场 Tab 配置 ─────────────────────────────────────────────
type MarketTab = "A" | "HK" | "US"

const MARKET_TABS: { key: MarketTab; label: string }[] = [
  { key: "A",  label: "A股" },
  { key: "HK", label: "港股" },
  { key: "US", label: "美股" },
]

// ── 骨架屏 ────────────────────────────────────────────────────
function SkeletonList() {
  return (
    <div className="p-2 space-y-1">
      {Array.from({ length: 12 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between px-3 py-2">
          <div className="space-y-1.5">
            <div className="h-2.5 w-16 bg-[#21262d] rounded animate-pulse" />
            <div className="h-2 w-24 bg-[#21262d] rounded animate-pulse" />
          </div>
          <div className="space-y-1.5 text-right">
            <div className="h-2.5 w-12 bg-[#21262d] rounded animate-pulse" />
            <div className="h-2 w-10 bg-[#21262d] rounded animate-pulse" />
          </div>
        </div>
      ))}
    </div>
  )
}

// ── 涨跌统计 ─────────────────────────────────────────────────
interface MarketStatsProps {
  items: MarketOverviewItem[]
}

function MarketStats({ items }: MarketStatsProps) {
  const withPct = items.filter(i => i.change_pct != null)
  const upCount   = withPct.filter(i => (i.change_pct ?? 0) > 0).length
  const downCount = withPct.filter(i => (i.change_pct ?? 0) < 0).length
  const flatCount = withPct.filter(i => (i.change_pct ?? 0) === 0).length

  return (
    <div className="px-3 py-1.5 flex gap-3 text-xs text-[#8b949e] border-b border-[#21262d] bg-[#0d1117]/50">
      <span className="text-[#3fb950]">▲ {upCount}</span>
      <span className="text-[#f85149]">▼ {downCount}</span>
      <span className="text-[#6e7681]">— {flatCount}</span>
    </div>
  )
}

// ── 搜索结果行（来自后端 symbol search API）────────────────────
interface SearchResultRowProps {
  symbol: string
  market: string
  name: string
  nameZh: string | null
  isSelected: boolean
  onClick: () => void
}

function SearchResultRow({ symbol, market, name, nameZh, isSelected, onClick }: SearchResultRowProps) {
  return (
    <button
      className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors
        hover:bg-[#1c2128] ${isSelected ? "bg-[#1c2a3a]" : ""}`}
      onClick={onClick}
    >
      <div className="min-w-0">
        <p className="text-xs font-mono text-[#e6edf3] truncate">{nameZh ?? name}</p>
        <p className="text-[10px] text-[#6e7681] font-mono">{symbol}</p>
      </div>
      <span className="text-[10px] text-[#6e7681] shrink-0 ml-2">{market}</span>
    </button>
  )
}

// ── 主组件 Props ──────────────────────────────────────────────
interface StockPanelProps {
  overview: MarketOverview | undefined
  isLoading: boolean
  selectedSymbol: string
  selectedMarket: Market
  onSelect: (symbol: string, market: Market) => void
}

export function StockPanel({
  overview,
  isLoading,
  selectedSymbol,
  selectedMarket,
  onSelect,
}: StockPanelProps) {
  const [activeTab, setActiveTab] = useState<MarketTab>("US")
  const [searchQuery, setSearchQuery] = useState("")
  const { data: spotData, dataUpdatedAt } = useSpotQuotes()

  const rawItems: MarketOverviewItem[] = overview?.[activeTab] ?? []

  // 面板内过滤
  const panelFiltered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return rawItems
    return rawItems.filter(item =>
      item.symbol.toLowerCase().includes(q) ||
      (item.name_zh ?? "").includes(searchQuery) ||
      item.name.toLowerCase().includes(q)
    )
  }, [rawItems, searchQuery])

  // 当面板内无结果且有搜索词时，调用后端搜索
  const shouldCallBackend = searchQuery.trim().length >= 1 && panelFiltered.length === 0
  const { data: searchResults, isLoading: searching } = useSymbolSearch(
    searchQuery.trim(),
    shouldCallBackend ? activeTab : null,
  )

  // 是否显示后端搜索结果
  const showBackendResults = shouldCallBackend && !searching

  return (
    <div className="w-72 shrink-0 flex flex-col border-r border-[#21262d] overflow-hidden">
      {/* Tab 切换 */}
      <div className="flex border-b border-[#21262d]">
        {MARKET_TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => { setActiveTab(tab.key); setSearchQuery("") }}
            className={`flex-1 py-2 text-xs font-medium transition-colors
              ${activeTab === tab.key
                ? "text-[#58a6ff] border-b-2 border-[#58a6ff]"
                : "text-[#8b949e] hover:text-[#e6edf3]"}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 搜索框 */}
      <div className="p-2">
        <div className="relative">
          <input
            type="text"
            placeholder="搜索代码或中文名称…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="input w-full text-xs py-1.5 pr-8"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[#6e7681] hover:text-[#e6edf3] text-xs"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* 统计行（仅在数据加载完成且无搜索词时显示） */}
      {!isLoading && !searchQuery && overview && (
        <MarketStats items={rawItems} />
      )}

      {/* 股票列表 */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <SkeletonList />
        ) : searching ? (
          <div className="flex justify-center py-4">
            <Spinner size="sm" />
          </div>
        ) : showBackendResults ? (
          // 后端搜索结果
          <>
            {(searchResults ?? []).length === 0 ? (
              <p className="text-center text-xs text-[#6e7681] py-8">
                未找到 "{searchQuery}" 相关股票
              </p>
            ) : (
              <>
                <p className="text-[10px] text-[#6e7681] px-3 py-1.5 border-b border-[#21262d]">
                  全库搜索结果（{searchResults!.length} 条）
                </p>
                {searchResults!.map(r => (
                  <SearchResultRow
                    key={`${r.market}:${r.symbol}`}
                    symbol={r.symbol}
                    market={r.market}
                    name={r.name}
                    nameZh={r.name_zh}
                    isSelected={selectedSymbol === r.symbol && selectedMarket === r.market}
                    onClick={() => onSelect(r.symbol, r.market as Market)}
                  />
                ))}
              </>
            )}
          </>
        ) : panelFiltered.length === 0 ? (
          <p className="text-center text-xs text-[#6e7681] py-8">
            {searchQuery ? "无匹配结果" : "暂无数据"}
          </p>
        ) : (
          panelFiltered.map(item => (
            <MarketStockItem
              key={`${item.market}:${item.symbol}`}
              item={item}
              isSelected={selectedSymbol === item.symbol && selectedMarket === item.market}
              onClick={() => onSelect(item.symbol, item.market as Market)}
            />
          ))
        )}
      </div>

      {/* 底部数据源状态 */}
      <div className="px-3 py-2 border-t border-[#21262d]">
        {isLoading ? (
          <div className="flex items-center gap-1.5 text-[10px] text-[#6e7681]">
            <Spinner size="sm" />
            <span>加载中…</span>
          </div>
        ) : showBackendResults ? (
          <p className="text-[10px] text-[#6e7681]">搜索全库股票池</p>
        ) : (() => {
          const spotList = spotData?.[activeTab as keyof typeof spotData] ?? []
          const realtimeCount = spotList.filter(q => q.source === "realtime").length
          const delayedCount  = spotList.filter(q => q.source === "delayed").length
          const dailyCount    = spotList.filter(q => q.source === "daily").length
          const liveCount = realtimeCount + delayedCount + dailyCount
          const hasLive = liveCount > 0
          const updatedTime = dataUpdatedAt
            ? new Date(dataUpdatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : null
          const sourceLabel = realtimeCount > 0
            ? `实时行情 · ${realtimeCount} 支`
            : delayedCount > 0 && dailyCount > 0
              ? `延迟+日线 · ${liveCount} 支`
              : delayedCount > 0
                ? `延迟行情 · ${delayedCount} 支`
                : dailyCount > 0
                  ? `日线收盘 · ${dailyCount} 支`
                  : "演示数据"
          return (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full ${hasLive ? "bg-[#3fb950] animate-pulse" : "bg-[#6e7681]"}`} />
                <span className="text-[10px] text-[#6e7681]">{sourceLabel}</span>
              </div>
              {updatedTime && (
                <span className="text-[10px] text-[#3d444d]">{updatedTime}</span>
              )}
            </div>
          )
        })()}
      </div>
    </div>
  )
}
