import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import type { EquityPoint } from "@/types"

interface EquityCurveProps {
  data: EquityPoint[]
  initialCash: number
  height?: number
}

function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

function formatValue(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return v.toFixed(0)
}

export function EquityCurve({ data, initialCash, height = 240 }: EquityCurveProps) {
  const isUp = data.length === 0 || data[data.length - 1].value >= initialCash

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={isUp ? "#3fb950" : "#f85149"} stopOpacity={0.25} />
            <stop offset="95%" stopColor={isUp ? "#3fb950" : "#f85149"} stopOpacity={0.01} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis
          dataKey="time"
          tickFormatter={formatDate}
          tick={{ fill: "#8b949e", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={formatValue}
          tick={{ fill: "#8b949e", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={52}
        />
        <Tooltip
          contentStyle={{
            background: "#161b22",
            border: "1px solid #30363d",
            borderRadius: 6,
            fontSize: 12,
            color: "#e6edf3",
          }}
          labelFormatter={formatDate}
          formatter={(val: number) => [`$${val.toLocaleString()}`, "净值"]}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke={isUp ? "#3fb950" : "#f85149"}
          strokeWidth={1.5}
          fill="url(#eq-fill)"
          dot={false}
          activeDot={{ r: 3, fill: isUp ? "#3fb950" : "#f85149" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
