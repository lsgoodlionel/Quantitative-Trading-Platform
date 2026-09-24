import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 投研产物库（V4 M4）─────────────────────────────────────────
// 数据集 / 模型 / 信号三类产物的列表、预览与删除。
//
// 产物内容本身不经前端流转：`preview` 只回结构化摘要，
// 模型 `detail` 只回特征重要性等元信息 —— 反序列化在服务端做。

export type ArtifactKind = "dataset" | "model" | "signal"

export interface ArtifactMeta {
  artifact_id: string
  kind: ArtifactKind
  name: string
  created_at: string
  size_bytes: number
  tags: Record<string, string>
  checksum: string
}

export interface ArtifactListResponse {
  total: number
  limit: number
  offset: number
  items: ArtifactMeta[]
  /** 容量策略标识与说明 —— 后端明示，前端如实展示，不要自己编一套措辞 */
  capacity_policy: string
  capacity_note: string
}

export interface ArtifactFilters {
  kind?: ArtifactKind
  nameContains?: string
  limit?: number
  offset?: number
}

function toQuery(f: ArtifactFilters): string {
  const params = new URLSearchParams()
  if (f.kind) params.set("kind", f.kind)
  if (f.nameContains) params.set("name_contains", f.nameContains)
  params.set("limit", String(f.limit ?? 50))
  params.set("offset", String(f.offset ?? 0))
  return params.toString()
}

export function useLabArtifacts(filters: ArtifactFilters = {}) {
  return useQuery<ArtifactListResponse, Error>({
    queryKey: ["lab-artifacts", filters],
    queryFn: () => api.get<ArtifactListResponse>(`/api/v1/lab/artifacts?${toQuery(filters)}`),
  })
}

export function useArtifactPreview(artifactId: string | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["lab-artifact-preview", artifactId],
    queryFn: () => api.get(`/api/v1/lab/artifacts/${artifactId}/preview`),
    enabled: artifactId !== null,
  })
}

export function useModelDetail(artifactId: string | null, kind: ArtifactKind | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["lab-artifact-detail", artifactId],
    queryFn: () => api.get(`/api/v1/lab/artifacts/${artifactId}/detail`),
    // detail 只对模型有意义，其他类别调了会 400
    enabled: artifactId !== null && kind === "model",
  })
}

export function useDeleteArtifact() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (artifactId) => api.delete(`/api/v1/lab/artifacts/${artifactId}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["lab-artifacts"] }),
  })
}

/** 字节数 → 人类可读。产物动辄几十 MB，直接显示字节数没人看得懂。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB"]
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`
}

export const KIND_LABELS: Record<ArtifactKind, string> = {
  dataset: "数据集",
  model: "模型",
  signal: "信号",
}
