import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import type { DrawdownPoint } from "@/types"

interface DrawdownChartProps {
  data: DrawdownPoint[]
  height?: number
}

export function DrawdownChart({ data, height = 180 }: DrawdownChartProps) {
  if (!data.length) return null

  const minVal = Math.min(...data.map((d) => d.value))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f85149" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#f85149" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
        <XAxis
          dataKey="time"
          tickFormatter={(v: string) => v.slice(0, 7)}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          domain={[Math.floor(minVal * 1.1), 0]}
          width={52}
        />
        <Tooltip
          contentStyle={{
            background: "#161b22", border: "1px solid #30363d",
            borderRadius: 6, fontSize: 11,
          }}
          labelFormatter={(l: string) => l.slice(0, 10)}
          formatter={(v: number) => [`${v.toFixed(2)}%`, "回撤"]}
        />
        <ReferenceLine y={0} stroke="#30363d" />
        <Area
          type="monotone"
          dataKey="value"
          stroke="#f85149"
          strokeWidth={1.5}
          fill="url(#ddGrad)"
          dot={false}
          activeDot={{ r: 3, fill: "#f85149" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
