import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import { NotificationRow } from "@/pages/notifications/NotificationRow"
import {
  useClearNotifications,
  useDeleteNotification,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationInbox,
  type NotificationItem,
} from "@/hooks/useNotifications"

const PAGE_LIMIT = 100

/**
 * 通知中心（V3 G5 §2.5）
 *
 * 站内是统一事件总线的默认渠道：回测 / 寻优 / 挖掘完成、数据源降级、价格预警
 * 都会落在这里。Telegram / Webhook 需要在「设置 → 通知渠道」里主动勾选。
 */
export function Notifications() {
  const { toast } = useToast()
  const [unreadOnly, setUnreadOnly] = useState(false)

  const inboxQ = useNotificationInbox(unreadOnly, PAGE_LIMIT)
  const markReadM = useMarkNotificationRead()
  const markAllM = useMarkAllNotificationsRead()
  const deleteM = useDeleteNotification()
  const clearM = useClearNotifications()

  const items = inboxQ.data?.items ?? []
  const unread = inboxQ.data?.unread ?? 0

  const handleToggleRead = (item: NotificationItem) => {
    markReadM.mutate(
      { id: item.id, isRead: !item.is_read },
      { onError: (e) => toast(`操作失败: ${e.message}`, "error") },
    )
  }

  const handleDelete = (item: NotificationItem) => {
    deleteM.mutate(item.id, {
      onError: (e) => toast(`删除失败: ${e.message}`, "error"),
    })
  }

  const handleMarkAll = () => {
    markAllM.mutate(undefined, {
      onSuccess: (r) => toast(`已标记 ${r.updated} 条为已读`, "success"),
      onError: (e) => toast(`操作失败: ${e.message}`, "error"),
    })
  }

  const handleClear = () => {
    clearM.mutate(undefined, {
      onSuccess: (r) => toast(`已清空 ${r.deleted} 条通知`, "success"),
      onError: (e) => toast(`清空失败: ${e.message}`, "error"),
    })
  }

  return (
    <AppShell title="通知中心">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-sm text-[#e6edf3]">
            未读 <span className="font-mono text-[#58a6ff]">{unread}</span>
          </span>
          <button
            onClick={() => setUnreadOnly((v) => !v)}
            aria-pressed={unreadOnly}
            className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
              unreadOnly
                ? "border-[#58a6ff] bg-[#1f6feb]/20 text-[#58a6ff]"
                : "border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]"
            }`}
          >
            仅看未读
          </button>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleMarkAll}
            disabled={unread === 0 || markAllM.isPending}
            className="rounded-md border border-[#30363d] px-3 py-1.5 text-sm text-[#8b949e] transition-colors hover:text-[#e6edf3] disabled:opacity-40"
          >
            全部标为已读
          </button>
          <button
            onClick={handleClear}
            disabled={items.length === 0 || clearM.isPending}
            className="rounded-md border border-[#30363d] px-3 py-1.5 text-sm text-[#8b949e] transition-colors hover:text-[#f85149] disabled:opacity-40"
          >
            清空
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-[#21262d] bg-[#161b22]">
        {inboxQ.isLoading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : inboxQ.isError ? (
          <div className="py-8">
            <EmptyState
              title="通知加载失败"
              description="稍后重试；通知是旁路系统，不影响其他功能"
            />
          </div>
        ) : items.length === 0 ? (
          <div className="py-8">
            <EmptyState
              title={unreadOnly ? "没有未读通知" : "暂无通知"}
              description="回测、参数寻优、因子挖掘完成后会在这里提醒你"
            />
          </div>
        ) : (
          <ul>
            {items.map((item) => (
              <NotificationRow
                key={item.id}
                item={item}
                onToggleRead={handleToggleRead}
                onDelete={handleDelete}
              />
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  )
}
