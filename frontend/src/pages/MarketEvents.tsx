import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import type { PageHelpData } from "@/data/pageHelp"
import type { Market } from "@/types"
import {
  useCompanyNews,
  useEarningsCalendar,
  useDividendCalendar,
  useOptionExpirations,
  useOptionChain,
  type CompanyNewsItem,
  type EarningsEvent,
  type DividendEvent,
  type OptionContract,
} from "@/hooks/useMarketEvents"

// ── 常量 & 帮助 ──────────────────────────────────────────────────

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

const PAGE_HELP: PageHelpData = {
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

// ── 格式化 ───────────────────────────────────────────────────────

function fmtDate(v: string | null): string {
  if (!v) return "—"
  return v.slice(0, 10)
}

function fmtDateTime(v: string | null): string {
  if (!v) return "—"
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function fmtNum(v: number | null | undefined, d = 2): string {
  return v == null ? "—" : v.toFixed(d)
}

function fmtInt(v: number | null | undefined): string {
  return v == null ? "—" : Math.round(v).toLocaleString("en-US")
}

function fmtPct(v: number | null | undefined, fromFraction = false): string {
  if (v == null) return "—"
  const pct = fromFraction ? v * 100 : v
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`
}

function pctColor(v: number | null | undefined): string {
  if (v == null) return "text-[#8b949e]"
  if (v > 0) return "text-[#3fb950]"
  if (v < 0) return "text-[#f85149]"
  return "text-[#8b949e]"
}

// ── 通用小组件 ───────────────────────────────────────────────────

function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null
  return (
    <div className="mb-3 rounded-md border border-[#9e6a03] bg-[#9e6a03]/10 px-3 py-2 text-xs text-[#e3b341]">
      {items.map((w, i) => (
        <div key={i}>⚠️ {w}</div>
      ))}
    </div>
  )
}

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-16">
      <Spinner />
    </div>
  )
}

function UpcomingBadge() {
  return (
    <span className="ml-2 rounded bg-[#1f6feb]/20 px-1.5 py-0.5 text-[10px] text-[#58a6ff]">
      即将
    </span>
  )
}

// ── 新闻面板 ─────────────────────────────────────────────────────

function NewsCard({ item }: { item: CompanyNewsItem }) {
  const body = (
    <div className="flex gap-3 rounded-lg border border-[#21262d] bg-[#161b22] p-3 transition-colors hover:border-[#30363d]">
      {item.thumbnail && (
        <img
          src={item.thumbnail}
          alt=""
          loading="lazy"
          className="h-16 w-16 shrink-0 rounded object-cover"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-sm font-medium text-[#e6edf3]">{item.title}</div>
        {item.summary && (
          <div className="mt-1 line-clamp-2 text-xs text-[#8b949e]">{item.summary}</div>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-[#484f58]">
          {item.publisher && <span>{item.publisher}</span>}
          <span>{fmtDateTime(item.published_at)}</span>
        </div>
      </div>
    </div>
  )
  return item.url ? (
    <a href={item.url} target="_blank" rel="noopener noreferrer" className="block">
      {body}
    </a>
  ) : (
    body
  )
}

function NewsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useCompanyNews(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="新闻加载失败" description="数据源暂不可用，请稍后重试" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.items.length > 0 ? (
        <div className="space-y-2">
          {data.items.map((item, i) => (
            <NewsCard key={item.url ?? i} item={item} />
          ))}
        </div>
      ) : (
        <EmptyState title="暂无新闻" description="该标的近期无新闻或数据源不覆盖" />
      )}
    </div>
  )
}

// ── 财报日历面板 ─────────────────────────────────────────────────

function EarningsRow({ e }: { e: EarningsEvent }) {
  return (
    <tr className="border-b border-[#21262d] hover:bg-[#161b22]">
      <td className="px-3 py-2 text-[#e6edf3]">
        {fmtDate(e.report_date)}
        {e.is_upcoming && <UpcomingBadge />}
      </td>
      <td className="px-3 py-2 text-[#8b949e]">{e.period ?? "—"}</td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.eps_estimate)}</td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.eps_actual)}</td>
      <td className={`px-3 py-2 text-right ${pctColor(e.surprise_percent)}`}>
        {fmtPct(e.surprise_percent)}
      </td>
    </tr>
  )
}

function EarningsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useEarningsCalendar(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="财报日历加载失败" description="数据源暂不可用" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.events.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-[#21262d]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d1117] text-xs text-[#8b949e]">
              <tr>
                <th className="px-3 py-2 text-left font-medium">披露日</th>
                <th className="px-3 py-2 text-left font-medium">报告期</th>
                <th className="px-3 py-2 text-right font-medium">EPS 预期</th>
                <th className="px-3 py-2 text-right font-medium">EPS 实际</th>
                <th className="px-3 py-2 text-right font-medium">超预期</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e, i) => (
                <EarningsRow key={`${e.report_date}-${i}`} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="暂无财报日历" description="该标的无财报数据或数据源不覆盖" />
      )}
    </div>
  )
}

// ── 分红日历面板 ─────────────────────────────────────────────────

function DividendRow({ e }: { e: DividendEvent }) {
  return (
    <tr className="border-b border-[#21262d] hover:bg-[#161b22]">
      <td className="px-3 py-2 text-[#e6edf3]">
        {fmtDate(e.ex_dividend_date)}
        {e.is_upcoming && <UpcomingBadge />}
      </td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.amount, 4)}</td>
      <td className="px-3 py-2 text-right text-[#3fb950]">{fmtPct(e.dividend_yield, true)}</td>
      <td className="px-3 py-2 text-[#8b949e]">{fmtDate(e.record_date)}</td>
      <td className="px-3 py-2 text-[#8b949e]">{fmtDate(e.payment_date)}</td>
    </tr>
  )
}

function DividendPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useDividendCalendar(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="分红日历加载失败" description="数据源暂不可用" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.events.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-[#21262d]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d1117] text-xs text-[#8b949e]">
              <tr>
                <th className="px-3 py-2 text-left font-medium">除息日</th>
                <th className="px-3 py-2 text-right font-medium">每股分红</th>
                <th className="px-3 py-2 text-right font-medium">股息率</th>
                <th className="px-3 py-2 text-left font-medium">登记日</th>
                <th className="px-3 py-2 text-left font-medium">派息日</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e, i) => (
                <DividendRow key={`${e.ex_dividend_date}-${i}`} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="暂无分红日历" description="该标的无分红记录或数据源不覆盖" />
      )}
    </div>
  )
}

// ── 期权链面板 ───────────────────────────────────────────────────

const OPTION_COLS = [
  "行权价", "最新", "买价", "卖价", "涨跌%", "成交", "OI", "IV", "Δ", "Γ", "Θ", "ν",
]

function OptionRow({ c }: { c: OptionContract }) {
  return (
    <tr
      className={`border-b border-[#21262d] hover:bg-[#161b22] ${
        c.in_the_money ? "bg-[#1f6feb]/5" : ""
      }`}
    >
      <td className="px-2 py-1.5 font-medium text-[#e6edf3]">{fmtNum(c.strike)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.last_price)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtNum(c.bid)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtNum(c.ask)}</td>
      <td className={`px-2 py-1.5 text-right ${pctColor(c.percent_change)}`}>
        {fmtPct(c.percent_change)}
      </td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtInt(c.volume)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtInt(c.open_interest)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtPct(c.implied_volatility, true)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.delta, 3)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.gamma, 4)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.theta, 4)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.vega, 4)}</td>
    </tr>
  )
}

function OptionTable({ title, rows }: { title: string; rows: OptionContract[] }) {
  return (
    <div>
      <div className="mb-1.5 text-sm font-medium text-[#e6edf3]">{title}</div>
      <div className="overflow-x-auto rounded-lg border border-[#21262d]">
        <table className="w-full text-xs">
          <thead className="bg-[#0d1117] text-[#8b949e]">
            <tr>
              {OPTION_COLS.map((col) => (
                <th key={col} className="px-2 py-1.5 text-right font-medium first:text-left">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((c, i) => (
              <OptionRow key={c.contract_symbol ?? i} c={c} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function OptionsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const isUS = market === "US"
  const expQuery = useOptionExpirations(symbol, isUS)
  const [expiration, setExpiration] = useState<string | null>(null)
  const activeExp = expiration ?? expQuery.data?.expirations[0] ?? null
  const chainQuery = useOptionChain(symbol, activeExp, isUS)

  if (!isUS) {
    return <EmptyState title="仅支持美股期权" description="期权链数据来自 yfinance，目前仅覆盖美股标的" />
  }
  if (expQuery.isLoading) return <LoadingBlock />
  if (expQuery.isError)
    return <EmptyState title="期权数据加载失败" description="数据源暂不可用" />

  const expirations = expQuery.data?.expirations ?? []
  if (!expirations.length) {
    return (
      <div>
        <Warnings items={expQuery.data?.warnings ?? []} />
        <EmptyState title="无期权链" description="该标的无期权或非期权标的" />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#8b949e]">到期日</span>
          <select
            value={activeExp ?? ""}
            onChange={(e) => setExpiration(e.target.value)}
            className="rounded-md border border-[#30363d] bg-[#0d1117] px-2 py-1 text-sm text-[#e6edf3] focus:border-[#58a6ff] focus:outline-none"
          >
            {expirations.map((exp) => (
              <option key={exp} value={exp}>
                {exp}
              </option>
            ))}
          </select>
        </div>
        {chainQuery.data?.underlying_price != null && (
          <span className="text-xs text-[#8b949e]">
            标的现价 <span className="text-[#e6edf3]">{fmtNum(chainQuery.data.underlying_price)}</span>
          </span>
        )}
        <span className="text-xs text-[#484f58]">
          Greeks 无风险利率 {fmtPct((chainQuery.data?.risk_free_rate ?? 0) * 100)}
        </span>
      </div>

      <Warnings items={chainQuery.data?.warnings ?? []} />

      {chainQuery.isLoading ? (
        <LoadingBlock />
      ) : chainQuery.data ? (
        <div className="space-y-4">
          <OptionTable title="🟢 看涨 Calls" rows={chainQuery.data.calls} />
          <OptionTable title="🔴 看跌 Puts" rows={chainQuery.data.puts} />
        </div>
      ) : (
        <EmptyState title="无期权链数据" description="请选择其它到期日" />
      )}
    </div>
  )
}

// ── 页面主体 ─────────────────────────────────────────────────────

export function MarketEvents() {
  const [market, setMarket] = useState<Market>("US")
  const [symbol, setSymbol] = useState("AAPL")
  const [inputValue, setInputValue] = useState("AAPL")
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
    <AppShell title="事件与期权" help={PAGE_HELP}>
      <div className="mx-auto max-w-5xl p-4">
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

        {/* Tab 切换 */}
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

        {/* Tab 内容 */}
        <div key={`${market}:${symbol}:${tab}`}>
          {tab === "news" && <NewsPanel symbol={symbol} market={market} />}
          {tab === "earnings" && <EarningsPanel symbol={symbol} market={market} />}
          {tab === "dividends" && <DividendPanel symbol={symbol} market={market} />}
          {tab === "options" && <OptionsPanel symbol={symbol} market={market} />}
        </div>
      </div>
    </AppShell>
  )
}
