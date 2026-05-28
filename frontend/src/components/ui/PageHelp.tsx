import { useState, useEffect, useRef } from "react"
import type { PageHelpData } from "@/data/pageHelp"

interface PageHelpProps {
  data: PageHelpData
}

// ── 帮助抽屉（右侧滑入）─────────────────────────────────────

function HelpDrawer({ data, onClose }: { data: PageHelpData; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)

  // 点击遮罩关闭
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [onClose])

  // Esc 关闭
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  return (
    // 遮罩层
    <div
      className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px]"
      aria-modal="true"
      role="dialog"
    >
      {/* 抽屉面板 */}
      <div
        ref={ref}
        className="absolute right-0 top-0 h-full w-full max-w-sm bg-[#161b22] border-l border-[#21262d] flex flex-col shadow-2xl animate-[slideInRight_260ms_cubic-bezier(0.16,1,0.3,1)]"
      >
        {/* 头部 */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-[#21262d] shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[#58a6ff] text-sm font-semibold">📖 功能说明</span>
            </div>
            <p className="text-[#8b949e] text-xs leading-relaxed">{data.summary}</p>
          </div>
          <button
            onClick={onClose}
            className="ml-3 shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
            aria-label="关闭帮助"
          >
            ✕
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {data.sections.map((section) => (
            <div key={section.heading}>
              <h3 className="text-xs font-semibold text-[#e6edf3] mb-2.5">{section.heading}</h3>
              <ul className="space-y-2">
                {section.items.map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-[#8b949e] leading-relaxed">
                    <span className="text-[#3d444d] shrink-0 mt-0.5">▸</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* 底部 */}
        <div className="px-5 py-3 border-t border-[#21262d] shrink-0">
          <p className="text-[10px] text-[#6e7681]">按 Esc 或点击外部关闭</p>
        </div>
      </div>
    </div>
  )
}

// ── 主组件：? 按钮 ────────────────────────────────────────────

export function PageHelp({ data }: PageHelpProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="查看功能说明与原理"
        aria-label="页面帮助"
        className="
          inline-flex items-center justify-center
          w-[18px] h-[18px] rounded-full
          text-[10px] font-bold leading-none
          bg-[#21262d] text-[#8b949e]
          border border-[#30363d]
          hover:bg-[#30363d] hover:text-[#58a6ff] hover:border-[#58a6ff]/50
          transition-colors cursor-pointer select-none
        "
      >
        ?
      </button>

      {open && <HelpDrawer data={data} onClose={() => setOpen(false)} />}
    </>
  )
}
