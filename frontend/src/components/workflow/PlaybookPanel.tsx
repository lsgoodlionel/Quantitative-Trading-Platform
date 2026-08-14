// ── 单条 Playbook 的渲染（V3 · H4）──────────────────────────────
import { useCallback, useMemo } from "react"
import { Link } from "react-router-dom"
import { useSymbolContext } from "@/contexts/SymbolContext"
import { withQuery } from "@/components/palette/commandSearch"
import {
  resetPlaybookProgress,
  togglePlaybookStep,
  type PlaybookProgress,
} from "@/hooks/useWorkflowStorage"
import { StepTrack, type StepTrackItem } from "./StepTrack"
import type { Playbook, PlaybookStep } from "./playbookTypes"

interface PlaybookPanelProps {
  playbook: Playbook
  progress: PlaybookProgress
  onProgressChange: (next: PlaybookProgress) => void
}

export function PlaybookPanel({ playbook, progress, onProgressChange }: PlaybookPanelProps) {
  const { symbol, market } = useSymbolContext()
  const doneIds = useMemo(() => new Set(progress[playbook.id] ?? []), [progress, playbook.id])

  const toggle = useCallback(
    (stepId: string) => onProgressChange(togglePlaybookStep(playbook.id, stepId)),
    [onProgressChange, playbook.id],
  )

  const reset = useCallback(
    () => onProgressChange(resetPlaybookProgress(playbook.id)),
    [onProgressChange, playbook.id],
  )

  // 第一个未标记完成的步骤高亮为「当前」，给用户一个明确的下一步
  const activeIndex = playbook.steps.findIndex((s) => !doneIds.has(s.id))

  const trackItems: StepTrackItem[] = playbook.steps.map((step, i) => ({
    id: step.id,
    label: step.title,
    status: doneIds.has(step.id) ? "done" : i === activeIndex ? "active" : "pending",
  }))

  const stepTarget = (step: PlaybookStep): string =>
    step.withSymbol && symbol ? withQuery(step.to, { symbol, market }) : step.to

  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-4">
        <p className="text-xs text-[#8b949e] leading-relaxed">
          {playbook.summary}
          <br />
          <span className="text-[#6e7681]">
            这里是<strong className="text-[#e3b341]">操作引导</strong>，不是进度追踪：
            完成状态由你手动标记并只存在本机浏览器，系统不会去反查你是否真的跑过某一步。
          </span>
        </p>
        <button
          type="button"
          onClick={reset}
          className="shrink-0 px-3 py-1.5 rounded text-xs text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3] hover:border-[#8b949e] transition-colors"
        >
          重置这条路径
        </button>
      </div>

      <div className="mb-4">
        <StepTrack items={trackItems} />
      </div>

      <ol className="space-y-2">
        {playbook.steps.map((step, i) => {
          const isDone = doneIds.has(step.id)
          return (
            <li
              key={step.id}
              className={`rounded-lg border p-3 transition-colors ${
                isDone
                  ? "border-[#3fb950]/35 bg-[#0f1a10]"
                  : i === activeIndex
                    ? "border-[#58a6ff]/40 bg-[#0d1421]"
                    : "border-[#21262d] bg-[#0d1117]"
              }`}
            >
              <div className="flex items-start gap-3">
                <span className="text-[11px] text-[#6e7681] mt-0.5 w-4 shrink-0">{i + 1}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[#e6edf3]">{step.title}</p>
                  <p className="text-xs text-[#8b949e] mt-1 leading-relaxed">{step.description}</p>
                  <p className="text-[11px] text-[#6e7681] mt-1.5">
                    完成判据：{step.criteria}
                  </p>
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <Link
                    to={stepTarget(step)}
                    className="px-3 py-1 rounded text-xs text-[#58a6ff] border border-[#58a6ff]/35 hover:bg-[#58a6ff]/10 transition-colors text-center"
                  >
                    去这一步 →
                  </Link>
                  <button
                    type="button"
                    onClick={() => toggle(step.id)}
                    className={`px-3 py-1 rounded text-xs border transition-colors ${
                      isDone
                        ? "text-[#3fb950] border-[#3fb950]/40 hover:bg-[#3fb950]/10"
                        : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3] hover:border-[#8b949e]"
                    }`}
                  >
                    {isDone ? "取消完成" : "标记完成"}
                  </button>
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
