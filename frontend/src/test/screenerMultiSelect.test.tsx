import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ToastProvider } from "@/components/ui/Toast"
import { clearWatchlist, loadWatchlist } from "@/lib/watchlist"
import type { ScreenerCandidate, ScreenerRunResult } from "@/hooks/useScreener"

// ── Screener 数据 hook 全部替身化 ───────────────────────────────
// 只测多选贯通（G3），不测数据获取；筛选结果由 mock 直接给出。

function makeCandidate(symbol: string): ScreenerCandidate {
  return {
    symbol,
    market: "US",
    name: `${symbol} Inc`,
    sector: "Tech",
    price: 100,
    change_pct: 1.5,
    pe: 20,
    pb: 3,
    market_cap: 1e11,
    market_cap_yi: 1000,
    dividend_yield: 1,
    volume: 1e6,
    turnover: 1e8,
    turnover_rate: 1,
  }
}

const RESULT: ScreenerRunResult = {
  market: "US",
  generated_at: "2026-01-01T00:00:00Z",
  universe_size: 100,
  count: 3,
  candidates: [makeCandidate("AAPL"), makeCandidate("MSFT"), makeCandidate("NVDA")],
}

vi.mock("@/hooks/useScreener", () => ({
  useScreenerRun: () => ({ mutate: vi.fn(), isPending: false, data: RESULT }),
  useScreenerPresets: () => ({ data: [] }),
  useScreenerSectors: () => ({ data: [] }),
  useScreenerMovers: () => ({ data: null, isLoading: false, isError: true }),
}))

vi.mock("@/hooks/useStrategy", () => ({
  usePresets: () => ({ data: [{ name: "double_ma", description: "" }] }),
}))

const { Screener } = await import("@/pages/Screener")

/** 跳转目标的探针：把 URL 参数直接渲染出来，便于断言「URL 即状态」 */
function OptimizerProbe() {
  const location = useLocation()
  return <div data-testid="optimizer-url">{location.pathname + location.search}</div>
}

function renderScreener() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/screener"]}>
          <Routes>
            <Route path="/screener" element={<Screener />} />
            <Route path="/portfolio-optimizer" element={<OptimizerProbe />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe("Screener multi-select (G3)", () => {
  beforeEach(() => clearWatchlist())

  it("renders one checkbox per candidate plus a select-all box", () => {
    renderScreener()

    expect(screen.getAllByTestId("screener-row")).toHaveLength(3)
    expect(screen.getByLabelText("全选/取消全选")).toBeInTheDocument()
    expect(screen.getByLabelText("选择 AAPL")).toBeInTheDocument()
  })

  it("hides the action bar until something is selected", () => {
    renderScreener()

    expect(screen.queryByTestId("selection-actions")).not.toBeInTheDocument()

    fireEvent.click(screen.getByLabelText("选择 AAPL"))

    expect(screen.getByTestId("selection-actions")).toBeInTheDocument()
    expect(screen.getByText("已选 1 只")).toBeInTheDocument()
  })

  it("selects and deselects all rows", () => {
    renderScreener()
    const selectAll = screen.getByLabelText("全选/取消全选")

    fireEvent.click(selectAll)
    expect(screen.getByText("已选 3 只")).toBeInTheDocument()

    fireEvent.click(selectAll)
    expect(screen.queryByTestId("selection-actions")).not.toBeInTheDocument()
  })

  it("deselects an individual row without touching the others", () => {
    renderScreener()

    fireEvent.click(screen.getByLabelText("全选/取消全选"))
    fireEvent.click(screen.getByLabelText("选择 MSFT"))

    expect(screen.getByText("已选 2 只")).toBeInTheDocument()
    expect(screen.getByLabelText("选择 MSFT")).not.toBeChecked()
    expect(screen.getByLabelText("选择 AAPL")).toBeChecked()
  })

  it("clears the selection via 清空选择", () => {
    renderScreener()

    fireEvent.click(screen.getByLabelText("选择 AAPL"))
    fireEvent.click(screen.getByRole("button", { name: "清空选择" }))

    expect(screen.queryByTestId("selection-actions")).not.toBeInTheDocument()
    expect(screen.getByLabelText("选择 AAPL")).not.toBeChecked()
  })

  it("adds the selected candidates to the watchlist", () => {
    renderScreener()

    fireEvent.click(screen.getByLabelText("选择 AAPL"))
    fireEvent.click(screen.getByLabelText("选择 NVDA"))
    fireEvent.click(screen.getByRole("button", { name: /加自选池/ }))

    expect(loadWatchlist().map((i) => i.symbol)).toEqual(["AAPL", "NVDA"])
  })

  it("navigates to the optimizer with symbols in the URL", () => {
    renderScreener()

    fireEvent.click(screen.getByLabelText("全选/取消全选"))
    fireEvent.click(screen.getByRole("button", { name: /送组合优化/ }))

    const url = screen.getByTestId("optimizer-url").textContent ?? ""
    expect(url).toContain("/portfolio-optimizer?")
    const params = new URLSearchParams(url.split("?")[1])
    expect(params.get("symbols")).toBe("AAPL,MSFT,NVDA")
    expect(params.get("market")).toBe("US")
  })

  it("opens the batch backtest modal with the selected count", () => {
    renderScreener()

    fireEvent.click(screen.getByLabelText("全选/取消全选"))
    fireEvent.click(screen.getByRole("button", { name: /批量回测/ }))

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByText("批量回测 · 3 只标的")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /开始回测（3 只）/ })).toBeInTheDocument()
  })
})
