import { Link } from "react-router-dom"
import { DEFAULT_SETUP_URL } from "@/hooks/useCopilot"

/**
 * 一个模型都没配时的引导块。
 *
 * 后端在这种情况下回的是 200 + `needs_setup`，不是裸 501 —— 对用户来说
 * 「还没配模型」是一句引导语，不是一个错误码。这里给出去配置的直达入口。
 */
export function SetupGuidance({ text, setupUrl }: { text: string; setupUrl: string | null }) {
  return (
    <div className="rounded-md border border-[#388bfd]/40 bg-[#0d1b2a]/50 p-3 space-y-2">
      <h3 className="text-xs font-semibold text-[#58a6ff]">还没有可用的模型服务</h3>
      <p className="text-[11px] text-[#c9d1d9] leading-relaxed whitespace-pre-line">{text}</p>
      <Link to={setupUrl ?? DEFAULT_SETUP_URL} className="btn btn-primary text-xs inline-block">
        去配置
      </Link>
    </div>
  )
}
