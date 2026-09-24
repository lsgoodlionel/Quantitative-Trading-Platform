// 交易页（V3 · H1）：原「策略自动交易」+「订单中心」两页合并为两个 Tab。
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useUrlTab } from "@/hooks/useUrlTab"
import { LiveTab } from "./LiveTab"
import { OrdersTab } from "./OrdersTab"

const TABS = ["live", "orders"] as const
type TradingTab = (typeof TABS)[number]

const TAB_LABELS: { key: TradingTab; label: string }[] = [
  { key: "live", label: "🤖 策略交易" },
  { key: "orders", label: "📋 订单中心" },
]

export function TradingPage() {
  const [tab, setTab] = useUrlTab<TradingTab>(TABS, "live")

  // 两个 Tab 合并前各是独立页面，帮助文案跟着当前 Tab 走
  const help = tab === "orders" ? PAGE_HELP["orders"] : PAGE_HELP["live-strategy"]

  return (
    <AppShell title="交易" help={help}>
      <div className="flex gap-1 mb-5 border-b border-[#21262d]">
        {TAB_LABELS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === key
                ? "border-[#58a6ff] text-[#58a6ff]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "live" && <LiveTab />}
      {tab === "orders" && <OrdersTab />}
    </AppShell>
  )
}
