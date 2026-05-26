import { create } from "zustand"
import { persist } from "zustand/middleware"

interface AuthState {
  token: string | null
  user: string | null          // display username
  isAuthenticated: boolean
  login: (token: string, username: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,

      login: (token, username) => {
        localStorage.setItem("access_token", token)
        set({ token, user: username, isAuthenticated: true })
      },

      logout: () => {
        localStorage.removeItem("access_token")
        set({ token: null, user: null, isAuthenticated: false })
      },
    }),
    {
      name: "quantbot-auth",
      partialize: (state) => ({ token: state.token, user: state.user, isAuthenticated: state.isAuthenticated }),
    },
  ),
)
