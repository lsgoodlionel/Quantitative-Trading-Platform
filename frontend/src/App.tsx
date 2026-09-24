import { Component, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter, Routes } from "react-router-dom"
import { ToastProvider } from "@/components/ui/Toast"
import { CommandPaletteHost } from "@/components/palette/CommandPaletteHost"
import { CopilotLauncher } from "@/components/copilot/CopilotLauncher"
import { SymbolProvider } from "@/contexts/SymbolContext"
import { appRouteElements } from "@/routes"
import { useAuthStore } from "@/stores/auth"

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

/**
 * 路由内部的外壳：命令面板与全局标的上下文都依赖 router
 * （前者要 navigate，后者要读写 ?symbol=），所以只能挂在 BrowserRouter 里面。
 */
function RoutedShell() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  return (
    <SymbolProvider>
      <div className="min-h-screen bg-[#0d1117]">
        <Routes>{appRouteElements}</Routes>
      </div>
      {/* 登录页不挂：那里按 ⌘K 跳过去只会被路由守卫弹回来 */}
      <CommandPaletteHost enabled={isAuthenticated} />
      {/* Copilot 挂在 SymbolProvider 之内：它要读当前标的来补全「回测一下这只」这类指代。
          同样登录后才挂 —— 未登录时它的工具一个都调不通。 */}
      {isAuthenticated && <CopilotLauncher />}
    </SymbolProvider>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <RoutedShell />
          </BrowserRouter>
        </ToastProvider>
        <ReactQueryDevtools />
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
