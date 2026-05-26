import { create } from "zustand"
import { persist } from "zustand/middleware"
import { api } from "@/lib/api"

interface AuthState {
  token: string | null
  user: { id: string; email: string; role: string } | null
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,

      login: async (username, password) => {
        const form = new URLSearchParams({ username, password })
        const response = await fetch(
          `${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/v1/auth/token`,
          { method: "POST", body: form, headers: { "Content-Type": "application/x-www-form-urlencoded" } }
        )
        if (!response.ok) throw new Error("Login failed")
        const data = (await response.json()) as { access_token: string }
        localStorage.setItem("access_token", data.access_token)
        const user = await api.get<{ id: string; email: string; role: string }>("/api/v1/auth/me")
        set({ token: data.access_token, user, isAuthenticated: true })
      },

      logout: () => {
        localStorage.removeItem("access_token")
        set({ token: null, user: null, isAuthenticated: false })
      },
    }),
    { name: "quantbot-auth", partialize: (state) => ({ token: state.token, user: state.user }) }
  )
)
