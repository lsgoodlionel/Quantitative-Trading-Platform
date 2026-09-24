// 事件期权 Tab 的公共格式化与小组件：新闻/财报/分红/期权四个面板都用。
import { Spinner } from "@/components/ui/Spinner"

// ── 格式化 ───────────────────────────────────────────────────────

export function fmtDate(v: string | null): string {
  if (!v) return "—"
  return v.slice(0, 10)
}

export function fmtDateTime(v: string | null): string {
  if (!v) return "—"
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function fmtNum(v: number | null | undefined, d = 2): string {
  return v == null ? "—" : v.toFixed(d)
}

export function fmtInt(v: number | null | undefined): string {
  return v == null ? "—" : Math.round(v).toLocaleString("en-US")
}

export function fmtPct(v: number | null | undefined, fromFraction = false): string {
  if (v == null) return "—"
  const pct = fromFraction ? v * 100 : v
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`
}

export function pctColor(v: number | null | undefined): string {
  if (v == null) return "text-[#8b949e]"
  if (v > 0) return "text-[#3fb950]"
  if (v < 0) return "text-[#f85149]"
  return "text-[#8b949e]"
}

// ── 通用小组件 ───────────────────────────────────────────────────

export function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null
  return (
    <div className="mb-3 rounded-md border border-[#9e6a03] bg-[#9e6a03]/10 px-3 py-2 text-xs text-[#e3b341]">
      {items.map((w, i) => (
        <div key={i}>⚠️ {w}</div>
      ))}
    </div>
  )
}

export function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-16">
      <Spinner />
    </div>
  )
}

export function UpcomingBadge() {
  return (
    <span className="ml-2 rounded bg-[#1f6feb]/20 px-1.5 py-0.5 text-[10px] text-[#58a6ff]">
      即将
    </span>
  )
}
