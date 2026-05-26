import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { useAuthStore } from "@/stores/auth"

// 页面占位（Phase 5 逐步实现）
const Dashboard = () => <div className="p-8 text-gray-100">Dashboard — Phase 5 实现中</div>
const Market = () => <div className="p-8 text-gray-100">行情看板 — Phase 5</div>
const Strategies = () => <div className="p-8 text-gray-100">策略管理 — Phase 5</div>
const Backtest = () => <div className="p-8 text-gray-100">回测管理 — Phase 5</div>
const Orders = () => <div className="p-8 text-gray-100">订单记录 — Phase 5</div>
const Portfolio = () => <div className="p-8 text-gray-100">组合分析 — Phase 5</div>
const Risk = () => <div className="p-8 text-gray-100">风控配置 — Phase 5</div>
const Settings = () => <div className="p-8 text-gray-100">系统设置 — Phase 5</div>
const Login = () => <div className="p-8 text-gray-100">登录页 — Phase 5</div>

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 30,
      retry: 2,
    },
  },
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-gray-950">
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route path="/market" element={<ProtectedRoute><Market /></ProtectedRoute>} />
            <Route path="/strategies" element={<ProtectedRoute><Strategies /></ProtectedRoute>} />
            <Route path="/backtest" element={<ProtectedRoute><Backtest /></ProtectedRoute>} />
            <Route path="/orders" element={<ProtectedRoute><Orders /></ProtectedRoute>} />
            <Route path="/portfolio" element={<ProtectedRoute><Portfolio /></ProtectedRoute>} />
            <Route path="/risk" element={<ProtectedRoute><Risk /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </BrowserRouter>
      <ReactQueryDevtools />
    </QueryClientProvider>
  )
}
