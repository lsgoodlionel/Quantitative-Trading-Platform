import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { useAuthStore } from "@/stores/auth"
import { ToastProvider } from "@/components/ui/Toast"

import { Login } from "@/pages/Login"
import { Dashboard } from "@/pages/Dashboard"
import { Market } from "@/pages/Market"
import { Strategies } from "@/pages/Strategies"
import { Backtest } from "@/pages/Backtest"
import { Orders } from "@/pages/Orders"
import { Portfolio } from "@/pages/Portfolio"
import { Risk } from "@/pages/Risk"
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
              <Route path="/risk"        element={<Protected element={<Risk />} />} />
              <Route path="/settings"    element={<Protected element={<Settings />} />} />
              <Route path="*"            element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </BrowserRouter>
      </ToastProvider>
      <ReactQueryDevtools />
    </QueryClientProvider>
  )
}
