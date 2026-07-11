import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Line,
} from "recharts"
import type { SeriesPoint } from "@/hooks/useBacktestReport"

interface RollingAreaProps {
  data: SeriesPoint[]
  color?: string
  height?: number
  valueFormatter?: (v: number) => string
  label?: string
  /** 可选叠加基准线（如买入持有），与主序列共享 x 轴 */
  benchmark?: SeriesPoint[]
  benchmarkLabel?: string
  /** y 轴下界固定为 0（如暴露 0~100%、累计收益增长） */
  zeroFloor?: boolean
}

const DEFAULT_COLOR = "#58a6ff"

function fmtDate(iso: string): string {
  return iso.slice(0, 7)
}

/** 将主序列与基准按 time 合并，供 recharts 双线渲染 */
function mergeBenchmark(
  data: SeriesPoint[],
  benchmark: SeriesPoint[],
): { time: string; value: number; bench?: number }[] {
  const benchMap = new Map(benchmark.map((p) => [p.time, p.value]))
  return data.map((p) => ({ time: p.time, value: p.value, bench: benchMap.get(p.time) }))
}

export function RollingArea({
  data,
  color = DEFAULT_COLOR,
  height = 180,
  valueFormatter = (v) => v.toFixed(2),
  label = "",
  benchmark,
  benchmarkLabel = "基准",
  zeroFloor = false,
}: RollingAreaProps) {
  if (!data.length) return null

  const gradientId = `rollArea-${color.replace("#", "")}`
  const chartData = benchmark?.length ? mergeBenchmark(data, benchmark) : data

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.22} />
            <stop offset="95%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>
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
          domain={zeroFloor ? [0, "auto"] : ["auto", "auto"]}
        />
        <Tooltip
          contentStyle={{
            background: "#161b22", border: "1px solid #30363d",
            borderRadius: 6, fontSize: 11, color: "#e6edf3",
          }}
          labelFormatter={(l: string) => l.slice(0, 10)}
          formatter={(v: number, name: string) => [
            valueFormatter(v), name === "bench" ? benchmarkLabel : label,
          ]}
        />
        <ReferenceLine y={0} stroke="#30363d" />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#${gradientId})`}
          dot={false}
          activeDot={{ r: 3, fill: color }}
          name={label}
        />
        {benchmark?.length ? (
          <Line
            type="monotone"
            dataKey="bench"
            stroke="#6e7681"
            strokeWidth={1}
            strokeDasharray="4 3"
            dot={false}
            name="bench"
          />
        ) : null}
      </AreaChart>
    </ResponsiveContainer>
  )
}
