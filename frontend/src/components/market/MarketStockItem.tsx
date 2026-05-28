import type { MarketOverviewItem } from "@/types"

interface MarketStockItemProps {
  item: MarketOverviewItem
  isSelected: boolean
  onClick: () => void
}

export function MarketStockItem({ item, isSelected, onClick }: MarketStockItemProps) {
  const isUp = (item.change_pct ?? 0) >= 0
  const priceColor = isUp ? "text-[#3fb950]" : "text-[#f85149]"

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center justify-between px-3 py-2 text-sm transition-colors
        ${isSelected
          ? "bg-[#1f6feb]/30 border-l-2 border-[#58a6ff]"
          : "hover:bg-[#21262d] border-l-2 border-transparent"}`}
    >
      <div className="text-left min-w-0">
        <div className="font-mono text-xs text-[#8b949e]">{item.symbol}</div>
        <div className="text-[#e6edf3] text-xs truncate">{item.name_zh ?? item.name}</div>
      </div>
      <div className="text-right shrink-0 ml-2">
        {item.price != null
          ? (
            <div className={`font-mono text-xs font-semibold ${priceColor}`}>
              {item.price.toFixed(2)}
            </div>
          )
          : <div className="text-[#6e7681] text-xs">—</div>
        }
        {item.change_pct != null
          ? (
            <div className={`text-[10px] ${priceColor}`}>
              {isUp ? "+" : ""}{item.change_pct.toFixed(2)}%
            </div>
          )
          : null
        }
      </div>
    </button>
  )
}
