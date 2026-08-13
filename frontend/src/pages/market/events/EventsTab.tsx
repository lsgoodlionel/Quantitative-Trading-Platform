// 事件期权 Tab：公司新闻 / 财报日历 / 分红日历 / 期权链。
// 由原 pages/MarketEvents.tsx 整页迁入行情页的一个 Tab（V3 · H1），内容未改。
import { useState } from "react"
import type { PageHelpData } from "@/data/pageHelp"
import type { Market } from "@/types"
import { NewsPanel } from "./NewsPanel"
import { DividendPanel, EarningsPanel } from "./CalendarPanels"
import { OptionsPanel } from "./OptionsPanel"

const MARKETS: { value: Market; label: string; defaultSymbol: string }[] = [
  { value: "US", label: "美股", defaultSymbol: "AAPL" },
  { value: "HK", label: "港股", defaultSymbol: "00700" },
  { value: "A", label: "A股", defaultSymbol: "600519" },
]

type EventTab = "news" | "earnings" | "dividends" | "options"

const TABS: { key: EventTab; label: string; icon: string }[] = [
  { key: "news", label: "公司新闻", icon: "📰" },
  { key: "earnings", label: "财报日历", icon: "📅" },
  { key: "dividends", label: "分红日历", icon: "💰" },
  { key: "options", label: "期权链", icon: "⛓️" },
]

/** 该 Tab 专属帮助文案：合并前是独立页面的 help，选中本 Tab 时顶栏展示它 */
export const EVENTS_HELP: PageHelpData = {
  summary: "事件与期权：公司新闻流 + 财报/分红日历 + 期权链（含 BSM Greeks）",
  sections: [
    {
      heading: "📊 功能介绍",
      items: [
        "公司新闻：yfinance 新闻流（美股/港股；A 股暂无源）",
        "财报日历：财报披露日 + EPS 预期/实际/超预期",
        "分红日历：除权除息日 + 每股金额 + 股息率",
        "期权链：行权价/到期/OI/成交/IV + Delta/Gamma/Theta/Vega（仅美股）",
      ],
    },
    {
      heading: "⚙️ 运行原理",
      items: [
        "美股/港股：yfinance .news/.calendar/.dividends/option_chain",
        "A 股：AkShare 预约披露时间 + 分红送配详情",
        "Greeks：本地 BSM 定价，S=现价 sigma=IV r=无风险利率 q=0",
        "T=剩余自然日/365；当日到期或 IV 缺失时不计算 Greeks",
      ],
    },
  ],
}

interface EventsTabProps {
  initialSymbol: string
  initialMarket: Market
}

export function EventsTab({ initialSymbol, initialMarket }: EventsTabProps) {
  const [market, setMarket] = useState<Market>(initialMarket)
  const [symbol, setSymbol] = useState(initialSymbol)
  const [inputValue, setInputValue] = useState(initialSymbol)
  const [tab, setTab] = useState<EventTab>("news")

  const applySymbol = () => {
    const s = inputValue.trim().toUpperCase()
    if (s) setSymbol(s)
  }

  const changeMarket = (m: Market) => {
    setMarket(m)
    const def = MARKETS.find((x) => x.value === m)?.defaultSymbol ?? ""
    setSymbol(def)
    setInputValue(def)
    if (m !== "US" && tab === "options") setTab("news")
  }

  return (
    <div className="mx-auto max-w-5xl">
      {/* 标的选择 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-[#30363d] bg-[#0d1117] p-0.5">
          {MARKETS.map((m) => (
            <button
              key={m.value}
              onClick={() => changeMarket(m.value)}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                market === m.value
                  ? "bg-[#238636] text-white"
                  : "text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applySymbol()}
            placeholder="标的代码"
            className="w-36 rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-1.5 text-sm text-[#e6edf3] focus:border-[#58a6ff] focus:outline-none"
          />
          <button
            onClick={applySymbol}
            className="rounded-md bg-[#238636] px-3 py-1.5 text-sm text-white transition-colors hover:bg-[#2ea043]"
          >
            查询
          </button>
        </div>
        <span className="text-sm text-[#8b949e]">
          当前：<span className="font-medium text-[#e6edf3]">{symbol}</span>
        </span>
      </div>

      {/* 子 Tab 切换 */}
      <div className="mb-4 flex gap-1 border-b border-[#21262d]">
        {TABS.map((t) => {
          const disabled = t.key === "options" && market !== "US"
          return (
            <button
              key={t.key}
              onClick={() => !disabled && setTab(t.key)}
              disabled={disabled}
              className={`-mb-px border-b-2 px-4 py-2 text-sm transition-colors ${
                tab === t.key
                  ? "border-[#58a6ff] text-[#e6edf3]"
                  : "border-transparent text-[#8b949e] hover:text-[#e6edf3]"
              } ${disabled ? "cursor-not-allowed opacity-40" : ""}`}
            >
              {t.icon} {t.label}
            </button>
          )
        })}
      </div>

      {/* 子 Tab 内容 */}
      <div key={`${market}:${symbol}:${tab}`}>
        {tab === "news" && <NewsPanel symbol={symbol} market={market} />}
        {tab === "earnings" && <EarningsPanel symbol={symbol} market={market} />}
        {tab === "dividends" && <DividendPanel symbol={symbol} market={market} />}
        {tab === "options" && <OptionsPanel symbol={symbol} market={market} />}
      </div>
    </div>
  )
}
