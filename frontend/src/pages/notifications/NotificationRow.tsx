import {
  formatPayloadValue,
  formatRelativeTime,
  notificationMeta,
} from "@/lib/notificationMeta"
import type { NotificationItem } from "@/hooks/useNotifications"

const TONE_CLASS: Record<string, string> = {
  info:    "text-[#58a6ff] border-[#1f6feb]/40",
  success: "text-[#3fb950] border-[#238636]/40",
  warning: "text-[#e3b341] border-[#9e6a03]/50",
  danger:  "text-[#f85149] border-[#da3633]/50",
}

interface NotificationRowProps {
  item: NotificationItem
  onToggleRead: (item: NotificationItem) => void
  onDelete: (item: NotificationItem) => void
}

export function NotificationRow({ item, onToggleRead, onDelete }: NotificationRowProps) {
  const meta = notificationMeta(item.type)
  const entries = Object.entries(item.payload ?? {})

  return (
    <li
      data-testid="notification-row"
      data-read={item.is_read}
      className={`flex gap-3 px-4 py-3 border-b border-[#21262d]/60 transition-colors ${
        item.is_read ? "bg-transparent" : "bg-[#1f6feb]/[0.06]"
      } hover:bg-[#161b22]`}
    >
      {/* 未读圆点：不靠颜色单独表意，配合 aria-label */}
      <span
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
          item.is_read ? "bg-transparent" : "bg-[#58a6ff]"
        }`}
        aria-label={item.is_read ? "已读" : "未读"}
      />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`shrink-0 rounded border px-1.5 py-0.5 text-xs ${
              TONE_CLASS[meta.tone] ?? TONE_CLASS.info
            }`}
          >
            {meta.emoji} {meta.label}
          </span>
          <span className={`text-sm ${item.is_read ? "text-[#8b949e]" : "text-[#e6edf3]"}`}>
            {item.title}
          </span>
          {item.symbol && (
            <span className="font-mono text-xs text-[#58a6ff]">
              {item.symbol}
              {item.market ? ` · ${item.market}` : ""}
            </span>
          )}
        </div>

        {entries.length > 0 && (
          <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-[#8b949e]">
            {entries.map(([key, value]) => (
              <div key={key} className="flex gap-1">
                <dt>{key}:</dt>
                <dd className="text-[#c9d1d9] font-mono">{formatPayloadValue(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        <time className="text-xs text-[#6e7681]">{formatRelativeTime(item.created_at)}</time>
        <div className="flex gap-2">
          <button
            onClick={() => onToggleRead(item)}
            className="text-xs text-[#58a6ff] hover:underline"
          >
            {item.is_read ? "标为未读" : "标为已读"}
          </button>
          <button
            onClick={() => onDelete(item)}
            className="text-xs text-[#8b949e] hover:text-[#f85149]"
            aria-label={`删除通知 ${item.title}`}
          >
            删除
          </button>
        </div>
      </div>
    </li>
  )
}
