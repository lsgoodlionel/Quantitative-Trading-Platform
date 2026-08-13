import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { RebalancePanel } from "@/pages/portfolio/RebalancePanel"
import type { RebalancePreviewResult } from "@/hooks/useRebalance"

const PREVIEW: RebalancePreviewResult = {
  market: "US",
  portfolio_value: 20000,
  legs: [
    {
      symbol: "BBB", current_qty: 100, target_qty: 0, delta_qty: -100,
      price: 50, delta_value: -5000, reason: "close", side: "SELL",
    },
    {
      symbol: "AAA", current_qty: 100, target_qty: 200, delta_qty: 100,
      price: 100, delta_value: 10000, reason: "increase", side: "BUY",
    },
  ],
  total_buy_value: 10000,
  total_sell_value: 5000,
  estimated_commission: 4.5,
  warnings: ["CCC 取不到有效价格，已从调仓计划中剔除"],
  confirm_token: "rb1.100.aaa.bbb.sig",
  expires_in_seconds: 60,
}

function renderPanel(weights: Record<string, number> = { AAA: 1.0 }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RebalancePanel weights={weights} market="US" />
    </QueryClientProvider>,
  )
}

describe("RebalancePanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("blocks preview when weights do not sum to 1", async () => {
    const post = vi.spyOn(api, "post")
    renderPanel({ AAA: 0.5, BBB: 0.25 })

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))

    expect(await screen.findByText(/需要等于 1/)).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })

  it("renders legs table and warnings after preview", async () => {
    vi.spyOn(api, "post").mockResolvedValue(PREVIEW as never)
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))

    expect(await screen.findByText("AAA")).toBeInTheDocument()
    expect(screen.getByText("BBB")).toBeInTheDocument()
    expect(screen.getByText(/CCC 取不到有效价格/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /确认执行 2 笔委托/ })).toBeInTheDocument()
  })

  it("sends the confirm_token back untouched on execute", async () => {
    const post = vi.spyOn(api, "post")
      .mockResolvedValueOnce(PREVIEW as never)
      .mockResolvedValueOnce({ submitted: [], rejected: [] } as never)
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))
    fireEvent.click(await screen.findByRole("button", { name: /确认执行/ }))

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    const [url, body] = post.mock.calls[1]
    expect(url).toBe("/api/v1/portfolio/rebalance/execute")
    expect(body).toMatchObject({
      market: "US",
      confirm_token: PREVIEW.confirm_token,
      legs: PREVIEW.legs,
    })
  })

  it("shows both submitted and rejected legs on partial failure", async () => {
    vi.spyOn(api, "post")
      .mockResolvedValueOnce(PREVIEW as never)
      .mockResolvedValueOnce({
        submitted: [{ symbol: "BBB", order_id: "o-1", side: "SELL", qty: 100, status: "SUBMITTED" }],
        rejected: [{ symbol: "AAA", side: "BUY", qty: 100, reason: "RiskViolationError: 超上限" }],
      } as never)
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))
    fireEvent.click(await screen.findByRole("button", { name: /确认执行/ }))

    expect(await screen.findByText(/已提交 1 笔/)).toBeInTheDocument()
    expect(screen.getByText(/被拒 1 笔/)).toBeInTheDocument()
    expect(screen.getByText(/RiskViolationError: 超上限/)).toBeInTheDocument()
  })

  it("surfaces a stale-token rejection as an actionable error", async () => {
    vi.spyOn(api, "post")
      .mockResolvedValueOnce(PREVIEW as never)
      .mockRejectedValueOnce(new Error("持仓在预览与执行之间发生了变化，请重新预览"))
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))
    fireEvent.click(await screen.findByRole("button", { name: /确认执行/ }))

    expect(await screen.findByText(/请重新预览后再试/)).toBeInTheDocument()
  })

  it("tells the user when nothing needs trading", async () => {
    vi.spyOn(api, "post").mockResolvedValue({ ...PREVIEW, legs: [] } as never)
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /预览调仓/ }))

    expect(await screen.findByText(/无需调仓/)).toBeInTheDocument()
  })
})
