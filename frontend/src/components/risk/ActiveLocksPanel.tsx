import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useActiveLocks, useClearLock } from "@/hooks/useProtections"
import { PROTECTION_LABELS } from "@/components/risk/ProtectionsCard"
import type { ActiveLock } from "@/types"

function formatUntil(iso: string): string {
  const until = new Date(iso).getTime()
  const mins = Math.max(0, Math.round((until - Date.now()) / 60000))
  if (mins <= 0) return "即将解除"
  if (mins < 60) return `剩 ${mins} 分钟`
  const h = Math.floor(mins / 60)
  return `剩 ${h} 小时 ${mins % 60} 分`
}

function LockRow({ lock, onClear, clearing }: { lock: ActiveLock; onClear: () => void; clearing: boolean }) {
  const scopeLabel = lock.scope === "global" ? "全局" : "标的"
  const target = lock.symbol ? `${lock.symbol}${lock.market ? ` · ${lock.market}` : ""}` : "全部标的"

  return (
    <div className="text-xs bg-[#2a1b1b] border border-[#f85149]/20 rounded px-3 py-2 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[#f85149] font-medium">
          {PROTECTION_LABELS[lock.protection_type] ?? lock.protection_type}
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-[#f85149]/30 text-[#f85149]">
          {scopeLabel}
        </span>
      </div>
      <p className="text-[#8b949e]">{target}</p>
      <p className="text-[#6e7681] leading-relaxed">{lock.reason}</p>
      <div className="flex items-center justify-between pt-1">
        <span className="text-[10px] text-[#6e7681]">{formatUntil(lock.until)}</span>
        <button
          className="px-2 py-0.5 rounded text-[10px] border border-[#6e7681]/40 text-[#8b949e] hover:bg-[#21262d] transition-colors"
          onClick={onClear}
          disabled={clearing}
        >
          {clearing ? <Spinner size="sm" /> : "解除"}
        </button>
      </div>
    </div>
  )
}

export function ActiveLocksPanel() {
  const { data, isLoading } = useActiveLocks()
  const { mutate: clearLock, isPending, variables } = useClearLock()
  const { toast } = useToast()

  const locks = data?.locks ?? []

  function handleClear(id: string) {
    clearLock(id, {
      onSuccess: () => toast("锁已解除", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  return (
    <div className="card border-[#f85149]/20">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
        当前锁定 {locks.length > 0 && <span className="text-[#f85149]">({locks.length})</span>}
      </h3>

      {isLoading && <div className="flex justify-center py-4"><Spinner /></div>}

      {!isLoading && locks.length === 0 && (
        <p className="text-[#3fb950] text-sm text-center py-2">✓ 无活跃锁定</p>
      )}

      <div className="space-y-2">
        {locks.map((lock) => (
          <LockRow
            key={lock.id}
            lock={lock}
            onClear={() => handleClear(lock.id)}
            clearing={isPending && variables === lock.id}
          />
        ))}
      </div>
    </div>
  )
}
