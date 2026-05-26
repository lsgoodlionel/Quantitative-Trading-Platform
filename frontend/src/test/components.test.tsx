import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { PnlCell, PercentCell } from "@/components/ui/PnlCell"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { Modal } from "@/components/ui/Modal"

// ── Spinner ──────────────────────────────────────────────────────

describe("Spinner", () => {
  it("renders with role=status", () => {
    render(<Spinner />)
    expect(screen.getByRole("status")).toBeInTheDocument()
  })

  it("has aria-label Loading", () => {
    render(<Spinner />)
    expect(screen.getByLabelText("Loading")).toBeInTheDocument()
  })

  it("applies sm size class", () => {
    render(<Spinner size="sm" />)
    const el = screen.getByRole("status")
    expect(el.className).toContain("h-4")
  })

  it("applies lg size class", () => {
    render(<Spinner size="lg" />)
    const el = screen.getByRole("status")
    expect(el.className).toContain("h-10")
  })
})

// ── EmptyState ───────────────────────────────────────────────────

describe("EmptyState", () => {
  it("renders title", () => {
    render(<EmptyState title="No data found" />)
    expect(screen.getByText("No data found")).toBeInTheDocument()
  })

  it("renders optional description", () => {
    render(<EmptyState title="Empty" description="Try again later" />)
    expect(screen.getByText("Try again later")).toBeInTheDocument()
  })

  it("renders optional action", () => {
    render(<EmptyState title="Empty" action={<button>Refresh</button>} />)
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument()
  })

  it("omits description when not provided", () => {
    render(<EmptyState title="Empty" />)
    // Only the title paragraph exists — no second paragraph for description
    const paragraphs = screen.getAllByRole("paragraph")
    expect(paragraphs).toHaveLength(1)
    expect(paragraphs[0]).toHaveTextContent("Empty")
  })
})

// ── PnlCell ──────────────────────────────────────────────────────

describe("PnlCell", () => {
  it("shows dash for null value", () => {
    render(<PnlCell value={null} />)
    expect(screen.getByText("—")).toBeInTheDocument()
  })

  it("shows positive value with + prefix", () => {
    render(<PnlCell value={123.45} />)
    expect(screen.getByText("+123.45")).toBeInTheDocument()
  })

  it("shows negative value without + prefix", () => {
    render(<PnlCell value={-50.0} />)
    expect(screen.getByText("-50.00")).toBeInTheDocument()
  })

  it("shows zero as positive", () => {
    render(<PnlCell value={0} />)
    expect(screen.getByText("+0.00")).toBeInTheDocument()
  })

  it("appends suffix", () => {
    render(<PnlCell value={5.5} suffix="%" />)
    expect(screen.getByText("+5.50%")).toBeInTheDocument()
  })

  it("applies green color for positive", () => {
    render(<PnlCell value={10} />)
    const el = screen.getByText("+10.00")
    expect(el.className).toContain("text-[#3fb950]")
  })

  it("applies red color for negative", () => {
    render(<PnlCell value={-10} />)
    const el = screen.getByText("-10.00")
    expect(el.className).toContain("text-[#f85149]")
  })
})

describe("PercentCell", () => {
  it("appends percent sign", () => {
    render(<PercentCell value={3.14} />)
    expect(screen.getByText("+3.14%")).toBeInTheDocument()
  })
})

// ── StatusBadge ───────────────────────────────────────────────────

describe("StatusBadge", () => {
  it("renders filled label in Chinese", () => {
    render(<StatusBadge status="filled" />)
    expect(screen.getByText("已成交")).toBeInTheDocument()
  })

  it("renders rejected with red style", () => {
    render(<StatusBadge status="rejected" />)
    const el = screen.getByText("已拒绝")
    expect(el.className).toContain("text-[#f85149]")
  })

  it("renders pending_submit", () => {
    render(<StatusBadge status="pending_submit" />)
    expect(screen.getByText("待提交")).toBeInTheDocument()
  })

  it("renders cancelled", () => {
    render(<StatusBadge status="cancelled" />)
    expect(screen.getByText("已撤销")).toBeInTheDocument()
  })
})

// ── Modal ──────────────────────────────────────────────────────────

describe("Modal", () => {
  it("renders nothing when closed", () => {
    render(<Modal open={false} onClose={() => {}} title="Test"><p>Content</p></Modal>)
    expect(screen.queryByText("Test")).not.toBeInTheDocument()
  })

  it("renders title and children when open", () => {
    render(<Modal open={true} onClose={() => {}} title="My Modal"><p>Body text</p></Modal>)
    expect(screen.getByText("My Modal")).toBeInTheDocument()
    expect(screen.getByText("Body text")).toBeInTheDocument()
  })

  it("calls onClose when close button clicked", () => {
    let closed = false
    render(<Modal open={true} onClose={() => { closed = true }} title="T"><span /></Modal>)
    fireEvent.click(screen.getByLabelText("关闭"))
    expect(closed).toBe(true)
  })

  it("has role=dialog when open", () => {
    render(<Modal open={true} onClose={() => {}} title="T"><span /></Modal>)
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
})
