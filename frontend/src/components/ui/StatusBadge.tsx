import { clsx } from "clsx"
import type { OrderStatus } from "@/types"

const ORDER_STATUS_STYLES: Record<OrderStatus, string> = {
  pending_submit: "bg-[#21262d] text-[#8b949e] border-[#30363d]",
  submitted:      "bg-[#1c2536] text-[#58a6ff] border-[#58a6ff]/30",
  partial:        "bg-[#272111] text-[#e3b341] border-[#e3b341]/30",
  filled:         "bg-[#162a1e] text-[#3fb950] border-[#3fb950]/30",
  cancelled:      "bg-[#21262d] text-[#6e7681] border-[#30363d]",
  rejected:       "bg-[#2a1b1b] text-[#f85149] border-[#f85149]/30",
  expired:        "bg-[#21262d] text-[#6e7681] border-[#30363d]",
}

const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  pending_submit: "待提交",
  submitted:      "已提交",
  partial:        "部分成交",
  filled:         "已成交",
  cancelled:      "已撤销",
  rejected:       "已拒绝",
  expired:        "已过期",
}

interface StatusBadgeProps {
  status: OrderStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border",
        ORDER_STATUS_STYLES[status] ?? "bg-[#21262d] text-[#8b949e] border-[#30363d]",
      )}
    >
      {ORDER_STATUS_LABELS[status] ?? status}
    </span>
  )
}
