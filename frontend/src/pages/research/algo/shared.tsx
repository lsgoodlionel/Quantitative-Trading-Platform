/**
 * Shared UI primitives for AlgoLab panels.
 * Kept here so each panel file stays focused and under 400 lines.
 */

export const CHART_COLORS = {
  green: "#3fb950",
  red:   "#f85149",
  blue:  "#58a6ff",
  muted: "#8b949e",
}

export function SectionCard({
  title,
  sub,
  children,
}: {
  title: string
  sub?: string
  children: React.ReactNode
}) {
  return (
    <div className="card">
      <div className="flex items-baseline gap-2 mb-4">
        <h3 className="text-sm font-semibold text-[#e6edf3]">{title}</h3>
        {sub && <span className="text-xs text-[#6e7681]">{sub}</span>}
      </div>
      {children}
    </div>
  )
}

export function ParamRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-3 mb-3">
      <label className="label w-40 shrink-0 text-right">{label}</label>
      {children}
    </div>
  )
}

export function MetaGrid({
  items,
}: {
  items: { label: string; value: string; accent?: "up" | "down" }[]
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      {items.map(({ label, value, accent }) => (
        <div key={label} className="bg-[#1c2128] border border-[#21262d] rounded-lg p-3">
          <p className="text-xs text-[#6e7681] mb-1">{label}</p>
          <p
            className={`font-mono text-sm font-semibold ${
              accent === "up"
                ? "text-[#3fb950]"
                : accent === "down"
                  ? "text-[#f85149]"
                  : "text-[#e6edf3]"
            }`}
          >
            {value}
          </p>
        </div>
      ))}
    </div>
  )
}
