import { useState } from "react"
import { BiasCheckTab } from "./BiasCheckTab"
import { SubTabs } from "./SubTabs"
import { WalkForwardTab } from "./WalkForwardTab"

type ValidationSub = "walkforward" | "bias"

const SUBS: { key: ValidationSub; label: string }[] = [
  { key: "walkforward", label: "🔁 Walk-Forward" },
  { key: "bias", label: "🔬 偏差检测" },
]

// ── Tab: 样本外验证（V3 · H2 合并 Walk-Forward + 偏差检测）────
export function ValidationCombinedTab() {
  const [sub, setSub] = useState<ValidationSub>("walkforward")
  return (
    <div className="space-y-5">
      <SubTabs tabs={SUBS} active={sub} onChange={setSub} />
      {sub === "walkforward" ? <WalkForwardTab /> : <BiasCheckTab />}
    </div>
  )
}
