import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 类型（与 backend/app/api/v1/endpoints/copilot.py 的 schema 一一对应）──────

export interface CopilotCard {
  kind: string
  title: string
  data: Record<string, unknown>
}

export interface DraftField {
  label: string
  value: string
  emphasis: boolean
}

export interface CopilotDraft {
  draft_id: string
  tool: string
  /** "order" | "rebalance"，确认执行时原样回传 */
  action: string
  title: string
  summary: string
  fields: DraftField[]
  /** 校验过的完整参数，确认时原样回传（服务端会再校验一遍） */
  params: Record<string, unknown>
  /** 执行时真正走的既有端点 —— 展示出来，证明没绕过风控 */
  endpoint: string
  method: string
  legs: Record<string, unknown>[]
  warnings: string[]
}

export interface CopilotChatResponse {
  text: string
  cards: CopilotCard[]
  drafts: CopilotDraft[]
  rounds: number
  /** true = 撞到工具调用轮次上限，回复是不完整的 */
  truncated: boolean
  /** true = 一个模型都没配，前端应展示引导块而不是错误 */
  needs_setup: boolean
  setup_url: string | null
  provider_id: string | null
  model: string | null
  /** "invalid_tool_arguments" | "provider_unavailable" | null */
  error_kind: string | null
}

export interface CopilotMessageIn {
  role: "user" | "assistant"
  content: string
}

export interface DraftExecuteResponse {
  action: string
  result: Record<string, unknown>
}

export interface ToolInfo {
  name: string
  description: string
  requires_confirmation: boolean
}

const BASE = "/api/v1/copilot"

// ── 展示辅助 ──────────────────────────────────────────────────────────────────

/** 未配置模型时引导用户去的页面（后端给不出时的兜底）。 */
export const DEFAULT_SETUP_URL = "/settings/models"

/** 把 `error_kind` 压成一句可直接渲染的提示；正常时返回 null。 */
export function describeErrorKind(kind: string | null): string | null {
  if (kind === "invalid_tool_arguments") {
    return "模型返回的参数无法解析，本次没有执行任何操作。"
  }
  if (kind === "provider_unavailable") return "模型服务当前连不上，请检查「模型管理」里的配置。"
  return null
}

/** 草稿卡片的强调色：下单是红的，不是灰的 —— 这是要花真钱的操作。 */
export function draftTone(action: string): "danger" | "warn" {
  return action === "order" ? "danger" : "warn"
}

// ── 变更 ─────────────────────────────────────────────────────────────────────

export function useCopilotChat() {
  return useMutation<CopilotChatResponse, Error, CopilotMessageIn[]>({
    mutationFn: (messages) =>
      api.post<CopilotChatResponse>(`${BASE}/chat`, { messages }),
  })
}

export function useExecuteDraft() {
  return useMutation<DraftExecuteResponse, Error, CopilotDraft>({
    mutationFn: (draft) =>
      api.post<DraftExecuteResponse>(`${BASE}/drafts/execute`, {
        draft_id: draft.draft_id,
        action: draft.action,
        params: draft.params,
      }),
  })
}
