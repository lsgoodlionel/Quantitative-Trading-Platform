import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useAuthStore } from "@/stores/auth"
import { getRoleFromToken } from "@/lib/jwt"

// ── 角色模型 ──────────────────────────────────────────────────────────────────
// 层级：Viewer < Trader < Admin（与后端 app/core/rbac.py 保持一致）

export type Role = "viewer" | "trader" | "admin"

const ROLE_RANK: Record<Role, number> = {
  viewer: 0,
  trader: 1,
  admin: 2,
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "只读用户",
  trader: "交易员",
  admin: "管理员",
}

/** 徽章配色（沿用项目 GitHub 暗色调）。 */
export const ROLE_BADGE_STYLES: Record<Role, string> = {
  viewer: "text-[#8b949e] bg-[#161b22] border-[#30363d]",
  trader: "text-[#58a6ff] bg-[#1c2a3a] border-[#388bfd]/30",
  admin: "text-[#e3b341] bg-[#2a2415] border-[#e3b341]/40",
}

/** 任意字符串安全归一化为 Role；未知一律降级为 viewer（fail-safe）。 */
export function normalizeRole(raw: string | null | undefined): Role {
  const v = (raw ?? "").trim().toLowerCase()
  return v === "admin" || v === "trader" || v === "viewer" ? v : "viewer"
}

export function hasPermission(role: Role, minRole: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minRole]
}

// ── /auth/me 查询 ─────────────────────────────────────────────────────────────

export interface CurrentUser {
  id: string
  email: string
  role: string
}

/**
 * 拉取当前用户信息（含权威 role），并同步写回 auth store。
 * 已登录时启用；未登录时不请求。
 */
export function useCurrentUser() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const setRole = useAuthStore((s) => s.setRole)

  return useQuery<CurrentUser>({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      const user = await api.get<CurrentUser>("/api/v1/auth/me")
      setRole(user.role)
      return user
    },
    enabled: isAuthenticated,
    staleTime: 1000 * 60 * 5,
  })
}

export interface Permissions {
  role: Role
  roleLabel: string
  canTrade: boolean // Trader+：下单、撤单等交易操作
  canAdmin: boolean // Admin：券商密钥等系统配置
}

/**
 * 当前用户权限。优先使用 store 中的 role（来自 /auth/me），
 * 回退到从 access_token 解析，最终兜底 viewer。
 */
export function usePermissions(): Permissions {
  const storedRole = useAuthStore((s) => s.role)
  const token = useAuthStore((s) => s.token)

  const role = normalizeRole(storedRole ?? getRoleFromToken(token))

  return {
    role,
    roleLabel: ROLE_LABELS[role],
    canTrade: hasPermission(role, "trader"),
    canAdmin: hasPermission(role, "admin"),
  }
}
