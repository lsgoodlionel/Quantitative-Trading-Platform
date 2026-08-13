import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  MemoryRouter,
  Route,
  Routes,
  createRoutesFromElements,
  useLocation,
  useNavigate,
} from "react-router-dom"
import { LegacyRedirect } from "@/components/routing/LegacyRedirect"
import { LEGACY_REDIRECTS, appRouteElements } from "@/routes"
import { useUrlTab } from "@/hooks/useUrlTab"
import { readSymbolSelection } from "@/pages/market/symbolParam"
import { readInitialSelection } from "@/pages/portfolio/optimizer/selection"

// 注：这里用 MemoryRouter 而非 createMemoryRouter —— data router 在 jsdom 下
// 构造 Request 会撞上 undici 的 AbortSignal 校验，导航直接抛错。

/** 探针：把当前 location 打到 DOM 上，并提供一个「后退」按钮供断言历史行为 */
function LocationProbe() {
  const { pathname, search } = useLocation()
  const navigate = useNavigate()
  return (
    <div>
      <div data-testid="location">{pathname + search}</div>
      <button onClick={() => navigate(-1)}>go-back</button>
    </div>
  )
}

/** 进入 entry 前先在历史里放一个 /seed，用于验证「后退」落在哪 */
function renderLegacyRouter(entry: string) {
  render(
    <MemoryRouter initialEntries={["/seed", entry]} initialIndex={1}>
      <Routes>
        {LEGACY_REDIRECTS.map((r) => (
          <Route key={r.from} path={r.from} element={<LegacyRedirect to={r.to} tab={r.tab} />} />
        ))}
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

function locationText(): string {
  return screen.getByTestId("location").textContent ?? ""
}

function searchParams(): URLSearchParams {
  const text = locationText()
  return new URLSearchParams(text.slice(text.indexOf("?")))
}

// ── 旧路径重定向（H1 · 2.2）────────────────────────────────────

describe("旧路径重定向", () => {
  it("覆盖全部被合并的页面路径", () => {
    expect(LEGACY_REDIRECTS.map((r) => r.from).sort()).toEqual(
      [
        "/algolab",
        "/factor",
        "/live-strategy",
        "/market-events",
        "/orders",
        "/portfolio-optimizer",
      ].sort(),
    )
  })

  it.each(LEGACY_REDIRECTS)("$from 重定向到 $to?tab=$tab", ({ from, to, tab }) => {
    renderLegacyRouter(from)

    expect(screen.getByTestId("location")).toHaveTextContent(`${to}?tab=${tab}`)
  })

  it.each(LEGACY_REDIRECTS)("$from 用 replace 跳转：后退不会被弹回旧路径", ({ from }) => {
    renderLegacyRouter(from)

    fireEvent.click(screen.getByText("go-back"))

    // 没有 replace 的话，旧路径还在历史里，后退会再次触发重定向，
    // 用户被困在「后退 → 又回来」的循环里；这里应当退回进入前的页面。
    expect(locationText()).toBe("/seed")
  })

  it("保留旧链接上的查询参数（选股器送来的 ?symbols=）", () => {
    renderLegacyRouter("/portfolio-optimizer?symbols=AAPL,MSFT&market=US")

    expect(locationText().startsWith("/portfolio?")).toBe(true)
    const params = searchParams()
    expect(params.get("tab")).toBe("optimizer")
    expect(params.get("symbols")).toBe("AAPL,MSFT")
    expect(params.get("market")).toBe("US")
  })

  it("保留回测页带往策略交易的参数", () => {
    renderLegacyRouter("/live-strategy?strategy=double_ma&symbol=TSLA&market=US")

    const params = searchParams()
    expect(params.get("tab")).toBe("live")
    expect(params.get("strategy")).toBe("double_ma")
    expect(params.get("symbol")).toBe("TSLA")
  })

  it("应用真实路由表里注册了每一条旧路径", () => {
    const routes = createRoutesFromElements(appRouteElements)
    const paths = routes.map((r) => r.path)

    for (const { from } of LEGACY_REDIRECTS) {
      expect(paths).toContain(from)
    }
    for (const page of ["/", "/market", "/screener", "/research", "/strategies",
                        "/backtest", "/portfolio", "/trading", "/settings"]) {
      expect(paths).toContain(page)
    }
  })
})

// ── Tab 状态进 URL（H1 · 2.3）──────────────────────────────────

const TABS = ["one", "two", "three"] as const
type Tab = (typeof TABS)[number]

function TabHarness() {
  const [tab, setTab] = useUrlTab<Tab>(TABS, "one")
  return (
    <div>
      <span data-testid="tab">{tab}</span>
      {TABS.map((t) => (
        <button key={t} onClick={() => setTab(t)}>{`go-${t}`}</button>
      ))}
      <LocationProbe />
    </div>
  )
}

function renderTabHarness(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/p" element={<TabHarness />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("useUrlTab", () => {
  it("无 tab 参数时回落到默认 Tab", () => {
    renderTabHarness("/p")
    expect(screen.getByTestId("tab")).toHaveTextContent("one")
  })

  it("刷新后停在 URL 指定的 Tab", () => {
    renderTabHarness("/p?tab=three")
    expect(screen.getByTestId("tab")).toHaveTextContent("three")
  })

  it("URL 里是未知 Tab 时回落到默认值，不渲染空白页", () => {
    renderTabHarness("/p?tab=bogus")
    expect(screen.getByTestId("tab")).toHaveTextContent("one")
  })

  it("切 Tab 后 URL 跟着变", () => {
    renderTabHarness("/p")

    fireEvent.click(screen.getByText("go-two"))

    expect(screen.getByTestId("tab")).toHaveTextContent("two")
    expect(screen.getByTestId("location")).toHaveTextContent("/p?tab=two")
  })

  it("切 Tab 保留页面上的其它查询参数", () => {
    renderTabHarness("/p?tab=one&symbol=AAPL")

    fireEvent.click(screen.getByText("go-three"))

    const params = searchParams()
    expect(params.get("tab")).toBe("three")
    expect(params.get("symbol")).toBe("AAPL")
  })

  it("切 Tab 进历史栈，可以后退回上一个 Tab", () => {
    renderTabHarness("/p?tab=one")

    fireEvent.click(screen.getByText("go-two"))
    expect(screen.getByTestId("tab")).toHaveTextContent("two")

    fireEvent.click(screen.getByText("go-back"))

    // 切 Tab 是 push 而非 replace，所以「后退」回到上一个 Tab
    expect(screen.getByTestId("tab")).toHaveTextContent("one")
  })
})

// ── 合并后各 Tab 原有的 URL 参数（H1 · 2.3）─────────────────────

describe("行情页 ?symbol= 参数", () => {
  it("读取选股器送来的标的与市场", () => {
    expect(readSymbolSelection("?symbol=tsla&market=US")).toEqual({
      symbol: "TSLA",
      market: "US",
    })
  })

  it("无参数时回落到默认标的", () => {
    expect(readSymbolSelection("")).toEqual({ symbol: "AAPL", market: "US" })
  })

  it("市场非法时回落到 US，而不是把非法值传给行情接口", () => {
    expect(readSymbolSelection("?symbol=00700&market=XX")).toEqual({
      symbol: "00700",
      market: "US",
    })
  })

  it("按市场识别港股/A 股", () => {
    expect(readSymbolSelection("?symbol=00700&market=HK").market).toBe("HK")
    expect(readSymbolSelection("?symbol=600519&market=A").market).toBe("A")
  })
})

// ── 页面文件体量（H6）────────────────────────────────────────────

const MAX_LINES = 800

// 用 Vite 的 glob 把 src/pages 下每个源文件读成字符串（无需 node 类型）
const pageSources = import.meta.glob("../pages/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>

describe("页面文件体量", () => {
  it(`src/pages 下没有超过 ${MAX_LINES} 行的文件`, () => {
    const oversized = Object.entries(pageSources)
      .map(([path, source]) => ({ path, lines: source.split("\n").length }))
      .filter(({ lines }) => lines > MAX_LINES)

    expect(oversized).toEqual([])
  })

  it("确实扫到了页面文件（防止 glob 写错导致空断言）", () => {
    expect(Object.keys(pageSources).length).toBeGreaterThan(40)
  })
})

describe("组合优化 ?symbols= 参数", () => {
  it("读取选股器送来的标的列表", () => {
    expect(readInitialSelection("?symbols=aapl,msft&market=US")).toEqual({
      symbolsText: "AAPL, MSFT",
      market: "US",
    })
  })

  it("无参数时回落到该市场的默认篮子", () => {
    const { symbolsText, market } = readInitialSelection("?market=HK")
    expect(market).toBe("HK")
    expect(symbolsText).toContain("00700")
  })
})
