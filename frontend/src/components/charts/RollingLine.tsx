import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts"
import type { SeriesPoint } from "@/hooks/useBacktestReport"

interface RollingLineProps {
  data: SeriesPoint[]
  color?: string
  height?: number
  /** y 轴参考线（如滚动夏普的 y=1） */
  refLine?: number
  /** tooltip / y 轴数值格式化 */
  valueFormatter?: (v: number) => string
  label?: string
}

const DEFAULT_COLOR = "#58a6ff"

function fmtDate(iso: string): string {
  return iso.slice(0, 7)
}

export function RollingLine({
  data,
  color = DEFAULT_COLOR,
  height = 180,
  refLine,
  valueFormatter = (v) => v.toFixed(2),
  label = "",
}: RollingLineProps) {
  if (!data.length) return null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis
          dataKey="time"
          tickFormatter={fmtDate}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={valueFormatter}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={48}
        />
        <Tooltip
          contentStyle={{
            background: "#161b22", border: "1px solid #30363d",
            borderRadius: 6, fontSize: 11, color: "#e6edf3",
          }}
          labelFormatter={(l: string) => l.slice(0, 10)}
          formatter={(v: number) => [valueFormatter(v), label]}
        />
        {refLine !== undefined && (
          <ReferenceLine y={refLine} stroke="#6e7681" strokeDasharray="4 3" />
        )}
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 3, fill: color }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
