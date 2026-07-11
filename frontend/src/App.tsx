import { Component, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { useAuthStore } from "@/stores/auth"
import { ToastProvider } from "@/components/ui/Toast"

// ── Error Boundary ─────────────────────────────────────────────
interface EBState { hasError: boolean; message: string }
class ErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { hasError: false, message: "" }
  }
  static getDerivedStateFromError(err: unknown): EBState {
    return { hasError: true, message: err instanceof Error ? err.message : String(err) }
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[#0d1117] flex items-center justify-center p-8">
          <div className="max-w-lg w-full bg-[#161b22] border border-[#f85149]/40 rounded-lg p-6">
            <h2 className="text-[#f85149] font-semibold mb-2">页面渲染错误</h2>
            <pre className="text-[#8b949e] text-xs whitespace-pre-wrap break-all">{this.state.message}</pre>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 px-4 py-2 rounded bg-[#21262d] text-[#e6edf3] text-sm hover:bg-[#30363d] transition-colors"
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

import { Login } from "@/pages/Login"
import { Dashboard } from "@/pages/Dashboard"
import { Market } from "@/pages/Market"
import { Strategies } from "@/pages/Strategies"
import { Backtest } from "@/pages/Backtest"
import { Orders } from "@/pages/Orders"
import { Portfolio } from "@/pages/Portfolio"
import { Risk } from "@/pages/Risk"
import { AlgoLab } from "@/pages/AlgoLab"
import { PortfolioOptimizer } from "@/pages/PortfolioOptimizer"
import { FactorAnalysis } from "@/pages/FactorAnalysis"
import { Screener } from "@/pages/Screener"
import { AlertsPage } from "@/pages/Alerts"
import { LiveStrategy } from "@/pages/LiveStrategy"
import { Settings } from "@/pages/Settings"

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

function Protected({ element }: { element: React.ReactNode }) {
  return <ProtectedRoute>{element}</ProtectedRoute>
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <div className="min-h-screen bg-[#0d1117]">
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/"            element={<Protected element={<Dashboard />} />} />
              <Route path="/market"      element={<Protected element={<Market />} />} />
              <Route path="/strategies"  element={<Protected element={<Strategies />} />} />
              <Route path="/backtest"    element={<Protected element={<Backtest />} />} />
              <Route path="/orders"      element={<Protected element={<Orders />} />} />
              <Route path="/portfolio"   element={<Protected element={<Portfolio />} />} />
              <Route path="/portfolio-optimizer" element={<Protected element={<PortfolioOptimizer />} />} />
              <Route path="/risk"        element={<Protected element={<Risk />} />} />
              <Route path="/algolab"       element={<Protected element={<AlgoLab />} />} />
              <Route path="/factor"        element={<Protected element={<FactorAnalysis />} />} />
              <Route path="/screener"      element={<Protected element={<Screener />} />} />
              <Route path="/alerts"          element={<Protected element={<AlertsPage />} />} />
              <Route path="/live-strategy" element={<Protected element={<LiveStrategy />} />} />
              <Route path="/settings"      element={<Protected element={<Settings />} />} />
              <Route path="*"            element={<Navigate to="/" replace />} />
            </Routes>
            </div>
          </BrowserRouter>
        </ToastProvider>
        <ReactQueryDevtools />
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
