/**
 * Copilot 侧栏测试（V3 Wave B-c / I1）
 *
 * 对应契约 docs/contracts/waveBc-copilot.md §四 验收 4：
 * 草稿卡片显示完整参数；未配置模型时的引导块。
 */
import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import { DraftCard } from "@/components/copilot/DraftCard"
import { ResultCard, formatValue } from "@/components/copilot/ResultCard"
import { SetupGuidance } from "@/components/copilot/SetupGuidance"
import {
  CopilotTranscript,
  type CopilotTurn,
} from "@/components/copilot/CopilotTranscript"
import { describeErrorKind, draftTone, type CopilotDraft } from "@/hooks/useCopilot"

const ORDER_DRAFT: CopilotDraft = {
  draft_id: "d-1",
  tool: "draft_order",
  action: "order",
  title: "买入 AAPL 100 股",
  summary: "确认后将通过实盘下单端点提交。",
  fields: [
    { label: "标的", value: "AAPL（US）", emphasis: false },
    { label: "方向", value: "买入", emphasis: true },
    { label: "数量", value: "100 股", emphasis: true },
    { label: "订单类型", value: "限价单", emphasis: false },
    { label: "价格", value: "限价 190.0000", emphasis: false },
    { label: "预估金额", value: "19,000.00", emphasis: true },
  ],
  params: { symbol: "AAPL", market: "US", side: "BUY", qty: 100, limit_price: 190 },
  endpoint: "POST /api/v1/orders",
  method: "POST",
  legs: [],
  warnings: [],
}

function renderDraft(overrides: Partial<Parameters<typeof DraftCard>[0]> = {}) {
  const handlers = {
    onConfirm: vi.fn<Parameters<typeof DraftCard>[0]["onConfirm"]>(),
    onCancel: vi.fn<Parameters<typeof DraftCard>[0]["onCancel"]>(),
  }
  render(
    <DraftCard
      draft={ORDER_DRAFT}
      canExecute
      busy={false}
      outcome={null}
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

// ── 展示辅助 ──────────────────────────────────────────────────────────────────

describe("describeErrorKind", () => {
  it("explains unparsable tool arguments in plain words", () => {
    expect(describeErrorKind("invalid_tool_arguments")).toContain("无法解析")
  })

  it("explains an unreachable provider", () => {
    expect(describeErrorKind("provider_unavailable")).toContain("连不上")
  })

  it("returns null when nothing went wrong", () => {
    expect(describeErrorKind(null)).toBeNull()
  })
})

describe("draftTone", () => {
  it("marks orders as the most dangerous action", () => {
    expect(draftTone("order")).toBe("danger")
    expect(draftTone("rebalance")).toBe("warn")
  })
})

describe("formatValue", () => {
  it("renders null-ish values as a dash instead of blank", () => {
    expect(formatValue(null)).toBe("-")
    expect(formatValue(undefined)).toBe("-")
  })

  it("keeps integers readable and rounds floats", () => {
    expect(formatValue(1234567)).toBe("1,234,567")
    expect(formatValue(1.23456)).toBe("1.23")
  })
})

// ── 草稿卡片 ─────────────────────────────────────────────────────────────────

describe("DraftCard", () => {
  it("shows every parameter, not just the symbol", () => {
    // 只写「买入 AAPL」而不写数量的确认按钮，等于没有确认
    renderDraft()

    for (const text of ["AAPL（US）", "买入", "100 股", "限价单", "限价 190.0000", "19,000.00"]) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
  })

  it("shows which existing endpoint the execution goes through", () => {
    renderDraft()

    expect(screen.getByText(/POST \/api\/v1\/orders/)).toBeInTheDocument()
  })

  it("marks the draft as pending confirmation", () => {
    renderDraft()

    expect(screen.getByText("待确认")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "确认执行" })).toBeEnabled()
  })

  it("hands the whole draft back on confirm", () => {
    const handlers = renderDraft()

    fireEvent.click(screen.getByRole("button", { name: "确认执行" }))

    expect(handlers.onConfirm).toHaveBeenCalledWith(ORDER_DRAFT)
  })

  it("executes nothing when cancelled", () => {
    const handlers = renderDraft()

    fireEvent.click(screen.getByRole("button", { name: "取消" }))

    expect(handlers.onConfirm).not.toHaveBeenCalled()
    expect(handlers.onCancel).toHaveBeenCalledWith(ORDER_DRAFT)
    expect(screen.getByRole("status").textContent).toContain("未执行任何操作")
  })

  it("disables execution for read-only roles and says why", () => {
    renderDraft({ canExecute: false })

    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled()
    expect(screen.getByText("需要交易员及以上角色")).toBeInTheDocument()
  })

  it("blocks a second click while the first is still running", () => {
    renderDraft({ busy: true })

    expect(screen.getByRole("button", { name: "执行中…" })).toBeDisabled()
  })

  it("replaces the buttons with the outcome once executed", () => {
    renderDraft({ outcome: { ok: true, message: "已执行，请到订单页查看结果。" } })

    expect(screen.queryByRole("button", { name: "确认执行" })).toBeNull()
    expect(screen.getByRole("status").textContent).toContain("已执行")
  })

  it("surfaces the failure reason verbatim", () => {
    renderDraft({ outcome: { ok: false, message: "执行失败：Risk violation: 超出单笔上限" } })

    expect(screen.getByRole("status").textContent).toContain("Risk violation")
  })

  it("lists rebalance legs with quantities and amounts", () => {
    renderDraft({
      draft: {
        ...ORDER_DRAFT,
        draft_id: "d-2",
        action: "rebalance",
        legs: [{ symbol: "AAPL", delta_qty: 12, price: 190, delta_value: 2280 }],
      },
    })

    const row = screen.getByText("AAPL").closest("tr")
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText("12")).toBeInTheDocument()
    expect(within(row as HTMLElement).getByText("2280")).toBeInTheDocument()
  })

  it("shows warnings that came with the draft", () => {
    renderDraft({ draft: { ...ORDER_DRAFT, warnings: ["未取到最新行情，预估金额不可用。"] } })

    expect(screen.getByText(/未取到最新行情/)).toBeInTheDocument()
  })
})

// ── 未配置引导 ───────────────────────────────────────────────────────────────

describe("SetupGuidance", () => {
  it("offers a direct link to the model settings page", () => {
    render(
      <MemoryRouter>
        <SetupGuidance text="尚未配置任何可用的模型服务。" setupUrl="/settings/models" />
      </MemoryRouter>,
    )

    expect(screen.getByText(/尚未配置任何可用的模型服务/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute(
      "href",
      "/settings/models",
    )
  })

  it("falls back to the default settings path", () => {
    render(
      <MemoryRouter>
        <SetupGuidance text="还没配" setupUrl={null} />
      </MemoryRouter>,
    )

    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute(
      "href",
      "/settings/models",
    )
  })
})

// ── 结果卡片 ─────────────────────────────────────────────────────────────────

describe("ResultCard", () => {
  it("renders a quote card", () => {
    render(
      <ResultCard
        card={{
          kind: "quote",
          title: "get_quote",
          data: { open: 1, high: 2, low: 0.5, close: 1.5, volume: 1000 },
        }}
      />,
    )

    expect(screen.getByText("最新行情")).toBeInTheDocument()
    expect(screen.getByText("1.50")).toBeInTheDocument()
  })

  it("renders positions and says so when there are none", () => {
    render(<ResultCard card={{ kind: "positions", title: "", data: { positions: [] } }} />)

    expect(screen.getByText("没有持仓")).toBeInTheDocument()
  })

  it("falls back to a key-value dump for unknown card kinds", () => {
    // 不认识的 kind 不该让数据消失
    render(<ResultCard card={{ kind: "mystery", title: "怪东西", data: { alpha: 42 } }} />)

    expect(screen.getByText("42")).toBeInTheDocument()
  })
})

// ── 消息流 ───────────────────────────────────────────────────────────────────

describe("CopilotTranscript", () => {
  const baseProps = {
    canExecute: true,
    executingDraftId: null,
    draftOutcomes: {},
    onConfirmDraft: vi.fn(),
    onCancelDraft: vi.fn(),
  }

  it("suggests example questions when the conversation is empty", () => {
    render(<CopilotTranscript turns={[]} {...baseProps} />)

    expect(screen.getByText(/AAPL 现在多少钱/)).toBeInTheDocument()
  })

  it("renders the setup guidance instead of a bare error", () => {
    const turns: CopilotTurn[] = [
      {
        id: "t1",
        role: "assistant",
        text: "尚未配置任何可用的模型服务。",
        needsSetup: true,
        setupUrl: "/settings/models",
      },
    ]

    render(
      <MemoryRouter>
        <CopilotTranscript turns={turns} {...baseProps} />
      </MemoryRouter>,
    )

    expect(screen.getByRole("link", { name: "去配置" })).toBeInTheDocument()
  })

  it("flags a reply cut short by the tool round limit", () => {
    const turns: CopilotTurn[] = [
      { id: "t1", role: "assistant", text: "先说到这里", truncated: true },
    ]

    render(<CopilotTranscript turns={turns} {...baseProps} />)

    expect(screen.getByText(/轮次上限/)).toBeInTheDocument()
  })

  it("shows an alert for unparsable tool arguments", () => {
    const turns: CopilotTurn[] = [
      { id: "t1", role: "assistant", text: "出错了", errorKind: "invalid_tool_arguments" },
    ]

    render(<CopilotTranscript turns={turns} {...baseProps} />)

    expect(screen.getByRole("alert").textContent).toContain("无法解析")
  })

  it("renders drafts after the text of the same turn", () => {
    const turns: CopilotTurn[] = [
      { id: "t1", role: "user", text: "买 100 股 AAPL" },
      { id: "t2", role: "assistant", text: "已生成草稿", drafts: [ORDER_DRAFT] },
    ]

    render(<CopilotTranscript turns={turns} {...baseProps} />)

    expect(screen.getByLabelText("我的提问")).toBeInTheDocument()
    expect(screen.getByLabelText("待确认草稿：买入 AAPL 100 股")).toBeInTheDocument()
    expect(screen.getByText("100 股")).toBeInTheDocument()
  })
})
