import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useFactorStrategies, useDeleteFactorStrategy,
  DEFAULT_SPEC, METHOD_LABELS,
  type FactorStrategySpec, type FactorStrategyRecord,
} from "@/hooks/useFactorStrategy"
import type { Market, Frequency } from "@/types"
import { FactorStrategyDialog } from "./FactorStrategyDialog"

interface FactorStrategyLabProps {
  market: Market
  freq: Frequency
}

/**
 * 因子策略列表 —— 已注册的每一条都带完整 spec，可直接编辑并重跑。
 *
 * 之所以列表里放的是 spec 而不是实验 id：实验记录器有滚动淘汰，
 * 只存引用会让策略在记录被挤掉后变成孤儿。
 */
export function FactorStrategyLab({ market, freq }: FactorStrategyLabProps) {
  const { data, isLoading, error } = useFactorStrategies()
  const [editing, setEditing] = useState<{ spec: FactorStrategySpec; name: string } | null>(null)

  const strategies = data?.strategies ?? []

  return (
    <div className="space-y-6">
      <div className="card flex flex-wrap items-center gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[#e6edf3]">因子策略</h3>
          <p className="text-[10px] text-[#6e7681] mt-0.5">
            公式因子 → 可交易组合策略。每条策略独立保存完整参数，实验记录被淘汰也不受影响。
          </p>
        </div>
        <button
          onClick={() => setEditing({ spec: { ...DEFAULT_SPEC }, name: "" })}
          className="ml-auto btn-primary text-xs px-4 py-2"
        >
          + 新建因子策略
        </button>
      </div>

      {isLoading && <div className="card flex items-center justify-center h-40"><Spinner /></div>}
      {error && <div className="card text-xs text-[#f85149]">{error.message}</div>}

      {data && strategies.length === 0 && (
        <div className="card flex items-center justify-center h-40 text-[#6e7681] text-sm text-center px-6">
          还没有因子策略。在「因子挖掘」或「实验记录」页签点「注册为策略」，或直接新建一条。
        </div>
      )}

      {strategies.length > 0 && (
        <div className="card">
          <StrategyTable
            records={strategies}
            onEdit={(r) => setEditing({ spec: r.spec, name: r.name })}
          />
        </div>
      )}

      <FactorStrategyDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        initialSpec={editing?.spec ?? DEFAULT_SPEC}
        defaultName={editing?.name}
        market={market}
        freq={freq}
        title={editing?.name ? `因子策略 · ${editing.name}` : "新建因子策略"}
      />
    </div>
  )
}

function formatTime(ts: number): string {
  if (!ts) return "—"
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  })
}

interface StrategyTableProps {
  records: FactorStrategyRecord[]
  onEdit: (record: FactorStrategyRecord) => void
}

function StrategyTable({ records, onEdit }: StrategyTableProps) {
  const { mutate: remove, isPending } = useDeleteFactorStrategy()
  const { toast } = useToast()

  function handleDelete(name: string) {
    remove(name, {
      onSuccess: () => toast(`已删除「${name}」`, "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#8b949e] border-b border-[#21262d]">
            <th className="text-left py-2 pr-3">名称</th>
            <th className="text-left py-2 pr-3">公式（RPN）</th>
            <th className="text-left py-2 pr-3">标的池</th>
            <th className="text-right py-2 pr-3">多/空</th>
            <th className="text-right py-2 pr-3">再平衡</th>
            <th className="text-left py-2 pr-3">权重方法</th>
            <th className="text-left py-2 pr-3">创建</th>
            <th className="text-right py-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr key={r.name} className="border-b border-[#21262d]/40 last:border-0 hover:bg-[#21262d]/30">
              <td className="py-1.5 pr-3 text-[#e6edf3]">
                <span className="block truncate max-w-[160px]" title={r.name}>{r.name}</span>
                {r.source_experiment_id && (
                  <span className="block text-[9px] text-[#6e7681]">来自实验 {r.source_experiment_id}</span>
                )}
              </td>
              <td className="py-1.5 pr-3 font-mono text-[#e6edf3] max-w-[200px] truncate" title={r.spec.formula}>
                {r.spec.formula}
              </td>
              <td className="py-1.5 pr-3 text-[#8b949e] max-w-[160px] truncate" title={r.spec.universe.join(", ")}>
                {r.spec.universe.length} 只
              </td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">
                {r.spec.long_quantile} / {r.spec.short_quantile ?? "—"}
              </td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{r.spec.rebalance_days}d</td>
              <td className="py-1.5 pr-3 text-[#8b949e]">
                {METHOD_LABELS[r.spec.portfolio_method] ?? r.spec.portfolio_method}
              </td>
              <td className="py-1.5 pr-3 text-[#6e7681] whitespace-nowrap">{formatTime(r.created_at)}</td>
              <td className="py-1.5 text-right whitespace-nowrap">
                <button
                  onClick={() => onEdit(r)}
                  className="text-[10px] px-2 py-1 rounded border border-[#30363d] text-[#58a6ff] hover:border-[#58a6ff]/40 transition-colors mr-1"
                >
                  编辑 / 重跑
                </button>
                <button
                  onClick={() => handleDelete(r.name)}
                  disabled={isPending}
                  className="text-[10px] px-2 py-1 rounded border border-[#30363d] text-[#8b949e] hover:text-[#f85149] hover:border-[#f85149]/40 transition-colors disabled:opacity-50"
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
