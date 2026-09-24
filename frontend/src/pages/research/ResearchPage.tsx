// 研究页（V3 · H1）：原「因子分析」+「算法实验室」两页合并为两个 Tab。
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useUrlTab } from "@/hooks/useUrlTab"
import { AlgoTab } from "./AlgoTab"
import { FactorTab } from "./FactorTab"

const TABS = ["factor", "ml"] as const
type ResearchTab = (typeof TABS)[number]

const TAB_LABELS: { key: ResearchTab; label: string }[] = [
  { key: "factor", label: "🔭 因子研究" },
  { key: "ml", label: "🧪 算法实验室" },
]

export function ResearchPage() {
  const [tab, setTab] = useUrlTab<ResearchTab>(TABS, "factor")

  // 两个 Tab 合并前各是独立页面，帮助文案跟着当前 Tab 走
  const help = tab === "ml" ? PAGE_HELP.algolab : PAGE_HELP.factor

  return (
    <AppShell title="研究" help={help}>
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

      {tab === "factor" && <FactorTab />}
      {tab === "ml" && <AlgoTab />}
    </AppShell>
  )
}
