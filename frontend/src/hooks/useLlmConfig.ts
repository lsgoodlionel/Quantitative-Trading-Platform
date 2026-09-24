import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 类型（与 backend/app/api/v1/endpoints/llm.py 的 schema 一一对应）──────────

export type ProviderKind = "openai_compat" | "anthropic"

export interface ProviderStatus {
  id: string
  label: string
  kind: ProviderKind
  requires_key: boolean
  doc_url: string
  /** 建议不是白名单：下拉框的候选而已，用户可自由输入任意模型名。 */
  suggested_models: string[]
  supports_model_listing: boolean
  default_base_url: string
  /** 保存过配置 */
  configured: boolean
  /** 配置完整到可以直接发请求（Ollama 不需要密钥也能 ready） */
  ready: boolean
  base_url: string | null
  default_model: string | null
  /** 掩码，如 "sk••••••••1234"。后端永远不回完整 key。 */
  key_hint: string | null
}

export interface ActiveModel {
  configured: boolean
  provider_id: string | null
  provider_label: string | null
  model: string | null
  /** false = 系统按预设顺序自动挑的，不是用户显式选的 */
  explicit: boolean
}

export interface ProvidersResponse {
  providers: ProviderStatus[]
  active: ActiveModel
}

export interface SaveProviderRequest {
  base_url: string
  default_model: string
  /** 留空 / 省略 = 保持原值（不是清空）。清空走 DELETE。 */
  api_key?: string
}

export interface TestResult {
  ok: boolean
  provider_id: string
  model: string | null
  latency_ms: number | null
  error: string | null
}

export interface ModelListResponse {
  provider_id: string
  models: string[]
}

const BASE = "/api/v1/llm"
const PROVIDERS_KEY = ["llm", "providers"] as const

// ── 展示辅助 ──────────────────────────────────────────────────────────────────

/** 一张卡片的状态徽标文案 + 配色。 */
export function providerStateLabel(p: ProviderStatus): string {
  if (p.ready) return "可用"
  if (!p.configured) return "未配置"
  return p.requires_key && !p.key_hint ? "缺少密钥" : "配置不完整"
}

export function providerStateTone(p: ProviderStatus): "ok" | "warn" | "idle" {
  if (p.ready) return "ok"
  return p.configured ? "warn" : "idle"
}

/** 把测试结果压成一句可直接渲染的话。 */
export function describeTestResult(r: TestResult): string {
  if (r.ok) {
    const latency = r.latency_ms == null ? "" : ` · ${Math.round(r.latency_ms)} ms`
    return `连接成功 · ${r.model ?? "默认模型"}${latency}`
  }
  return r.error ?? "连接失败"
}

// ── 查询 ─────────────────────────────────────────────────────────────────────

export function useLlmProviders() {
  return useQuery<ProvidersResponse>({
    queryKey: PROVIDERS_KEY,
    queryFn: () => api.get<ProvidersResponse>(`${BASE}/providers`),
  })
}

// ── 变更 ─────────────────────────────────────────────────────────────────────

export function useSaveProvider() {
  const qc = useQueryClient()
  return useMutation<ProviderStatus, Error, { id: string; body: SaveProviderRequest }>({
    mutationFn: ({ id, body }) => api.put<ProviderStatus>(`${BASE}/providers/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROVIDERS_KEY }),
  })
}

export function useDeleteProvider() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`${BASE}/providers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROVIDERS_KEY }),
  })
}

export function useTestProvider() {
  return useMutation<TestResult, Error, { id: string; model?: string }>({
    mutationFn: ({ id, model }) =>
      api.post<TestResult>(
        `${BASE}/providers/${id}/test${model ? `?model=${encodeURIComponent(model)}` : ""}`,
      ),
  })
}

export function useFetchProviderModels() {
  return useMutation<ModelListResponse, Error, string>({
    mutationFn: (id) => api.get<ModelListResponse>(`${BASE}/providers/${id}/models`),
  })
}

export function useSetActiveModel() {
  const qc = useQueryClient()
  return useMutation<ActiveModel, Error, { provider_id: string; model: string }>({
    mutationFn: (body) => api.put<ActiveModel>(`${BASE}/active`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROVIDERS_KEY }),
  })
}
