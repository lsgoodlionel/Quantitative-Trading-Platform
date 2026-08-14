import { Link } from "react-router-dom"
import { SETUP_URL, isSetupNeeded } from "@/hooks/useAiReports"

/**
 * AI 报告端点的错误展示。
 *
 * 「还没配模型」（501）和「模型服务挂了」（503/其它）是两个完全不同的排查方向：
 * 前者给一句引导语 + 一个能点的链接，后者才是红色报错。把两者混成同一个红条，
 * 用户会以为平台坏了。
 */
export function AiSetupNotice({ error }: { error: Error | null }) {
  if (!error) return null

  if (isSetupNeeded(error)) {
    return (
      <div className="rounded-md border border-[#388bfd]/40 bg-[#0d1b2a]/50 p-3 space-y-2">
        <h3 className="text-xs font-semibold text-[#58a6ff]">还没有可用的模型服务</h3>
        <p className="text-[11px] text-[#c9d1d9] leading-relaxed">{error.message}</p>
        <Link to={SETUP_URL} className="btn btn-primary text-xs inline-block">
          去配置
        </Link>
      </div>
    )
  }

  return (
    <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
      {error.message}
    </p>
  )
}
