import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 通知中心（V3 G5 §2.5）───────────────────────────────────────
// 站内收件箱：统一事件总线派发的每条事件都会落在这里，
// Telegram / Webhook 只是额外的外发渠道。

/**
 * 事件类型用 string 而非 types/index.ts 的 NotifyEventType 联合类型：
 * 后端新增事件类型时收件箱不应因为前端类型没跟上而渲染失败。
 */
export type NotificationType = string

export interface NotificationItem {
  id: string
  type: NotificationType
  title: string
  symbol: string | null
  market: string | null
  payload: Record<string, unknown>
  is_read: boolean
  /** Unix 秒（浮点） */
  created_at: number
}

export interface InboxResponse {
  items: NotificationItem[]
  unread: number
}

const INBOX_KEY = ["notify-inbox"]

/** GET /api/v1/notify/inbox */
export function useNotificationInbox(unreadOnly = false, limit = 50) {
  return useQuery<InboxResponse>({
    queryKey: [...INBOX_KEY, unreadOnly, limit],
    queryFn: () =>
      api.get<InboxResponse>(
        `/api/v1/notify/inbox?limit=${limit}&unread_only=${unreadOnly}`,
      ),
    staleTime: 1000 * 15,
  })
}

/** POST /api/v1/notify/inbox/{id}/read — 切换单条已读状态 */
export function useMarkNotificationRead() {
  const qc = useQueryClient()
  return useMutation<NotificationItem, Error, { id: string; isRead: boolean }>({
    mutationFn: ({ id, isRead }) =>
      api.post<NotificationItem>(`/api/v1/notify/inbox/${id}/read`, { is_read: isRead }),
    onSuccess: () => qc.invalidateQueries({ queryKey: INBOX_KEY }),
  })
}

/** POST /api/v1/notify/inbox/read-all */
export function useMarkAllNotificationsRead() {
  const qc = useQueryClient()
  return useMutation<{ updated: number }, Error, void>({
    mutationFn: () => api.post<{ updated: number }>("/api/v1/notify/inbox/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: INBOX_KEY }),
  })
}

/** DELETE /api/v1/notify/inbox/{id} */
export function useDeleteNotification() {
  const qc = useQueryClient()
  return useMutation<{ deleted: string }, Error, string>({
    mutationFn: (id) => api.delete<{ deleted: string }>(`/api/v1/notify/inbox/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: INBOX_KEY }),
  })
}

/** DELETE /api/v1/notify/inbox — 清空收件箱 */
export function useClearNotifications() {
  const qc = useQueryClient()
  return useMutation<{ deleted: number }, Error, void>({
    mutationFn: () => api.delete<{ deleted: number }>("/api/v1/notify/inbox"),
    onSuccess: () => qc.invalidateQueries({ queryKey: INBOX_KEY }),
  })
}
