import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"

// api 必须在 import hook 之前打桩
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}))

import { api } from "@/lib/api"
import { useFullValidationAsync } from "@/hooks/useFullValidation"
import type { FullValidationRequest } from "@/hooks/useFullValidation"

const REQ = {
  strategy_name: "double_ma",
  symbol: "AAPL",
  market: "US",
  frequency: "1d",
  start_date: "2024-01-01",
  end_date: "2024-06-30",
  initial_cash: 100000,
  params: {},
} as unknown as FullValidationRequest

const DONE = { run_id: "r1", requested_steps: [], steps: {}, grade: {} }

describe("useFullValidationAsync", () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset()
    vi.mocked(api.get).mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it("submits then polls until done", async () => {
    vi.mocked(api.post).mockResolvedValue({ task_id: "t1", status: "queued" })
    vi.mocked(api.get)
      .mockResolvedValueOnce({ task_id: "t1", status: "running", result: null, error: null })
      .mockResolvedValueOnce({ task_id: "t1", status: "done", result: DONE, error: null })

    const { result } = renderHook(() => useFullValidationAsync())
    await act(async () => { await result.current.start(REQ) })

    await waitFor(() => expect(result.current.result).toEqual(DONE), { timeout: 3000 })
    expect(result.current.isPending).toBe(false)
  })

  it("surfaces a task-level error instead of hanging", async () => {
    vi.mocked(api.post).mockResolvedValue({ task_id: "t1", status: "queued" })
    vi.mocked(api.get).mockResolvedValue({
      task_id: "t1", status: "error", result: null, error: "数据不足",
    })

    const { result } = renderHook(() => useFullValidationAsync())
    await act(async () => { await result.current.start(REQ) })

    await waitFor(() => expect(result.current.error?.message).toBe("数据不足"))
    expect(result.current.isPending).toBe(false)
  })

  it("does not poll when submission itself fails", async () => {
    // 配置非法（400）或队列不可用（503）应当直接结束，不进轮询
    vi.mocked(api.post).mockRejectedValue(new Error("未知策略"))

    const { result } = renderHook(() => useFullValidationAsync())
    await act(async () => { await result.current.start(REQ) })

    expect(result.current.error?.message).toBe("未知策略")
    expect(result.current.isPending).toBe(false)
    expect(api.get).not.toHaveBeenCalled()
  })

  it("stops polling after unmount", async () => {
    // 卸载后继续轮询会对着已销毁的组件 setState
    vi.mocked(api.post).mockResolvedValue({ task_id: "t1", status: "queued" })
    vi.mocked(api.get).mockResolvedValue({
      task_id: "t1", status: "running", result: null, error: null,
    })

    const { result, unmount } = renderHook(() => useFullValidationAsync())
    await act(async () => { await result.current.start(REQ) })
    const callsAtUnmount = vi.mocked(api.get).mock.calls.length
    unmount()

    await new Promise((r) => setTimeout(r, 1500))
    expect(vi.mocked(api.get).mock.calls.length).toBe(callsAtUnmount)
  })
})
