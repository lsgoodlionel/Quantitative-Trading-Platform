// 行情页（V3 · H1）：原「行情」+「事件期权」两页合并为三个 Tab。
// Tab 状态进 URL（?tab=），标的选择继续走 ?symbol=/&market=。
import { useCallback, useMemo, useState } from "react"
import { useLocation } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { StockPanel } from "@/components/market/StockPanel"
import { PAGE_HELP } from "@/data/pageHelp"
import { useUrlTab } from "@/hooks/useUrlTab"
import { useMarketOverview } from "@/hooks/useMarketData"
import { useSpotQuotes } from "@/hooks/useSpotQuotes"
import type { Market } from "@/types"
import { QuoteTab } from "./QuoteTab"
import { WatchlistTab } from "./WatchlistTab"
import { EVENTS_HELP, EventsTab } from "./events/EventsTab"
import { readSymbolSelection } from "./symbolParam"

const TABS = ["quote", "watchlist", "events"] as const
type MarketTab = (typeof TABS)[number]

const TAB_LABELS: { key: MarketTab; label: string }[] = [
  { key: "quote", label: "📊 行情查询" },
  { key: "watchlist", label: "⭐ 自选行情" },
  { key: "events", label: "🗓️ 事件期权" },
]

export function MarketPage() {
  const { search } = useLocation()
  const [tab, setTab] = useUrlTab<MarketTab>(TABS, "quote")

  // 左栏选中的标的（跨 Tab 共享）。惰性初始化：只读一次 URL，
  // 之后完全由用户在左栏的点选驱动，避免切 Tab 把选择弹回链接里的标的。
  const [initial] = useState(() => readSymbolSelection(search))
  const [panelSymbol, setPanelSymbol] = useState(initial.symbol)
  const [panelMarket, setPanelMarket] = useState<Market>(initial.market)

  const { data: overview, isLoading: overviewLoading } = useMarketOverview()
  const { data: spotData } = useSpotQuotes()

  // Merge real-time spot prices over the market overview base data
  const mergedOverview = useMemo(() => {
    if (!overview) return overview
    type OvItem = (typeof overview.A)[number]
    type SpotItem = NonNullable<typeof spotData>["A"][number]
    const mergeMarket = (items: OvItem[], spotList: SpotItem[] | undefined) => {
      if (!spotList) return items
      const spotMap = new Map(spotList.map(q => [q.symbol, q]))
      return items.map(item => {
        const spot = spotMap.get(item.symbol)
        if (!spot || spot.source === "demo" || spot.price == null) return item
        return {
          ...item,
          price: spot.price,
          prev_close: spot.prev_close ?? item.prev_close,
          change_pct: spot.change_pct ?? item.change_pct,
        }
      })
    }
    return {
      A:  mergeMarket(overview.A,  spotData?.A),
      HK: mergeMarket(overview.HK, spotData?.HK),
      US: mergeMarket(overview.US, spotData?.US),
    }
  }, [overview, spotData])

  const handlePanelSelect = useCallback((symbol: string, market: Market) => {
    setPanelSymbol(symbol)
    setPanelMarket(market)
  }, [])

  // 事件期权原本是独立页，有自己的帮助文案：选中该 Tab 时顶栏换成它
  const help = tab === "events" ? EVENTS_HELP : PAGE_HELP.market
  const tabKey = `${panelMarket}:${panelSymbol}`

  return (
    <AppShell title="行情" help={help}>
      <div className="flex h-full -m-4 lg:-m-6 overflow-hidden">
        {/* 左栏：市场股票列表 */}
        <StockPanel
          overview={mergedOverview}
          isLoading={overviewLoading}
          selectedSymbol={panelSymbol}
          selectedMarket={panelMarket}
          onSelect={handlePanelSelect}
        />

        {/* 右栏：图表区域 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Tab 切换 */}
          <div className="flex gap-1 px-4 lg:px-6 pt-4 lg:pt-6 border-b border-[#21262d]">
            {TAB_LABELS.map(({ key, label }) => (
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

          {/* Tab 内容 */}
          <div className="flex-1 overflow-auto p-4 lg:p-6">
            {tab === "quote" && (
              <QuoteTab key={tabKey} initialSymbol={panelSymbol} initialMarket={panelMarket} />
            )}
            {tab === "watchlist" && (
              <WatchlistTab key={tabKey} initialSymbol={panelSymbol} initialMarket={panelMarket} />
            )}
            {tab === "events" && (
              <EventsTab key={tabKey} initialSymbol={panelSymbol} initialMarket={panelMarket} />
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
