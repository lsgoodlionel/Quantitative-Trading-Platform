// ── Playbook 选择器（V3 · H4）───────────────────────────────────
//
// 选中哪条路径进 URL 的 `?playbook=`，沿用「URL 即状态」的既有惯例：
// 刷新、分享链接、命令面板的「打开 XX 引导」都能直接落到对应路径上。
import { useCallback, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { PLAYBOOKS, getPlaybook } from "@/data/playbooks"
import { loadPlaybookProgress, type PlaybookProgress } from "@/hooks/useWorkflowStorage"
import { PlaybookPanel } from "./PlaybookPanel"

export function PlaybookHub() {
  const [searchParams, setSearchParams] = useSearchParams()
  const active = getPlaybook(searchParams.get("playbook"))

  const [progress, setProgress] = useState<PlaybookProgress>(() => loadPlaybookProgress())

  const selectPlaybook = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("playbook", id)
          return next
        },
        { replace: false },
      )
    },
    [setSearchParams],
  )

  return (
    <section className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 lg:p-5 mb-6">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-[#e6edf3]">🗺️ 操作引导 Playbook</h2>
        <span className="text-[10px] text-[#6e7681]">按你要做的事挑一条路径</span>
      </div>

      <div role="tablist" aria-label="Playbook 路径" className="flex flex-wrap gap-2 mb-4">
        {PLAYBOOKS.map((pb) => {
          const isActive = pb.id === active.id
          return (
            <button
              key={pb.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => selectPlaybook(pb.id)}
              className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                isActive
                  ? "border-[#58a6ff]/50 bg-[#0d1421] text-[#58a6ff]"
                  : "border-[#30363d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#8b949e]"
              }`}
            >
              {pb.icon} {pb.name}
            </button>
          )
        })}
      </div>

      <PlaybookPanel playbook={active} progress={progress} onProgressChange={setProgress} />
    </section>
  )
}
