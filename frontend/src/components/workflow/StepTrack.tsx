// ── 通用步骤进度条（V3 · H4）────────────────────────────────────
//
// 原本写死在 TradingWorkflow 里，只认 WorkflowStep。Playbook 需要同样的观感
// 但步骤模型完全不同，于是把它降到最小公共契约（id / 序号 / 标题 / 状态），
// 由两边各自映射 —— 而不是复制一份样式出来慢慢漂移。

export type StepTrackStatus = "done" | "active" | "error" | "pending"

export interface StepTrackItem {
  id: string
  /** 圆点下方的标签 */
  label: string
  status: StepTrackStatus
}

interface StepTrackProps {
  items: readonly StepTrackItem[]
}

const CIRCLE_CLASS: Record<StepTrackStatus, string> = {
  done: "bg-[#1a3a1a] border-[#3fb950] text-[#3fb950]",
  error: "bg-[#3a1a1a] border-[#f85149] text-[#f85149]",
  active: "bg-[#1f3d5e] border-[#58a6ff] text-[#58a6ff] shadow-[0_0_8px_#58a6ff40]",
  pending: "bg-[#161b22] border-[#30363d] text-[#6e7681]",
}

const LABEL_CLASS: Record<StepTrackStatus, string> = {
  done: "text-[#3fb950]",
  error: "text-[#f85149]",
  active: "text-[#58a6ff]",
  pending: "text-[#6e7681]",
}

const GLYPH: Partial<Record<StepTrackStatus, string>> = {
  done: "✓",
  error: "!",
}

export function StepTrack({ items }: StepTrackProps) {
  return (
    <div className="flex items-center gap-0 overflow-x-auto pb-1">
      {items.map((item, i) => (
        <div key={item.id} className="flex items-center shrink-0">
          <div className="flex flex-col items-center">
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold border transition-all ${CIRCLE_CLASS[item.status]}`}
            >
              {GLYPH[item.status] ?? i + 1}
            </div>
            <span className={`text-[9px] mt-0.5 whitespace-nowrap ${LABEL_CLASS[item.status]}`}>
              {item.label}
            </span>
          </div>
          {i < items.length - 1 && (
            <div
              className={`w-6 h-px mx-0.5 mb-3 ${item.status === "done" ? "bg-[#3fb950]" : "bg-[#30363d]"}`}
            />
          )}
        </div>
      ))}
    </div>
  )
}
