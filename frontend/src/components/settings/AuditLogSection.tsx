import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import {
  useAuditLogs,
  AUDIT_ACTION_LABELS,
  AUDIT_ACTION_OPTIONS,
  type AuditRecord,
} from "@/hooks/useAudit"

const PAGE_SIZE = 20

// ── 展示工具 ────────────────────────────────────────────────────────────────

/** 动作 → 中文标签，未知动作回退为原始字符串 */
function actionLabel(action: string): string {
  return AUDIT_ACTION_LABELS[action] ?? action
}

/** 动作 → 徽章配色（区分下单/撤单/配置/风控） */
function actionColor(action: string): string {
  if (action.startsWith("order.submit")) return "text-[#3fb950] bg-[#132218] border-[#3fb950]/30"
  if (action.startsWith("order.cancel")) return "text-[#e3b341] bg-[#272111] border-[#e3b341]/30"
  if (action.startsWith("risk")) return "text-[#f85149] bg-[#2a1b1b] border-[#f85149]/30"
  return "text-[#58a6ff] bg-[#132033] border-[#388bfd]/30"
}

/** 本地时间格式化，解析失败时回退原串 */
function formatTs(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString("zh-CN", { hour12: false })
}

/** 将 detail 字典压缩为一行可读摘要 */
function formatDetail(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail)
  if (entries.length === 0) return "—"
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ")
}

// ── 行 ──────────────────────────────────────────────────────────────────────

function AuditRow({ record }: { record: AuditRecord }) {
  return (
    <tr className="border-b border-[#21262d]/60 last:border-0 align-top">
      <td className="py-2 pr-3 whitespace-nowrap font-mono text-[11px] text-[#8b949e]">
        {formatTs(record.ts)}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-[#e6edf3]">{record.actor}</td>
      <td className="py-2 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${actionColor(record.action)}`}>
          {actionLabel(record.action)}
        </span>
      </td>
      <td className="py-2 text-[11px] text-[#6e7681] leading-relaxed break-all">
        {formatDetail(record.detail)}
      </td>
    </tr>
  )
}

// ── Section ─────────────────────────────────────────────────────────────────

export function AuditLogSection() {
  const [action, setAction] = useState("")
  const [page, setPage] = useState(1)

  const { data, isLoading, isError, isFetching, refetch } = useAuditLogs({
    page,
    pageSize: PAGE_SIZE,
    action: action || undefined,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  function handleActionChange(next: string) {
    setAction(next)
    setPage(1)
  }

  return (
    <div className="space-y-3">
      {/* 工具栏 */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <select
            className="input text-xs py-1 px-2"
            value={action}
            onChange={(e) => handleActionChange(e.target.value)}
          >
            {AUDIT_ACTION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-[#6e7681]">共 {total} 条</span>
        </div>
        <button
          className="btn btn-ghost text-xs py-1 px-3"
          onClick={() => refetch()}
          disabled={isFetching}
        >
          {isFetching ? <Spinner size="sm" /> : "刷新"}
        </button>
      </div>

      {/* 内容 */}
      {isLoading ? (
        <div className="py-6 flex justify-center">
          <Spinner />
        </div>
      ) : isError ? (
        <p className="text-xs text-[#8b949e] py-4 text-center">
          无法加载审计日志，请确认后端服务已运行
        </p>
      ) : items.length === 0 ? (
        <p className="text-xs text-[#6e7681] py-6 text-center">暂无审计记录</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-[#6e7681] border-b border-[#30363d]">
                <th className="py-2 pr-3 font-medium">时间</th>
                <th className="py-2 pr-3 font-medium">操作者</th>
                <th className="py-2 pr-3 font-medium">动作</th>
                <th className="py-2 font-medium">详情</th>
              </tr>
            </thead>
            <tbody>
              {items.map((record) => (
                <AuditRow key={record.id} record={record} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            className="btn btn-ghost text-xs py-1 px-3"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || isFetching}
          >
            上一页
          </button>
          <span className="text-xs text-[#8b949e]">
            {page} / {totalPages}
          </span>
          <button
            className="btn btn-ghost text-xs py-1 px-3"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages || isFetching}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
