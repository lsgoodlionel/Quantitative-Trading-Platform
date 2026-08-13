import { useState } from "react"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import {
  useBacktestHistoryDetail,
  useBacktestHistoryList,
  useDeleteBacktestHistory,
  useRerunBacktestHistory,
  type HistorySummary,
} from "@/hooks/useBacktestHistory"
import { HistoryCompareView } from "./HistoryCompareView"

// ── Tab: 回测历史（V3 · H5）───────────────────────────────────
// 列表 → 详情 → 重跑 / 对比。存储落 TimescaleDB，无容量上限、不自动淘汰。

const PAGE_SIZE = 20
const MAX_COMPARE = 8

type HistoryView = "detail" | "compare"

export function HistoryTab() {
  const [offset, setOffset] = useState(0)
  const [symbolFilter, setSymbolFilter] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [view, setView] = useState<HistoryView>("detail")

  const { data, isPending, error } = useBacktestHistoryList({
    limit: PAGE_SIZE,
    offset,
    symbol: symbolFilter || undefined,
  })
  const { mutate: remove } = useDeleteBacktestHistory()

  function toggleCompare(id: string) {
    setCompareIds((prev) =>
      prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= MAX_COMPARE ? prev : [...prev, id],
    )
  }

  function handleDelete(id: string) {
    remove(id)
    setCompareIds((prev) => prev.filter((x) => x !== id))
    setSelectedId((prev) => (prev === id ? null : prev))
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <div className="xl:col-span-1 space-y-3">
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[#e6edf3]">回测历史</h2>
            <span className="text-[10px] text-[#6e7681] font-mono">{total} 条</span>
          </div>
          <p className="text-[11px] text-[#6e7681] leading-relaxed">
            历史落库保存，不会随刷新丢失，也没有条数上限；仅支持手动删除。
          </p>
          <input className="input w-full font-mono uppercase" placeholder="按标的筛选，如 AAPL"
            aria-label="按标的筛选"
            value={symbolFilter}
            onChange={(e) => { setSymbolFilter(e.target.value.toUpperCase()); setOffset(0) }} />
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        {isPending && (
          <div className="card flex justify-center py-8"><Spinner size="lg" /></div>
        )}

        {!isPending && items.length === 0 && (
          <div className="card">
            <EmptyState title="还没有保存的回测"
              description="在「策略回测」Tab 跑完一次后点击「💾 保存结果」" />
          </div>
        )}

        {items.map((item) => (
          <HistoryRow key={item.id} item={item}
            isSelected={selectedId === item.id}
            isCompared={compareIds.includes(item.id)}
            onSelect={() => { setSelectedId(item.id); setView("detail") }}
            onToggleCompare={() => toggleCompare(item.id)}
            onDelete={() => handleDelete(item.id)} />
        ))}

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between text-xs">
            <button type="button" className="btn btn-ghost text-xs" disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>← 上一页</button>
            <span className="text-[#6e7681] font-mono">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {total}
            </span>
            <button type="button" className="btn btn-ghost text-xs" disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}>下一页 →</button>
          </div>
        )}
      </div>

      <div className="xl:col-span-2 space-y-4">
        <div className="flex gap-1 border-b border-[#21262d]">
          {([["detail", "详情 / 重跑"], ["compare", `对比 (${compareIds.length})`]] as const).map(([key, label]) => (
            <button key={key} type="button"
              className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
                view === key ? "border-[#58a6ff] text-[#58a6ff]" : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
              }`}
              onClick={() => setView(key)}>
              {label}
            </button>
          ))}
        </div>

        {view === "detail" ? <DetailPane id={selectedId} /> : <HistoryCompareView ids={compareIds} />}
      </div>
    </div>
  )
}

interface HistoryRowProps {
  item: HistorySummary
  isSelected: boolean
  isCompared: boolean
  onSelect: () => void
  onToggleCompare: () => void
  onDelete: () => void
}

function HistoryRow({ item, isSelected, isCompared, onSelect, onToggleCompare, onDelete }: HistoryRowProps) {
  const ret = item.metrics?.total_return_pct
  return (
    <div className={`card py-3 space-y-2 cursor-pointer transition-colors ${
      isSelected ? "border-[#58a6ff]/50" : "hover:border-[#30363d]"
    }`} onClick={onSelect}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-xs text-[#e6edf3] truncate">{item.name}</div>
          <div className="text-[10px] text-[#6e7681] font-mono mt-0.5">
            {item.start_date} ~ {item.end_date} · {item.frequency}
          </div>
        </div>
        {typeof ret === "number" && (
          <span className={`text-xs font-mono ${ret >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {ret >= 0 ? "+" : ""}{ret.toFixed(2)}%
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-[10px] text-[#8b949e] cursor-pointer"
          onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={isCompared} onChange={onToggleCompare}
            aria-label={`对比 ${item.name}`} />
          对比
        </label>
        {item.curve_downsampled && (
          <span className="text-[10px] text-[#6e7681]" title="净值曲线已降采样存储">
            曲线 {item.curve_points} 点（已降采样）
          </span>
        )}
        <button type="button" className="btn btn-ghost text-[10px] ml-auto text-[#f85149]"
          onClick={(e) => { e.stopPropagation(); onDelete() }}>删除</button>
      </div>
    </div>
  )
}

function DetailPane({ id }: { id: string | null }) {
  const { data, isPending } = useBacktestHistoryDetail(id)
  const { mutate: rerun, data: rerunResult, isPending: isRerunning, error: rerunError } = useRerunBacktestHistory()

  if (!id) {
    return (
      <div className="card">
        <EmptyState title="选择左侧一条历史查看详情" description="可用原配置重跑，或勾选多条进行对比" />
      </div>
    )
  }
  if (isPending || !data) {
    return <div className="card flex justify-center py-10"><Spinner size="lg" /></div>
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <h3 className="text-sm font-semibold text-[#e6edf3]">{data.name}</h3>
            <p className="text-[10px] text-[#6e7681] font-mono mt-1">
              保存于 {data.created_at.slice(0, 19).replace("T", " ")}
            </p>
          </div>
          <button type="button" className="btn btn-primary text-xs whitespace-nowrap"
            disabled={isRerunning} onClick={() => rerun(data.id)}>
            {isRerunning ? <Spinner size="sm" /> : "↻ 用原配置重跑"}
          </button>
        </div>
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <Field label="策略" value={data.strategy_name} />
          <Field label="标的" value={`${data.symbol} · ${data.market}`} />
          <Field label="初始资金" value={data.initial_cash.toLocaleString()} />
          <Field label="期末净值" value={data.final_value.toLocaleString()} />
        </dl>
        <div className="mt-3">
          <span className="label">策略参数</span>
          <pre className="text-[10px] text-[#8b949e] font-mono mt-1 overflow-x-auto">
            {JSON.stringify(data.params, null, 1)}
          </pre>
        </div>
      </div>

      {rerunError && (
        <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          {rerunError.message}
        </p>
      )}

      {rerunResult && (
        <div className={`card border ${rerunResult.metrics_changed ? "border-[#d29922]/40" : "border-[#3fb950]/40"}`}>
          <h4 className="text-sm font-semibold text-[#e6edf3] mb-2">重跑结果</h4>
          <p className="text-[11px] leading-relaxed">
            {rerunResult.metrics_changed
              ? <span className="text-[#d29922]">⚠️ 指标与保存时不一致 —— 底层行情或复权数据可能已变化。</span>
              : <span className="text-[#3fb950]">✅ 指标与保存时逐项一致，配置完整复现。</span>}
          </p>
          <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mt-3">
            <Field label="期末净值" value={rerunResult.final_value.toLocaleString()} />
            <Field label="总收益%" value={fmt(rerunResult.metrics.total_return_pct)} />
            <Field label="夏普" value={fmt(rerunResult.metrics.sharpe_ratio)} />
            <Field label="最大回撤%" value={fmt(rerunResult.metrics.max_drawdown_pct)} />
          </dl>
        </div>
      )}

      <div className="card">
        <h4 className="text-sm font-semibold text-[#e6edf3] mb-3">保存时的指标</h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(data.metrics).slice(0, 12).map(([key, value]) => (
            <Field key={key} label={key} value={fmt(value)} />
          ))}
        </div>
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded px-3 py-2">
      <dt className="text-[10px] text-[#6e7681]">{label}</dt>
      <dd className="font-mono text-xs text-[#e6edf3] mt-0.5 truncate">{value}</dd>
    </div>
  )
}

function fmt(value: number | undefined): string {
  if (typeof value !== "number") return "—"
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}
