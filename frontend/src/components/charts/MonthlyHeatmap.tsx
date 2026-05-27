import type { MonthlyReturns } from "@/types"

const MONTH_LABELS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
const MONTH_CN = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]

interface MonthlyHeatmapProps {
  data: MonthlyReturns
}

function cellColor(val: number): string {
  if (val > 10) return "bg-[#0d4a21] text-[#3fb950]"
  if (val > 5)  return "bg-[#0c3d1c] text-[#3fb950]"
  if (val > 2)  return "bg-[#0b3318] text-[#56d364]"
  if (val > 0)  return "bg-[#091f10] text-[#7ee787]"
  if (val > -2) return "bg-[#1f0a0a] text-[#ff9ea0]"
  if (val > -5) return "bg-[#2a1010] text-[#f85149]"
  if (val > -10) return "bg-[#3a1111] text-[#f85149]"
  return "bg-[#4a1111] text-[#f85149]"
}

export function MonthlyHeatmap({ data }: MonthlyHeatmapProps) {
  const years = Object.keys(data).sort()
  if (!years.length) return null

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-separate border-spacing-0.5">
        <thead>
          <tr>
            <th className="text-left text-[#6e7681] py-1 pr-2 w-12">年份</th>
            {MONTH_CN.map((m, i) => (
              <th key={i} className="text-center text-[#6e7681] py-1 min-w-[42px]">{m}</th>
            ))}
            <th className="text-right text-[#6e7681] py-1 pl-2 w-16">全年</th>
          </tr>
        </thead>
        <tbody>
          {years.map((year) => {
            const months = data[year] ?? {}
            const yearTotal = Object.values(months).reduce((s, v) => s + v, 0)
            return (
              <tr key={year}>
                <td className="text-[#8b949e] pr-2 py-0.5 font-mono">{year}</td>
                {MONTH_LABELS.map((m, i) => {
                  const val = months[m]
                  if (val === undefined) {
                    return (
                      <td key={i} className="text-center py-0.5">
                        <span className="inline-block px-1 py-0.5 rounded text-[#3d444d] text-[10px]">—</span>
                      </td>
                    )
                  }
                  return (
                    <td key={i} className="text-center py-0.5">
                      <span className={`inline-block px-1 py-0.5 rounded text-[10px] font-mono ${cellColor(val)}`}>
                        {val > 0 ? "+" : ""}{val.toFixed(1)}%
                      </span>
                    </td>
                  )
                })}
                <td className="text-right py-0.5 pl-2">
                  <span className={`font-mono text-[10px] ${yearTotal >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {yearTotal >= 0 ? "+" : ""}{yearTotal.toFixed(1)}%
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
