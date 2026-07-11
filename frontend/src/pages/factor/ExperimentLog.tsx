import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useExperiments, useDeleteExperiment,
  type ExperimentKind, type ExperimentRecord,
} from "@/hooks/useFactorMining"
import { fmt } from "./universeConfig"

// ── 类型标签映射 ──────────────────────────────────────────────────
const KIND_META: Record<ExperimentKind, { label: string; color: string }> = {
  genetic_mining: { label: "遗传挖掘", color: "#3fb950" },
  factor_library: { label: "因子库", color: "#e3b341" },
  formula_factor: { label: "公式因子", color: "#58a6ff" },
  factor_analysis: { label: "因子分析", color: "#bc8cff" },
}

const KIND_FILTERS: { key: ExperimentKind | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "genetic_mining", label: "遗传挖掘" },
  { key: "formula_factor", label: "公式因子" },
  { key: "factor_library", label: "因子库" },
  { key: "factor_analysis", label: "因子分析" },
]

const SORT_OPTIONS: { key: "score" | "time"; label: string; hint: string }[] = [
  { key: "score", label: "按评分", hint: "适应度 / RankIC 综合排序" },
  { key: "time", label: "按时间", hint: "最新记录在前" },
]

function metricColor(v: number | null, threshold = 0.05): string {
  if (v == null || isNaN(v)) return "#8b949e"
  return Math.abs(v) >= threshold ? (v > 0 ? "#3fb950" : "#f85149") : "#e6edf3"
}

function formatTime(ts: number): string {
  if (!ts) return "—"
  const d = new Date(ts * 1000)
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

export function ExperimentLog() {
  const [kind, setKind] = useState<ExperimentKind | "all">("all")
  const [sortBy, setSortBy] = useState<"score" | "time">("score")

  const { data, isLoading, error } = useExperiments({
    sortBy,
    kind: kind === "all" ? undefined : kind,
    limit: 100,
  })

  return (
    <div className="space-y-6">
      {/* 工具栏 */}
      <div className="card flex flex-wrap items-center gap-4">
        <div>
          <p className="text-[10px] text-[#6e7681] mb-1">类型筛选</p>
          <div className="flex flex-wrap gap-1">
            {KIND_FILTERS.map((f) => (
              <button key={f.key} onClick={() => setKind(f.key)}
                className={`px-2.5 py-1 rounded text-[11px] transition-colors border ${
                  kind === f.key
                    ? "bg-[#58a6ff]/20 text-[#58a6ff] border-[#58a6ff]/40"
                    : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>{f.label}</button>
            ))}
          </div>
        </div>
        <div className="ml-auto">
          <p className="text-[10px] text-[#6e7681] mb-1">排序</p>
          <div className="flex gap-1">
            {SORT_OPTIONS.map((s) => (
              <button key={s.key} onClick={() => setSortBy(s.key)} title={s.hint}
                className={`px-2.5 py-1 rounded text-[11px] transition-colors border ${
                  sortBy === s.key
                    ? "bg-[#3fb950]/20 text-[#3fb950] border-[#3fb950]/40"
                    : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>{s.label}</button>
            ))}
          </div>
        </div>
      </div>

      {isLoading && <div className="card flex items-center justify-center h-48"><Spinner /></div>}
      {error && <div className="card text-xs text-[#f85149]">{error.message}</div>}

      {data && data.records.length === 0 && (
        <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm text-center px-6">
          暂无实验记录。在「因子挖掘」页签运行遗传挖掘，或在候选榜点击「记录」即可收藏到此排行榜。
        </div>
      )}

      {data && data.records.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-[#e6edf3]">实验排行榜</h3>
            <span className="text-[10px] text-[#6e7681]">共 {data.count} 条</span>
          </div>
          <ExperimentTable records={data.records} rankByScore={sortBy === "score"} />
        </div>
      )}
    </div>
  )
}

function ExperimentTable({ records, rankByScore }: { records: ExperimentRecord[]; rankByScore: boolean }) {
  const { mutate: remove, isPending } = useDeleteExperiment()
  const { toast } = useToast()

  function handleDelete(id: string) {
    remove(id, {
      onSuccess: () => toast("已删除", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#8b949e] border-b border-[#21262d]">
            {rankByScore && <th className="text-left py-2 pr-3">#</th>}
            <th className="text-left py-2 pr-3">类型</th>
            <th className="text-left py-2 pr-3">名称 / 公式</th>
            <th className="text-left py-2 pr-3">市场</th>
            <th className="text-right py-2 pr-3">适应度</th>
            <th className="text-right py-2 pr-3">RankIC</th>
            <th className="text-right py-2 pr-3">ICIR</th>
            <th className="text-left py-2 pr-3">时间</th>
            <th className="text-right py-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r, i) => {
            const km = KIND_META[r.kind] ?? { label: r.kind, color: "#8b949e" }
            return (
              <tr key={r.id} className="border-b border-[#21262d]/40 last:border-0 hover:bg-[#21262d]/30">
                {rankByScore && <td className="py-1.5 pr-3 text-[#6e7681] font-mono">{i + 1}</td>}
                <td className="py-1.5 pr-3">
                  <span className="badge text-[10px]" style={{ color: km.color, borderColor: `${km.color}55` }}>
                    {km.label}
                  </span>
                </td>
                <td className="py-1.5 pr-3 max-w-[240px]">
                  <span className="block font-mono text-[#e6edf3] truncate" title={r.name}>{r.name}</span>
                  {r.symbols.length > 0 && (
                    <span className="block text-[9px] text-[#6e7681] truncate">{r.symbols.join(", ")}</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-[#8b949e]">{r.market}</td>
                <td className="py-1.5 pr-3 text-right font-mono" style={{ color: metricColor(r.metrics.fitness, 0.001) }}>
                  {fmt(r.metrics.fitness, 3)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono" style={{ color: metricColor(r.metrics.rank_ic_mean) }}>
                  {fmt(r.metrics.rank_ic_mean)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{fmt(r.metrics.icir, 3)}</td>
                <td className="py-1.5 pr-3 text-[#6e7681] whitespace-nowrap">{formatTime(r.created_at)}</td>
                <td className="py-1.5 text-right">
                  <button onClick={() => handleDelete(r.id)} disabled={isPending}
                    className="text-[10px] px-2 py-1 rounded border border-[#30363d] text-[#8b949e] hover:text-[#f85149] hover:border-[#f85149]/40 transition-colors">
                    删除
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
