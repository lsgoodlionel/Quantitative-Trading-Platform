import { useEffect, useMemo, useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import {
  useSourcesStatus, useSourcesConfig, useUpdateSourcesConfig,
  type MarketKey, type MarketConfig, type SourceStatus,
} from "@/hooks/useDataConfig"
import { usePermissions } from "@/hooks/useRbac"

const MARKETS: { key: MarketKey; label: string; flag: string }[] = [
  { key: "US", label: "美股", flag: "🇺🇸" },
  { key: "HK", label: "港股", flag: "🇭🇰" },
  { key: "A",  label: "A股",  flag: "🇨🇳" },
]

const AUTO_REFRESH_MS = 20_000

// ── 单个数据源行 ───────────────────────────────────────────────
interface SourceRowProps {
  status?: SourceStatus
  metaName: string
  metaNote: string
  metaRealtime: boolean
  index: number
  total: number
  isActive: boolean
  order: string[]
  disabled: string[]
  pinned: string | null
  sourceId: string
  canEdit: boolean
  onChange: (next: Partial<MarketConfig>) => void
}

function SourceRow({
  status, metaName, metaNote, metaRealtime, index, total, isActive,
  order, disabled, pinned, sourceId, canEdit, onChange,
}: SourceRowProps) {
  const isDisabled = disabled.includes(sourceId)
  const isPinned = pinned === sourceId
  const ok = status?.ok ?? false
  const latency = status?.latency_ms

  function move(dir: -1 | 1) {
    const i = order.indexOf(sourceId)
    const j = i + dir
    if (i < 0 || j < 0 || j >= order.length) return
    const next = [...order]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange({ order: next })
  }
  function toggleEnabled() {
    onChange({ disabled: isDisabled ? disabled.filter((x) => x !== sourceId) : [...disabled, sourceId] })
  }
  function togglePin() {
    onChange({ pinned: isPinned ? null : sourceId })
  }

  return (
    <div className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border ${
      isPinned ? "border-[#e3b341]/40 bg-[#272111]/40"
      : isDisabled ? "border-[#21262d] bg-[#0d1117]/40 opacity-55"
      : isActive ? "border-[#3fb950]/30 bg-[#0d1a12]"
      : "border-[#21262d] bg-[#0d1117]/70"
    }`}>
      {/* 状态点 */}
      <span className={`w-2 h-2 rounded-full shrink-0 ${
        !status ? "bg-[#30363d] animate-pulse" : ok ? "bg-[#3fb950]" : "bg-[#f85149]"
      }`} />

      {/* 名称 + 标签 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-medium text-[#e6edf3]">{metaName}</span>
          {metaRealtime && (
            <span className="text-[9px] px-1 py-0.5 rounded border border-[#3fb950]/30 text-[#3fb950]">实时</span>
          )}
          {isActive && !isPinned && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-[#1a2a1a] text-[#3fb950]">当前生效</span>
          )}
          {isPinned && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-[#272111] text-[#e3b341]">★ 强制</span>
          )}
        </div>
        <p className="text-[10px] text-[#6e7681] truncate">
          {status?.error
            ? <span className="text-[#f85149]">{status.error}</span>
            : <>{metaNote}{latency != null && ok ? ` · ${latency}ms` : ""}</>}
        </p>
      </div>

      {/* 操作 */}
      {canEdit && (
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => move(-1)} disabled={index === 0}
            title="上移优先级" className="w-5 h-5 rounded text-[10px] text-[#6e7681] hover:text-[#e6edf3] hover:bg-[#21262d] disabled:opacity-30 transition-colors">▲</button>
          <button onClick={() => move(1)} disabled={index === total - 1}
            title="下移优先级" className="w-5 h-5 rounded text-[10px] text-[#6e7681] hover:text-[#e6edf3] hover:bg-[#21262d] disabled:opacity-30 transition-colors">▼</button>
          <button onClick={togglePin}
            title={isPinned ? "取消强制" : "强制只用此源"}
            className={`w-5 h-5 rounded text-[10px] transition-colors ${
              isPinned ? "text-[#e3b341]" : "text-[#6e7681] hover:text-[#e3b341] hover:bg-[#21262d]"
            }`}>★</button>
          <button onClick={toggleEnabled}
            title={isDisabled ? "启用" : "禁用"}
            className={`px-1.5 h-5 rounded text-[9px] font-medium transition-colors ${
              isDisabled ? "text-[#6e7681] border border-[#30363d]" : "text-[#3fb950] border border-[#3fb950]/30"
            }`}>{isDisabled ? "已禁用" : "启用"}</button>
        </div>
      )}
    </div>
  )
}

// ── 单市场卡片 ─────────────────────────────────────────────────
function MarketSourceCard({ market, flag, label }: { market: MarketKey; flag: string; label: string }) {
  const { data: cfgData } = useSourcesConfig()
  const { data: statusData, isFetching } = useSourcesStatus(false)
  const { mutate: update, isPending } = useUpdateSourcesConfig()
  const { canTrade } = usePermissions()

  const catalog = cfgData?.catalog[market] ?? []
  const cfg = cfgData?.config[market]
  const ms = statusData?.markets[market]
  const statusById = useMemo(() => {
    const m: Record<string, SourceStatus> = {}
    for (const s of ms?.sources ?? []) m[s.id] = s
    return m
  }, [ms])

  if (!cfg) return null

  // 按 order 排序展示
  const ordered = [...cfg.order]

  function applyChange(next: Partial<MarketConfig>) {
    update({
      market,
      order: next.order ?? cfg!.order,
      disabled: next.disabled ?? cfg!.disabled,
      pinned: next.pinned !== undefined ? next.pinned : cfg!.pinned,
    })
  }

  const okCount = (ms?.sources ?? []).filter((s) => s.ok && s.enabled).length

  return (
    <div>
      {/* 市场标题 + 汇总 */}
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-[#8b949e] uppercase tracking-wider flex items-center gap-1.5">
          <span>{flag}</span> {label}
        </h3>
        <div className="flex items-center gap-2 text-[10px]">
          {ms && (
            <span className={okCount > 0 ? "text-[#3fb950]" : "text-[#e3b341]"}>
              {okCount > 0 ? `${okCount} 源可用` : "无真实源 · 演示兜底"}
            </span>
          )}
          {cfg.pinned && <span className="text-[#e3b341]">已强制 {cfg.pinned}</span>}
          {(isPending || isFetching) && <Spinner size="sm" />}
        </div>
      </div>

      {/* 源列表 */}
      <div className="space-y-1.5">
        {ordered.map((sid, i) => {
          const meta = catalog.find((c) => c.id === sid)
          if (!meta) return null
          return (
            <SourceRow key={sid}
              sourceId={sid}
              status={statusById[sid]}
              metaName={meta.name}
              metaNote={meta.note}
              metaRealtime={meta.realtime}
              index={i}
              total={ordered.length}
              isActive={ms?.active_source === sid}
              order={cfg.order}
              disabled={cfg.disabled}
              pinned={cfg.pinned}
              canEdit={canTrade}
              onChange={applyChange}
            />
          )
        })}
      </div>
    </div>
  )
}

// ── 主面板 ─────────────────────────────────────────────────────
export function DataSourcePanel() {
  const [autoRefresh, setAutoRefresh] = useState(false)
  const { isLoading: cfgLoading, isError: cfgError } = useSourcesConfig()
  const { refetch, isFetching, dataUpdatedAt } = useSourcesStatus(autoRefresh ? AUTO_REFRESH_MS : false)

  // 首次进入自动探活一次
  useEffect(() => { refetch() }, [refetch])

  if (cfgError) {
    return <p className="text-xs text-[#f85149] py-2">无法加载数据源配置，请确认后端已启动</p>
  }
  if (cfgLoading) {
    return <div className="flex justify-center py-6"><Spinner /></div>
  }

  const updatedLabel = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false })
    : "—"

  return (
    <div className="space-y-5">
      {/* 说明 + 刷新控制 */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[11px] text-[#6e7681] leading-relaxed flex-1 min-w-0">
          每个市场支持多个数据源，按优先级自动切换；可拖动排序、禁用、或
          <span className="text-[#e3b341]"> ★ 强制</span>只用某一源。真实源全部失败时自动降级为
          <span className="text-[#8b949e]"> 合成演示数据</span>，平台永不断供。
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <label className="flex items-center gap-1.5 text-[10px] text-[#8b949e] cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-[#58a6ff]" />
            自动刷新(20s)
          </label>
          <button onClick={() => refetch()} disabled={isFetching}
            className="btn btn-ghost text-xs py-1 px-3">
            {isFetching ? <Spinner size="sm" /> : "立即检测"}
          </button>
        </div>
      </div>
      <p className="text-[10px] text-[#6e7681] -mt-3">上次检测：{updatedLabel}</p>

      {/* 三市场卡片 */}
      <div className="space-y-5">
        {MARKETS.map((m, i) => (
          <div key={m.key}>
            {i > 0 && <div className="border-t border-[#21262d] mb-5" />}
            <MarketSourceCard market={m.key} flag={m.flag} label={m.label} />
          </div>
        ))}
      </div>
    </div>
  )
}
