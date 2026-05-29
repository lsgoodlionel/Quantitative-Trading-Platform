/**
 * InsightBox — 通用分析结论与建议面板
 *
 * 用法：
 *   <InsightBox
 *     verdict="good"          // "good" | "warn" | "bad" | "neutral"
 *     title="结论"            // 标题
 *     summary="总体评价..."   // 一句话总结
 *     findings={[...]}        // 关键发现列表
 *     recommendations={[...]} // 后续建议列表
 *   />
 */

export type InsightVerdict = "good" | "warn" | "bad" | "neutral"

export interface InsightItem {
  text: string
  sub?: string
  type?: "good" | "warn" | "bad" | "neutral"
}

interface InsightBoxProps {
  verdict: InsightVerdict
  title?: string
  summary: string
  findings?: InsightItem[]
  recommendations?: InsightItem[]
  className?: string
}

const VERDICT_CFG: Record<InsightVerdict, {
  border: string; bg: string; badge: string; badgeBg: string; dot: string; label: string
}> = {
  good:    { border: "border-[#3fb950]/30", bg: "bg-[#162a1e]/40",  badge: "text-[#3fb950]", badgeBg: "bg-[#3fb950]/15", dot: "bg-[#3fb950]", label: "✓ 结论良好" },
  warn:    { border: "border-[#e3b341]/30", bg: "bg-[#272111]/40",  badge: "text-[#e3b341]", badgeBg: "bg-[#e3b341]/15", dot: "bg-[#e3b341]", label: "⚠ 需要关注" },
  bad:     { border: "border-[#f85149]/30", bg: "bg-[#2a1b1b]/40",  badge: "text-[#f85149]", badgeBg: "bg-[#f85149]/15", dot: "bg-[#f85149]", label: "✗ 存在风险" },
  neutral: { border: "border-[#58a6ff]/20", bg: "bg-[#1c2128]/40",  badge: "text-[#58a6ff]", badgeBg: "bg-[#58a6ff]/15", dot: "bg-[#58a6ff]", label: "ℹ 分析完成" },
}

const ITEM_COLOR: Record<NonNullable<InsightItem["type"]>, string> = {
  good:    "text-[#3fb950]",
  warn:    "text-[#e3b341]",
  bad:     "text-[#f85149]",
  neutral: "text-[#8b949e]",
}

const ITEM_DOT: Record<NonNullable<InsightItem["type"]>, string> = {
  good:    "bg-[#3fb950]",
  warn:    "bg-[#e3b341]",
  bad:     "bg-[#f85149]",
  neutral: "bg-[#6e7681]",
}

function InsightItemRow({ item }: { item: InsightItem }) {
  const t = item.type ?? "neutral"
  return (
    <div className="flex gap-2.5 items-start">
      <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${ITEM_DOT[t]}`} />
      <div>
        <span className={`text-xs leading-relaxed ${ITEM_COLOR[t]}`}>{item.text}</span>
        {item.sub && <p className="text-[10px] text-[#6e7681] mt-0.5 leading-relaxed">{item.sub}</p>}
      </div>
    </div>
  )
}

export function InsightBox({
  verdict,
  title = "📝 分析结论与建议",
  summary,
  findings = [],
  recommendations = [],
  className = "",
}: InsightBoxProps) {
  const cfg = VERDICT_CFG[verdict]

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-4 space-y-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2.5">
        <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
        <p className="text-xs font-semibold text-[#8b949e] uppercase tracking-wide">{title}</p>
        <span className={`ml-auto text-[10px] font-bold px-2 py-0.5 rounded ${cfg.badge} ${cfg.badgeBg}`}>
          {cfg.label}
        </span>
      </div>

      {/* Summary */}
      <p className="text-sm text-[#e6edf3] leading-relaxed">{summary}</p>

      {/* Findings */}
      {findings.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-[#6e7681] uppercase tracking-wider mb-2">关键发现</p>
          <div className="space-y-1.5">
            {findings.map((f, i) => <InsightItemRow key={i} item={f} />)}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div className="border-t border-[#21262d] pt-3">
          <p className="text-[10px] font-semibold text-[#6e7681] uppercase tracking-wider mb-2">后续建议</p>
          <div className="space-y-1.5">
            {recommendations.map((r, i) => <InsightItemRow key={i} item={r} />)}
          </div>
        </div>
      )}
    </div>
  )
}
