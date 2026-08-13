import { describe, it, expect, beforeEach, vi, afterEach } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ToastProvider } from "@/components/ui/Toast"
import {
  addToWatchlist,
  clearWatchlist,
  loadWatchlist,
  removeFromWatchlist,
} from "@/lib/watchlist"
import {
  formatPayloadValue,
  formatRelativeTime,
  knownNotificationTypes,
  notificationMeta,
} from "@/lib/notificationMeta"
import { NotificationRow } from "@/pages/notifications/NotificationRow"
import { SelectionActions } from "@/pages/screener/SelectionActions"
import {
  candidateKey,
  isAllSelected,
  toggleAllKeys,
  toggleKey,
} from "@/pages/screener/selection"
import type { NotificationItem } from "@/hooks/useNotifications"
import type { ScreenerCandidate } from "@/hooks/useScreener"

// ── 自选池（localStorage）────────────────────────────────────────

describe("watchlist store", () => {
  beforeEach(() => clearWatchlist())

  it("adds items and reports how many were new", () => {
    const { list, added } = addToWatchlist([
      { symbol: "AAPL", market: "US", name: "Apple" },
      { symbol: "MSFT", market: "US", name: "Microsoft" },
    ])

    expect(added).toBe(2)
    expect(list.map((i) => i.symbol)).toEqual(["AAPL", "MSFT"])
  })

  it("deduplicates by symbol+market and reports 0 added", () => {
    addToWatchlist([{ symbol: "AAPL", market: "US", name: "Apple" }])

    const { list, added } = addToWatchlist([{ symbol: "AAPL", market: "US", name: "Apple" }])

    expect(added).toBe(0)
    expect(list).toHaveLength(1)
  })

  it("treats same symbol in different markets as distinct", () => {
    const { added } = addToWatchlist([
      { symbol: "0700", market: "HK", name: "腾讯" },
      { symbol: "0700", market: "US", name: "other" },
    ])

    expect(added).toBe(2)
  })

  it("persists across reads", () => {
    addToWatchlist([{ symbol: "NVDA", market: "US", name: "Nvidia" }])
    expect(loadWatchlist()).toHaveLength(1)
  })

  it("removes a single item", () => {
    addToWatchlist([
      { symbol: "AAPL", market: "US", name: "Apple" },
      { symbol: "MSFT", market: "US", name: "Microsoft" },
    ])

    const rest = removeFromWatchlist({ symbol: "AAPL", market: "US" })

    expect(rest.map((i) => i.symbol)).toEqual(["MSFT"])
  })

  it("returns empty array when storage holds corrupt data", () => {
    localStorage.setItem("qb_watchlist", "{not json")
    expect(loadWatchlist()).toEqual([])
  })

  it("drops entries with the wrong shape", () => {
    localStorage.setItem("qb_watchlist", JSON.stringify([{ symbol: "AAPL" }, 42]))
    expect(loadWatchlist()).toEqual([])
  })
})

// ── 通知展示元数据 ──────────────────────────────────────────────

describe("notificationMeta", () => {
  it("maps the five new V3 event types to dedicated labels", () => {
    for (const type of [
      "backtest_done",
      "hyperopt_done",
      "mining_done",
      "data_source_degraded",
      "reconcile_diff",
    ]) {
      expect(notificationMeta(type).label).not.toBe("通知")
    }
  })

  it("falls back to a generic label for unknown types", () => {
    expect(notificationMeta("brand_new_type").label).toBe("通知")
  })

  it("exposes every known type", () => {
    expect(knownNotificationTypes()).toContain("risk_alert")
    expect(knownNotificationTypes()).toContain("reconcile_diff")
  })

  it("formats relative time by bucket", () => {
    const now = 1_000_000_000_000
    const sec = now / 1000
    expect(formatRelativeTime(sec, now)).toBe("刚刚")
    expect(formatRelativeTime(sec - 120, now)).toBe("2 分钟前")
    expect(formatRelativeTime(sec - 3 * 3600, now)).toBe("3 小时前")
    expect(formatRelativeTime(sec - 2 * 86400, now)).toBe("2 天前")
  })

  it("treats future timestamps as 刚刚 rather than negative time", () => {
    const now = 1_000_000_000_000
    expect(formatRelativeTime(now / 1000 + 500, now)).toBe("刚刚")
  })

  it("stringifies payload values without [object Object]", () => {
    expect(formatPayloadValue({ a: 1 })).toBe('{"a":1}')
    expect(formatPayloadValue(null)).toBe("—")
    expect(formatPayloadValue(3.5)).toBe("3.5")
  })
})

// ── 通知行组件 ──────────────────────────────────────────────────

function makeNotification(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "n1",
    type: "backtest_done",
    title: "回测完成 · double_ma",
    symbol: "AAPL",
    market: "US",
    payload: { 夏普比率: 1.23 },
    is_read: false,
    created_at: Date.now() / 1000,
    ...overrides,
  }
}

describe("NotificationRow", () => {
  it("renders title, label and payload", () => {
    render(
      <ul>
        <NotificationRow item={makeNotification()} onToggleRead={vi.fn()} onDelete={vi.fn()} />
      </ul>,
    )

    expect(screen.getByText("回测完成 · double_ma")).toBeInTheDocument()
    expect(screen.getByText(/回测完成$/)).toBeInTheDocument()
    expect(screen.getByText("1.23")).toBeInTheDocument()
  })

  it("marks unread rows with an unread dot", () => {
    render(
      <ul>
        <NotificationRow item={makeNotification()} onToggleRead={vi.fn()} onDelete={vi.fn()} />
      </ul>,
    )

    expect(screen.getByLabelText("未读")).toBeInTheDocument()
    expect(screen.getByTestId("notification-row")).toHaveAttribute("data-read", "false")
  })

  it("offers 标为未读 when already read", () => {
    render(
      <ul>
        <NotificationRow
          item={makeNotification({ is_read: true })}
          onToggleRead={vi.fn()}
          onDelete={vi.fn()}
        />
      </ul>,
    )

    expect(screen.getByRole("button", { name: "标为未读" })).toBeInTheDocument()
  })

  it("invokes callbacks on toggle and delete", () => {
    const onToggleRead = vi.fn()
    const onDelete = vi.fn()
    const item = makeNotification()
    render(
      <ul>
        <NotificationRow item={item} onToggleRead={onToggleRead} onDelete={onDelete} />
      </ul>,
    )

    fireEvent.click(screen.getByRole("button", { name: "标为已读" }))
    fireEvent.click(screen.getByRole("button", { name: /删除通知/ }))

    expect(onToggleRead).toHaveBeenCalledWith(item)
    expect(onDelete).toHaveBeenCalledWith(item)
  })

  it("renders unknown event types without crashing", () => {
    render(
      <ul>
        <NotificationRow
          item={makeNotification({ type: "future_type", payload: {} })}
          onToggleRead={vi.fn()}
          onDelete={vi.fn()}
        />
      </ul>,
    )

    expect(screen.getByText(/通知$/)).toBeInTheDocument()
  })
})

// ── 选中动作条 ──────────────────────────────────────────────────

function makeCandidate(symbol: string): ScreenerCandidate {
  return {
    symbol,
    market: "US",
    name: `${symbol} Inc`,
    sector: "Tech",
    price: 100,
    change_pct: 1,
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

function renderSelection(selected: ScreenerCandidate[], onClear = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/screener"]}>
          <SelectionActions selected={selected} market="US" onClear={onClear} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
  return { ...utils, onClear }
}

describe("screener selection helpers", () => {
  it("builds market-scoped keys so the same code in two markets stays distinct", () => {
    expect(candidateKey({ symbol: "0700", market: "HK" })).not.toBe(
      candidateKey({ symbol: "0700", market: "US" }),
    )
  })

  it("toggles a key on and off without mutating the input set", () => {
    const original = new Set<string>()

    const withKey = toggleKey(original, "US-AAPL")
    const withoutKey = toggleKey(withKey, "US-AAPL")

    expect(original.size).toBe(0)
    expect(withKey.has("US-AAPL")).toBe(true)
    expect(withoutKey.has("US-AAPL")).toBe(false)
  })

  it("does not report an empty result set as fully selected", () => {
    expect(isAllSelected([], new Set())).toBe(false)
  })

  it("selects all then clears all", () => {
    const rows = [makeCandidate("AAPL"), makeCandidate("MSFT")]

    const all = toggleAllKeys(rows, new Set())
    const cleared = toggleAllKeys(rows, all)

    expect(all.size).toBe(2)
    expect(isAllSelected(rows, all)).toBe(true)
    expect(cleared.size).toBe(0)
  })

  it("select-all fills in the remainder when only part is selected", () => {
    const rows = [makeCandidate("AAPL"), makeCandidate("MSFT")]
    const partial = new Set([candidateKey(rows[0])])

    expect(toggleAllKeys(rows, partial).size).toBe(2)
  })
})

describe("SelectionActions", () => {
  beforeEach(() => clearWatchlist())
  afterEach(() => vi.restoreAllMocks())

  it("renders nothing when no candidate is selected", () => {
    renderSelection([])
    expect(screen.queryByTestId("selection-actions")).not.toBeInTheDocument()
  })

  it("shows the selected count and the three actions", () => {
    renderSelection([makeCandidate("AAPL"), makeCandidate("MSFT")])

    const bar = screen.getByTestId("selection-actions")
    expect(within(bar).getByText("已选 2 只")).toBeInTheDocument()
    expect(within(bar).getByRole("button", { name: /送组合优化/ })).toBeInTheDocument()
    expect(within(bar).getByRole("button", { name: /加自选池/ })).toBeInTheDocument()
    expect(within(bar).getByRole("button", { name: /批量回测/ })).toBeInTheDocument()
  })

  it("adds selection to the watchlist and reports the count", () => {
    renderSelection([makeCandidate("AAPL"), makeCandidate("MSFT")])

    fireEvent.click(screen.getByRole("button", { name: /加自选池/ }))

    expect(loadWatchlist().map((i) => i.symbol)).toEqual(["AAPL", "MSFT"])
    expect(screen.getByText("已加入自选池 2 只")).toBeInTheDocument()
  })

  it("reports duplicates separately when re-adding", () => {
    addToWatchlist([{ symbol: "AAPL", market: "US", name: "AAPL Inc" }])
    renderSelection([makeCandidate("AAPL"), makeCandidate("MSFT")])

    fireEvent.click(screen.getByRole("button", { name: /加自选池/ }))

    expect(screen.getByText("已加入自选池 1 只（1 只已存在）")).toBeInTheDocument()
  })

  it("refuses to send a single symbol to the optimizer", () => {
    renderSelection([makeCandidate("AAPL")])

    fireEvent.click(screen.getByRole("button", { name: /送组合优化/ }))

    expect(screen.getByText("组合优化至少需要 2 个标的")).toBeInTheDocument()
  })

  it("opens the batch backtest modal", () => {
    renderSelection([makeCandidate("AAPL"), makeCandidate("MSFT")])

    fireEvent.click(screen.getByRole("button", { name: /批量回测/ }))

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByText("批量回测 · 2 只标的")).toBeInTheDocument()
  })

  it("blocks batch backtest above the backend cap", () => {
    const many = Array.from({ length: 51 }, (_, i) => makeCandidate(`S${i}`))
    renderSelection(many)

    fireEvent.click(screen.getByRole("button", { name: /批量回测/ }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(screen.getByText(/批量回测最多 50 个标的/)).toBeInTheDocument()
  })

  it("clears the selection via the clear button", () => {
    const { onClear } = renderSelection([makeCandidate("AAPL")])

    fireEvent.click(screen.getByRole("button", { name: "清空选择" }))

    expect(onClear).toHaveBeenCalled()
  })
})
