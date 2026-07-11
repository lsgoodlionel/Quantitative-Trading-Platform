import { create } from "zustand"
import { persist } from "zustand/middleware"
import { getRoleFromToken } from "@/lib/jwt"

interface AuthState {
  token: string | null
  user: string | null // display username
  role: string | null // RBAC 角色：admin / trader / viewer（来自 JWT 或 /auth/me）
  isAuthenticated: boolean
  login: (token: string, username: string, role?: string | null) => void
  setRole: (role: string | null) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      role: null,
      isAuthenticated: false,

      login: (token, username, role) => {
        localStorage.setItem("access_token", token)
        // role 未显式传入时，从 JWT payload 解析
        const resolvedRole = role ?? getRoleFromToken(token)
        set({ token, user: username, role: resolvedRole, isAuthenticated: true })
      },

      setRole: (role) => set({ role }),

      logout: () => {
        localStorage.removeItem("access_token")
        set({ token: null, user: null, role: null, isAuthenticated: false })
      },
    }),
    {
      name: "quantbot-auth",
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        role: state.role,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
)
