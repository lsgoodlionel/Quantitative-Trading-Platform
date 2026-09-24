import { useState } from "react"
import { CopilotPanel } from "@/components/copilot/CopilotPanel"

/**
 * 可从任意页面唤起的助手入口。
 *
 * 挂在 `App.tsx` 里一次即可（`App.tsx` 是共享文件，集成片段见交付报告）。
 * 面板未打开时只有一个悬浮按钮，不占布局、不发任何请求。
 */
export function CopilotLauncher() {
  const [open, setOpen] = useState(false)

  if (open) return <CopilotPanel onClose={() => setOpen(false)} />

  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      aria-label="打开平台助手"
      className="fixed bottom-6 right-6 z-40 rounded-full border border-[#388bfd]/40 bg-[#1c2a3a] px-4 py-2 text-xs text-[#58a6ff] shadow-lg hover:bg-[#22314a]"
    >
      助手
    </button>
  )
}
