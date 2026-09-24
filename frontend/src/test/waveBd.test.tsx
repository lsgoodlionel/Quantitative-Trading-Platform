// Wave B-d：命令面板 + 全局标的上下文（H3）· Playbook（H4）
import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { isPaletteShortcut, isTypingTarget } from "@/components/palette/shortcut"
import {
  ALL_COMMANDS,
  PAGE_COMMANDS,
  SYMBOL_COMMANDS,
  ACTION_COMMANDS,
} from "@/components/palette/commandRegistry"
import {
  filterCommands,
  groupCommands,
  resolveCommandTarget,
  withQuery,
} from "@/components/palette/commandSearch"
import { CommandPaletteHost } from "@/components/palette/CommandPaletteHost"
import { SymbolProvider, useSymbolContext } from "@/contexts/SymbolContext"
import { PLAYBOOKS, getPlaybook } from "@/data/playbooks"
import { PlaybookHub } from "@/components/workflow/PlaybookHub"
import {
  loadPlaybookProgress,
  togglePlaybookStep,
  resetPlaybookProgress,
} from "@/hooks/useWorkflowStorage"

// ── 公共探针 ──────────────────────────────────────────────────

/** 把当前 location 打到 DOM 上，便于断言跳转目标（含 ?tab=） */
function LocationProbe() {
  const { pathname, search } = useLocation()
  return <div data-testid="location">{pathname + search}</div>
}

function locationText(): string {
  return screen.getByTestId("location").textContent ?? ""
}

function pressPaletteShortcut(target: Element | Document = document) {
  fireEvent.keyDown(target, { key: "k", metaKey: true })
}

/** 渲染带命令面板的最小应用：面板挂在路由内部，跳转结果由探针读出 */
function renderApp(entry = "/") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <SymbolProvider>
        <CommandPaletteHost enabled />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </SymbolProvider>
    </MemoryRouter>,
  )
}

function paletteInput(): HTMLInputElement {
  return screen.getByPlaceholderText(/搜索/) as HTMLInputElement
}

function queryPalette(): HTMLElement | null {
  return screen.queryByRole("dialog", { name: /命令面板/ })
}

beforeEach(() => {
  localStorage.clear()
  document.body.style.overflow = ""
})

// ── 1. 快捷键判定（H3 · 1.1）──────────────────────────────────

describe("命令面板快捷键判定", () => {
  it("⌘K / Ctrl+K 命中，单独按 K 不命中", () => {
    expect(isPaletteShortcut({ key: "k", metaKey: true } as KeyboardEvent)).toBe(true)
    expect(isPaletteShortcut({ key: "k", ctrlKey: true } as KeyboardEvent)).toBe(true)
    expect(isPaletteShortcut({ key: "K", metaKey: true } as KeyboardEvent)).toBe(true)
    expect(isPaletteShortcut({ key: "k" } as KeyboardEvent)).toBe(false)
    expect(isPaletteShortcut({ key: "j", metaKey: true } as KeyboardEvent)).toBe(false)
  })

  it("识别输入态元素：input / textarea / contenteditable", () => {
    const input = document.createElement("input")
    const textarea = document.createElement("textarea")
    const editable = document.createElement("div")
    editable.setAttribute("contenteditable", "true")
    const plain = document.createElement("div")
    const notEditable = document.createElement("div")
    notEditable.setAttribute("contenteditable", "false")

    expect(isTypingTarget(input)).toBe(true)
    expect(isTypingTarget(textarea)).toBe(true)
    expect(isTypingTarget(editable)).toBe(true)
    expect(isTypingTarget(plain)).toBe(false)
    expect(isTypingTarget(notEditable)).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })

  it("contenteditable 内部的子元素也算输入态", () => {
    const editable = document.createElement("div")
    editable.setAttribute("contenteditable", "true")
    const child = document.createElement("span")
    editable.appendChild(child)
    document.body.appendChild(editable)

    expect(isTypingTarget(child)).toBe(true)

    document.body.removeChild(editable)
  })
})

// ── 2. 面板开关（H3 · 1.1）────────────────────────────────────

describe("命令面板开关", () => {
  it("⌘K 唤起面板", () => {
    renderApp()
    expect(queryPalette()).toBeNull()

    pressPaletteShortcut()

    expect(queryPalette()).not.toBeNull()
  })

  it("⌘K 在 input 内不触发", () => {
    renderApp()
    const input = document.createElement("input")
    document.body.appendChild(input)

    pressPaletteShortcut(input)

    expect(queryPalette()).toBeNull()
    document.body.removeChild(input)
  })

  it("⌘K 在 textarea 内不触发", () => {
    renderApp()
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)

    pressPaletteShortcut(textarea)

    expect(queryPalette()).toBeNull()
    document.body.removeChild(textarea)
  })

  it("⌘K 在 contenteditable 内不触发", () => {
    renderApp()
    const editable = document.createElement("div")
    editable.setAttribute("contenteditable", "true")
    document.body.appendChild(editable)

    pressPaletteShortcut(editable)

    expect(queryPalette()).toBeNull()
    document.body.removeChild(editable)
  })

  it("Escape 关闭面板", () => {
    renderApp()
    pressPaletteShortcut()
    expect(queryPalette()).not.toBeNull()

    fireEvent.keyDown(paletteInput(), { key: "Escape" })

    expect(queryPalette()).toBeNull()
  })

  it("Escape 不穿透到底层页面的 Escape 处理", () => {
    const outer = vi.fn()
    window.addEventListener("keydown", outer)
    renderApp()
    pressPaletteShortcut()
    outer.mockClear()

    fireEvent.keyDown(paletteInput(), { key: "Escape" })

    expect(outer).not.toHaveBeenCalled()
    window.removeEventListener("keydown", outer)
  })

  it("面板打开时锁滚动，关闭后恢复原值", () => {
    document.body.style.overflow = "auto"
    renderApp()

    pressPaletteShortcut()
    expect(document.body.style.overflow).toBe("hidden")

    fireEvent.keyDown(paletteInput(), { key: "Escape" })
    expect(document.body.style.overflow).toBe("auto")
  })

  it("未登录时快捷键不唤起面板", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <SymbolProvider>
          <CommandPaletteHost enabled={false} />
        </SymbolProvider>
      </MemoryRouter>,
    )

    pressPaletteShortcut()

    expect(queryPalette()).toBeNull()
  })
})

// ── 3. 条目搜索与分组（H3 · 1）────────────────────────────────

describe("命令条目", () => {
  it("三类条目都有，且 id 唯一", () => {
    expect(PAGE_COMMANDS.length).toBeGreaterThan(10)
    expect(SYMBOL_COMMANDS.length).toBeGreaterThan(0)
    expect(ACTION_COMMANDS.length).toBeGreaterThan(0)

    const ids = ALL_COMMANDS.map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it("页面条目全部指向真实路由前缀", () => {
    const known = [
      "/", "/market", "/screener", "/research", "/strategies", "/backtest",
      "/portfolio", "/trading", "/settings", "/settings/models",
      "/risk", "/alerts", "/notifications", "/lab",
    ]
    for (const cmd of PAGE_COMMANDS) {
      const path = cmd.to.split("?")[0]
      expect(known).toContain(path)
    }
  })

  it("中文子串匹配（不做拼音）", () => {
    const hits = filterCommands(ALL_COMMANDS, "优化")
    expect(hits.some((c) => c.to === "/portfolio?tab=optimizer")).toBe(true)

    // 拼音/首字母本期不支持，明确断言现状避免误以为能用
    expect(filterCommands(ALL_COMMANDS, "zuhe")).toHaveLength(0)
  })

  it("英文关键词匹配，大小写不敏感", () => {
    const hits = filterCommands(ALL_COMMANDS, "OPTIMIZER")
    expect(hits.some((c) => c.to === "/portfolio?tab=optimizer")).toBe(true)
  })

  it("空查询返回全部条目", () => {
    expect(filterCommands(ALL_COMMANDS, "   ")).toHaveLength(ALL_COMMANDS.length)
  })

  it("按类型分组，顺序固定为 页面 → 标的 → 动作", () => {
    const groups = groupCommands(ALL_COMMANDS)
    expect(groups.map((g) => g.kind)).toEqual(["page", "symbol", "action"])
    expect(groups.every((g) => g.items.length > 0)).toBe(true)
  })

  it("分组丢弃空组", () => {
    const groups = groupCommands(PAGE_COMMANDS)
    expect(groups.map((g) => g.kind)).toEqual(["page"])
  })
})

describe("跳转目标解析", () => {
  const ctx = { symbol: "TSLA", market: "US" } as const

  it("withQuery 保留已有查询参数", () => {
    expect(withQuery("/market?tab=quote", { symbol: "TSLA" })).toBe(
      "/market?tab=quote&symbol=TSLA",
    )
  })

  it("页面条目原样跳转（保留 ?tab=）", () => {
    const cmd = PAGE_COMMANDS.find((c) => c.to === "/portfolio?tab=optimizer")!
    expect(resolveCommandTarget(cmd, ctx)).toBe("/portfolio?tab=optimizer")
  })

  it("标的条目把自己的代码写进 URL", () => {
    const aapl = SYMBOL_COMMANDS.find((c) => c.symbol?.symbol === "AAPL")!
    const target = resolveCommandTarget(aapl, ctx)
    const params = new URLSearchParams(target.slice(target.indexOf("?")))
    expect(target.startsWith("/market")).toBe(true)
    expect(params.get("symbol")).toBe("AAPL")
    expect(params.get("market")).toBe("US")
  })

  it("带 withSymbol 的动作条目预置当前标的", () => {
    const cmd = ACTION_COMMANDS.find((c) => c.withSymbol)!
    const target = resolveCommandTarget(cmd, ctx)
    const params = new URLSearchParams(target.slice(target.indexOf("?")))
    expect(params.get("symbol")).toBe("TSLA")
    expect(params.get("market")).toBe("US")
  })
})

// ── 4. 面板内跳转（H3 · 零）──────────────────────────────────

describe("命令面板跳转", () => {
  it("页面条目跳到带正确 ?tab= 的地址", () => {
    renderApp()
    pressPaletteShortcut()

    fireEvent.change(paletteInput(), { target: { value: "组合优化" } })
    // ^ 锚定：动作条目「优化组合权重」的说明里也含「组合优化器」四个字
    fireEvent.click(screen.getByRole("option", { name: /^组合优化器/ }))

    expect(locationText()).toBe("/portfolio?tab=optimizer")
    expect(queryPalette()).toBeNull()
  })

  it("标的条目跳行情并把标的写进 URL", () => {
    renderApp()
    pressPaletteShortcut()

    fireEvent.change(paletteInput(), { target: { value: "TSLA" } })
    fireEvent.click(screen.getByRole("option", { name: /TSLA/ }))

    const text = locationText()
    expect(text.startsWith("/market")).toBe(true)
    const params = new URLSearchParams(text.slice(text.indexOf("?")))
    expect(params.get("symbol")).toBe("TSLA")
  })

  it("方向键 + 回车选中条目", () => {
    renderApp()
    pressPaletteShortcut()

    fireEvent.change(paletteInput(), { target: { value: "订单" } })
    fireEvent.keyDown(paletteInput(), { key: "Enter" })

    expect(locationText()).toBe("/trading?tab=orders")
  })

  it("无匹配时给出空状态且回车不跳转", () => {
    renderApp()
    pressPaletteShortcut()

    fireEvent.change(paletteInput(), { target: { value: "zzzz-not-exist" } })
    expect(screen.getByText(/没有匹配/)).toBeInTheDocument()

    fireEvent.keyDown(paletteInput(), { key: "Enter" })
    expect(locationText()).toBe("/")
  })
})

// ── 5. 全局标的上下文（H3 · 1.2）─────────────────────────────

function SymbolProbe() {
  const { symbol, market, setSymbol } = useSymbolContext()
  return (
    <div>
      <span data-testid="symbol">{symbol}</span>
      <span data-testid="market">{market}</span>
      <button onClick={() => setSymbol("NVDA")}>set-nvda</button>
      <button onClick={() => setSymbol("00700", "HK")}>set-hk</button>
      <button onClick={() => setSymbol("   ")}>set-empty</button>
      <LocationProbe />
    </div>
  )
}

function renderSymbolProbe(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <SymbolProvider>
        <Routes>
          <Route path="*" element={<SymbolProbe />} />
        </Routes>
      </SymbolProvider>
    </MemoryRouter>,
  )
}

describe("全局标的上下文", () => {
  it("初值从 URL 的 ?symbol= 读", () => {
    renderSymbolProbe("/market?symbol=tsla&market=US")
    expect(screen.getByTestId("symbol")).toHaveTextContent("TSLA")
    expect(screen.getByTestId("market")).toHaveTextContent("US")
  })

  it("URL 无标的时回落到默认标的", () => {
    renderSymbolProbe("/backtest")
    expect(screen.getByTestId("symbol")).toHaveTextContent("AAPL")
  })

  it("URL 上的市场非法时回落到 US", () => {
    renderSymbolProbe("/market?symbol=00700&market=XX")
    expect(screen.getByTestId("market")).toHaveTextContent("US")
  })

  it("setSymbol 后 URL 同步更新", () => {
    renderSymbolProbe("/market?tab=quote")

    fireEvent.click(screen.getByText("set-nvda"))

    expect(screen.getByTestId("symbol")).toHaveTextContent("NVDA")
    const params = new URLSearchParams(locationText().slice(locationText().indexOf("?")))
    expect(params.get("symbol")).toBe("NVDA")
    // 只改标的，不该把页面的 Tab 弄丢
    expect(params.get("tab")).toBe("quote")
  })

  it("setSymbol 可同时切市场", () => {
    renderSymbolProbe("/market")

    fireEvent.click(screen.getByText("set-hk"))

    expect(screen.getByTestId("market")).toHaveTextContent("HK")
    const params = new URLSearchParams(locationText().slice(locationText().indexOf("?")))
    expect(params.get("market")).toBe("HK")
  })

  it("空标的是空操作，不会把 URL 写脏", () => {
    renderSymbolProbe("/market?symbol=TSLA&market=US")

    fireEvent.click(screen.getByText("set-empty"))

    expect(screen.getByTestId("symbol")).toHaveTextContent("TSLA")
  })

  it("跨页面保持：在行情页选了标的，跳到没带 ?symbol= 的验证页也还在", () => {
    render(
      <MemoryRouter initialEntries={["/market?tab=quote"]}>
        <SymbolProvider>
          <Routes>
            <Route
              path="*"
              element={
                <>
                  <SymbolProbe />
                  <Link to="/backtest">go-backtest</Link>
                </>
              }
            />
          </Routes>
        </SymbolProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByText("set-nvda"))
    fireEvent.click(screen.getByText("go-backtest"))

    // 验证页 URL 上没有 ?symbol=，上下文这层缓存把选择带了过来
    expect(locationText()).toBe("/backtest")
    expect(screen.getByTestId("symbol")).toHaveTextContent("NVDA")
  })

  it("URL 是唯一真相：地址栏变化时上下文跟着变", () => {
    render(
      <MemoryRouter initialEntries={["/market?symbol=AAPL&market=US"]}>
        <SymbolProvider>
          <CommandPaletteHost enabled />
          <Routes>
            <Route path="*" element={<SymbolProbe />} />
          </Routes>
        </SymbolProvider>
      </MemoryRouter>,
    )
    expect(screen.getByTestId("symbol")).toHaveTextContent("AAPL")

    pressPaletteShortcut()
    fireEvent.change(paletteInput(), { target: { value: "MSFT" } })
    fireEvent.click(screen.getByRole("option", { name: /MSFT/ }))

    expect(screen.getByTestId("symbol")).toHaveTextContent("MSFT")
  })
})

// ── 6. Playbook 定义（H4）────────────────────────────────────

describe("Playbook 定义", () => {
  it("恰好三条路径", () => {
    expect(PLAYBOOKS).toHaveLength(3)
    expect(PLAYBOOKS.map((p) => p.id)).toEqual([
      "single-symbol",
      "discovery-portfolio",
      "factor-research",
    ])
  })

  it("每个步骤都有标题、说明、目标路由与完成判据", () => {
    for (const pb of PLAYBOOKS) {
      expect(pb.steps.length).toBeGreaterThanOrEqual(4)
      for (const step of pb.steps) {
        expect(step.title.length).toBeGreaterThan(0)
        expect(step.description.length).toBeGreaterThan(0)
        expect(step.criteria.length).toBeGreaterThan(0)
        expect(step.to.startsWith("/")).toBe(true)
      }
    }
  })

  it("同一条路径内步骤 id 唯一", () => {
    for (const pb of PLAYBOOKS) {
      const ids = pb.steps.map((s) => s.id)
      expect(new Set(ids).size).toBe(ids.length)
    }
  })

  it("getPlaybook 按 id 取，未知 id 回落到第一条", () => {
    expect(getPlaybook("factor-research").id).toBe("factor-research")
    expect(getPlaybook("bogus").id).toBe("single-symbol")
    expect(getPlaybook(null).id).toBe("single-symbol")
  })
})

// ── 7. Playbook 进度存储（H4 · 2.1）──────────────────────────

describe("Playbook 进度存储", () => {
  it("初始为空", () => {
    expect(loadPlaybookProgress()).toEqual({})
  })

  it("标记完成后持久化到 localStorage", () => {
    togglePlaybookStep("single-symbol", "pick-symbol")

    expect(loadPlaybookProgress()["single-symbol"]).toEqual(["pick-symbol"])
    expect(localStorage.getItem("qb_playbook_progress")).toContain("pick-symbol")
  })

  it("再次标记即取消完成", () => {
    togglePlaybookStep("single-symbol", "pick-symbol")
    togglePlaybookStep("single-symbol", "pick-symbol")

    expect(loadPlaybookProgress()["single-symbol"]).toEqual([])
  })

  it("重置只清掉指定路径", () => {
    togglePlaybookStep("single-symbol", "pick-symbol")
    togglePlaybookStep("factor-research", "build-factor")

    resetPlaybookProgress("single-symbol")

    const progress = loadPlaybookProgress()
    expect(progress["single-symbol"]).toBeUndefined()
    expect(progress["factor-research"]).toEqual(["build-factor"])
  })

  it("localStorage 里是脏数据时回落到空进度，而不是崩掉", () => {
    localStorage.setItem("qb_playbook_progress", "{not json")
    expect(loadPlaybookProgress()).toEqual({})

    localStorage.setItem("qb_playbook_progress", JSON.stringify({ a: "oops", b: ["ok"] }))
    expect(loadPlaybookProgress()).toEqual({ b: ["ok"] })
  })
})

// ── 8. Playbook 渲染（H4）───────────────────────────────────

function renderHub(entry = "/") {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <SymbolProvider>
        <PlaybookHub />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </SymbolProvider>
    </MemoryRouter>,
  )
}

describe("Playbook 渲染", () => {
  it("默认渲染第一条路径的全部步骤", () => {
    renderHub()

    // 标题在进度条与步骤卡片各出现一次，故用 getAllByText
    for (const step of PLAYBOOKS[0].steps) {
      expect(screen.getAllByText(step.title).length).toBeGreaterThan(0)
    }
    // 其它路径的步骤不该同时渲染
    expect(screen.queryByText(PLAYBOOKS[1].steps[0].title)).toBeNull()
  })

  it("说明这是引导而非进度追踪", () => {
    renderHub()
    expect(screen.getByText(/不是进度追踪/)).toBeInTheDocument()
  })

  it("?playbook= 决定选中哪条路径", () => {
    renderHub("/?playbook=factor-research")
    expect(screen.getAllByText(PLAYBOOKS[2].steps[0].title).length).toBeGreaterThan(0)
    expect(screen.queryByText(PLAYBOOKS[0].steps[0].title)).toBeNull()
  })

  it("切换路径写回 URL", () => {
    renderHub()

    fireEvent.click(screen.getByRole("tab", { name: new RegExp(PLAYBOOKS[1].name) }))

    expect(locationText()).toContain("playbook=discovery-portfolio")
  })

  it("标记完成后落到 localStorage，重置后清空", () => {
    renderHub()
    const first = PLAYBOOKS[0].steps[0]

    const toggle = screen.getAllByRole("button", { name: /标记完成/ })[0]
    act(() => { fireEvent.click(toggle) })

    expect(loadPlaybookProgress()["single-symbol"]).toContain(first.id)
    expect(screen.getAllByRole("button", { name: /取消完成/ })).toHaveLength(1)

    act(() => { fireEvent.click(screen.getByRole("button", { name: /重置/ })) })

    expect(loadPlaybookProgress()["single-symbol"]).toBeUndefined()
    expect(screen.queryAllByRole("button", { name: /取消完成/ })).toHaveLength(0)
  })

  it("刷新后从 localStorage 恢复已完成步骤", () => {
    togglePlaybookStep("single-symbol", PLAYBOOKS[0].steps[0].id)

    renderHub()

    expect(screen.getAllByRole("button", { name: /取消完成/ })).toHaveLength(1)
  })

  it("每个步骤都有一个「去这一步」的链接，指向该步骤的目标路由", () => {
    renderHub()

    const links = screen.getAllByRole("link", { name: /去这一步/ })
    expect(links).toHaveLength(PLAYBOOKS[0].steps.length)
    expect(links[0].getAttribute("href")).toContain(PLAYBOOKS[0].steps[0].to.split("?")[0])
  })
})
