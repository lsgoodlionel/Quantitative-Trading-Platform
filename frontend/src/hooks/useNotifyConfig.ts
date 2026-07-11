import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type {
  NotifyConfig,
  NotifyConfigStatus,
  NotifyEventType,
  NotifyTestResponse,
} from "@/types"

/** GET /api/v1/notify/config（脱敏） */
export function useNotifyConfig() {
  return useQuery<NotifyConfigStatus>({
    queryKey: ["notify-config"],
    queryFn: () => api.get<NotifyConfigStatus>("/api/v1/notify/config"),
  })
}

/** PUT /api/v1/notify/config（空密钥 = 保持原值） */
export function useUpdateNotifyConfig() {
  const qc = useQueryClient()
  return useMutation<NotifyConfigStatus, Error, NotifyConfig>({
    mutationFn: (config) =>
      api.put<NotifyConfigStatus>("/api/v1/notify/config", config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notify-config"] }),
  })
}

interface TestChannelInput {
  channel_id: string
  event_type?: NotifyEventType
}

/** POST /api/v1/notify/test */
export function useTestChannel() {
  return useMutation<NotifyTestResponse, Error, TestChannelInput>({
    mutationFn: (req) =>
      api.post<NotifyTestResponse>("/api/v1/notify/test", req),
  })
}
