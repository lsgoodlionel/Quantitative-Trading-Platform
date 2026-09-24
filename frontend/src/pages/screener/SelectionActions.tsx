import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useToast } from "@/components/ui/Toast"
import { Modal } from "@/components/ui/Modal"
import { addToWatchlist } from "@/lib/watchlist"
import { BatchBacktestPanel } from "@/pages/screener/BatchBacktestPanel"
import { MAX_BATCH_SYMBOLS } from "@/hooks/useBatchBacktest"
import type { ScreenerCandidate } from "@/hooks/useScreener"
import type { Market } from "@/types"

/** 组合优化至少需要 2 个标的（与后端 symbols min_length=2 一致） */
const MIN_OPTIMIZE_SYMBOLS = 2
/** 组合优化上限（与后端 symbols max_length=20 一致） */
const MAX_OPTIMIZE_SYMBOLS = 20

interface SelectionActionsProps {
  selected: ScreenerCandidate[]
  market: Market
  onClear: () => void
}

/**
 * 多选后浮出的动作条（V3 G3）：送组合优化 / 加自选池 / 批量回测。
 *
 * 「送组合优化」走 URL 参数（`?symbols=A,B,C&market=US`），符合项目
 * 「URL 即状态」惯例 —— 链接可分享、可收藏、刷新不丢。
 */
export function SelectionActions({ selected, market, onClear }: SelectionActionsProps) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [showBatch, setShowBatch] = useState(false)

  const symbols = selected.map((c) => c.symbol)
  if (symbols.length === 0) return null

  const goOptimizer = () => {
    if (symbols.length < MIN_OPTIMIZE_SYMBOLS) {
      toast(`组合优化至少需要 ${MIN_OPTIMIZE_SYMBOLS} 个标的`, "warning")
      return
    }
    const picked = symbols.slice(0, MAX_OPTIMIZE_SYMBOLS)
    if (symbols.length > MAX_OPTIMIZE_SYMBOLS) {
      toast(`组合优化最多 ${MAX_OPTIMIZE_SYMBOLS} 个标的，已取前 ${MAX_OPTIMIZE_SYMBOLS} 个`, "warning")
    }
    const query = new URLSearchParams({ symbols: picked.join(","), market })
    navigate(`/portfolio?tab=optimizer&${query.toString()}`)
  }

  const addWatchlist = () => {
    const { added } = addToWatchlist(
      selected.map((c) => ({ symbol: c.symbol, market: c.market, name: c.name })),
    )
    const dup = symbols.length - added
    toast(
      dup > 0 ? `已加入自选池 ${added} 只（${dup} 只已存在）` : `已加入自选池 ${added} 只`,
      "success",
    )
  }

  const openBatch = () => {
    if (symbols.length > MAX_BATCH_SYMBOLS) {
      toast(`批量回测最多 ${MAX_BATCH_SYMBOLS} 个标的，当前已选 ${symbols.length} 个`, "warning")
      return
    }
    setShowBatch(true)
  }

  const btn =
    "px-3 py-1.5 rounded-md text-sm border transition-colors border-[#30363d] " +
    "text-[#e6edf3] hover:border-[#58a6ff] hover:text-[#58a6ff]"

  return (
    <>
      <div
        data-testid="selection-actions"
        className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-[#1f6feb]/40 bg-[#1f6feb]/[0.08] px-3 py-2"
      >
        <span className="text-sm text-[#58a6ff]">已选 {symbols.length} 只</span>
        <span className="mx-1 h-4 w-px bg-[#30363d]" />
        <button onClick={goOptimizer} className={btn}>🎯 送组合优化</button>
        <button onClick={addWatchlist} className={btn}>⭐ 加自选池</button>
        <button onClick={openBatch} className={btn}>🔬 批量回测</button>
        <button
          onClick={onClear}
          className="ml-auto text-xs text-[#8b949e] transition-colors hover:text-[#e6edf3]"
        >
          清空选择
        </button>
      </div>

      <Modal
        open={showBatch}
        onClose={() => setShowBatch(false)}
        title={`批量回测 · ${symbols.length} 只标的`}
        className="max-w-3xl"
      >
        <BatchBacktestPanel symbols={symbols} market={market} />
      </Modal>
    </>
  )
}
