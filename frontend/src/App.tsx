import { Component, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter, Routes } from "react-router-dom"
import { ToastProvider } from "@/components/ui/Toast"
import { appRouteElements } from "@/routes"

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

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 30,
      retry: 2,
    },
  },
})

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <div className="min-h-screen bg-[#0d1117]">
              <Routes>{appRouteElements}</Routes>
            </div>
          </BrowserRouter>
        </ToastProvider>
        <ReactQueryDevtools />
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
