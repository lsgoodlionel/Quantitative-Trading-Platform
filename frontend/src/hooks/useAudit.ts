import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AuditRecord {
  id: string
  ts: string
  action: string
  actor: string
  detail: Record<string, unknown>
}

export interface AuditListResponse {
  items: AuditRecord[]
  total: number
  page: number
  page_size: number
}

export interface AuditQuery {
  page?: number
  pageSize?: number
  action?: string
  actor?: string
}

/** 动作 → 中文标签（与后端 AuditAction 常量对应） */
export const AUDIT_ACTION_LABELS: Record<string, string> = {
  "order.submit": "下单",
  "order.cancel": "撤单",
  "broker_config.save": "保存券商配置",
  "broker_config.delete": "删除券商配置",
  "risk_config.update": "修改风控规则",
}

/** 供筛选下拉使用的动作选项（含「全部」） */
export const AUDIT_ACTION_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部动作" },
  ...Object.entries(AUDIT_ACTION_LABELS).map(([value, label]) => ({ value, label })),
]

// ── Hook ──────────────────────────────────────────────────────────────────────

/** GET /api/v1/audit — 分页查询审计日志（倒序，30s 自动刷新） */
export function useAuditLogs(query: AuditQuery = {}) {
  const { page = 1, pageSize = 20, action, actor } = query

  const params = new URLSearchParams()
  params.set("page", String(page))
  params.set("page_size", String(pageSize))
  if (action) params.set("action", action)
  if (actor) params.set("actor", actor)

  return useQuery<AuditListResponse>({
    queryKey: ["audit-logs", page, pageSize, action ?? "", actor ?? ""],
    queryFn: () => api.get<AuditListResponse>(`/api/v1/audit?${params.toString()}`),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  })
}
