import { ROLE_BADGE_STYLES, ROLE_LABELS, normalizeRole } from "@/hooks/useRbac"

interface RoleBadgeProps {
  role: string | null | undefined
  size?: "sm" | "md"
  className?: string
}

/** 角色徽章：展示当前用户的 RBAC 角色（管理员 / 交易员 / 只读用户）。 */
export function RoleBadge({ role, size = "md", className = "" }: RoleBadgeProps) {
  const normalized = normalizeRole(role)
  const sizeCls = size === "sm" ? "text-[10px] px-1.5 py-0.5" : "text-xs px-2 py-0.5"

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border font-medium ${sizeCls} ${ROLE_BADGE_STYLES[normalized]} ${className}`}
      title={`当前角色：${ROLE_LABELS[normalized]}`}
    >
      {ROLE_LABELS[normalized]}
    </span>
  )
}
