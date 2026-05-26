import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useAuthStore } from "@/stores/auth"
import { ApiError } from "@/lib/api"
import { Spinner } from "@/components/ui/Spinner"

interface LoginResponse {
  access_token: string
  token_type: string
}

export function Login() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!username || !password) return

    setError("")
    setLoading(true)

    try {
      // FastAPI OAuth2 form login
      const body = new URLSearchParams({ username, password })
      const resp = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new ApiError(resp.status, (data as { detail?: string }).detail ?? "登录失败")
      }
      const data = (await resp.json()) as LoginResponse
      login(data.access_token, username)
      navigate("/", { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "登录失败，请重试")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0d1117] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="text-5xl font-bold font-mono text-[#58a6ff] mb-2">Q</div>
          <p className="text-[#e6edf3] text-xl font-semibold">QuantBot</p>
          <p className="text-[#6e7681] text-sm mt-1">多市场量化交易平台</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-4"
        >
          <div>
            <label className="label">用户名</label>
            <input
              type="text"
              className="input w-full mt-1"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              disabled={loading}
              placeholder="admin"
            />
          </div>

          <div>
            <label className="label">密码</label>
            <input
              type="password"
              className="input w-full mt-1"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              disabled={loading}
              placeholder="••••••••"
            />
          </div>

          {error && (
            <p className="text-[#f85149] text-sm bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            className="btn btn-primary w-full"
            disabled={loading || !username || !password}
          >
            {loading ? <Spinner size="sm" className="mx-auto" /> : "登录"}
          </button>
        </form>

        <p className="text-center text-[#6e7681] text-xs mt-4">
          QuantBot v0.1 · 仅供内部使用
        </p>
      </div>
    </div>
  )
}
