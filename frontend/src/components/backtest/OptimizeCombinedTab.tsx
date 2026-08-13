import { useState } from "react"
import { HyperoptTab } from "./HyperoptTab"
import { OptimizeTab } from "./OptimizeTab"
import { SubTabs } from "./SubTabs"

type OptimizeSub = "grid" | "hyperopt"

const SUBS: { key: OptimizeSub; label: string }[] = [
  { key: "grid", label: "🔍 网格搜索" },
  { key: "hyperopt", label: "🎯 Hyperopt" },
]

// ── Tab: 参数寻优（V3 · H2 合并 网格搜索 + Hyperopt）──────────
export function OptimizeCombinedTab() {
  const [sub, setSub] = useState<OptimizeSub>("grid")
  return (
    <div className="space-y-5">
      <SubTabs tabs={SUBS} active={sub} onChange={setSub} />
      {sub === "grid" ? <OptimizeTab /> : <HyperoptTab />}
    </div>
  )
}
