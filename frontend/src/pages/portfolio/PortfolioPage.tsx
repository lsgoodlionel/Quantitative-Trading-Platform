// 组合页（V3 · H1）：原「持仓分析」+「组合优化器」两页合并为两个 Tab。
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useUrlTab } from "@/hooks/useUrlTab"
import { HoldingsTab } from "./HoldingsTab"
import { OptimizerTab } from "./optimizer/OptimizerTab"

const TABS = ["holdings", "optimizer"] as const
type PortfolioTab = (typeof TABS)[number]

const TAB_LABELS: { key: PortfolioTab; label: string }[] = [
  { key: "holdings", label: "💼 持仓分析" },
  { key: "optimizer", label: "🎯 组合优化" },
]

export function PortfolioPage() {
  const [tab, setTab] = useUrlTab<PortfolioTab>(TABS, "holdings")

  // 组合优化原本是独立页，有自己的帮助文案：选中该 Tab 时顶栏换成它
  const help = tab === "optimizer" ? PAGE_HELP["portfolio-optimizer"] : PAGE_HELP["portfolio"]

  return (
    <AppShell title="组合" help={help}>
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

      {tab === "holdings" && <HoldingsTab />}
      {tab === "optimizer" && <OptimizerTab />}
    </AppShell>
  )
}
