// 因子分析的图表与统计表：IC 汇总、因子值时序、滚动/累计 IC、分位数收益。
// 全部是纯展示组件，数据由 FactorTab 传入。
import {
  AreaChart, Area, BarChart, Bar as RBar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  Cell,
} from "recharts"

export const PERIOD_COLORS: Record<number, string> = {
  5:  "#58a6ff",
  10: "#3fb950",
  20: "#e3b341",
}

// ── Helpers ────────────────────────────────────────────────────────

function pct(v: number | undefined): string {
  if (v == null || isNaN(v)) return "—"
  return `${(v * 100).toFixed(1)}%`
}

function num(v: number | undefined, d = 4): string {
  if (v == null || isNaN(v)) return "—"
  return v.toFixed(d)
}

function icColor(v: number | undefined): string {
  if (v == null || isNaN(v)) return "text-[#8b949e]"
  return v >= 0.05 ? "text-[#3fb950]" : v <= -0.05 ? "text-[#f85149]" : "text-[#e6edf3]"
}

// ── IC Stats Table ─────────────────────────────────────────────────

interface IcStatsProps {
  periods: number[]
  icMean: Record<string, number>
  icStd: Record<string, number>
  icIr: Record<string, number>
  icPosRate: Record<string, number>
  icAbsMean: Record<string, number>
}

export function IcStatsTable({ periods, icMean, icStd, icIr, icPosRate, icAbsMean }: IcStatsProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
            <th className="text-left py-2 pr-4">前瞻期</th>
            <th className="text-right py-2 pr-4">IC 均值</th>
            <th className="text-right py-2 pr-4">IC 标准差</th>
            <th className="text-right py-2 pr-4">IC IR</th>
            <th className="text-right py-2 pr-4">IC正比率</th>
            <th className="text-right py-2">IC绝对均值</th>
          </tr>
        </thead>
        <tbody>
          {periods.map((p) => {
            const k = String(p)
            const mean = icMean[k]
            return (
              <tr key={p} className="border-b border-[#21262d]/40 last:border-0">
                <td className="py-2 pr-4">
                  <span className="w-2 h-2 rounded-full inline-block mr-2" style={{ background: PERIOD_COLORS[p] }} />
                  <span className="text-[#e6edf3]">{p}日</span>
                </td>
                <td className={`py-2 pr-4 text-right font-mono ${icColor(mean)}`}>{num(mean)}</td>
                <td className="py-2 pr-4 text-right font-mono text-[#8b949e]">{num(icStd[k])}</td>
                <td className={`py-2 pr-4 text-right font-mono ${icColor(icIr[k])}`}>{num(icIr[k], 3)}</td>
                <td className="py-2 pr-4 text-right font-mono text-[#e6edf3]">{pct(icPosRate[k])}</td>
                <td className="py-2 text-right font-mono text-[#e6edf3]">{num(icAbsMean[k])}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Quantile Bar Chart ─────────────────────────────────────────────

export function QuantileChart({ data }: { data: number[] }) {
  const chartData = data.map((v, i) => ({
    q: `Q${i + 1}`,
    ret: v,
    fill: v >= 0 ? "#3fb950" : "#f85149",
  }))
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis dataKey="q" tick={{ fill: "#8b949e", fontSize: 11 }} />
        <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={48} tickFormatter={(v) => `${v.toFixed(1)}%`} />
        <ReferenceLine y={0} stroke="#6e7681" />
        <Tooltip
          formatter={(v: number) => [`${v.toFixed(2)}%`, "平均收益率"]}
          contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
          itemStyle={{ color: "#e6edf3" }}
        />
        <RBar dataKey="ret" radius={[3, 3, 0, 0]}>
          {chartData.map((d, i) => <Cell key={i} fill={d.fill} />)}
        </RBar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── IC Time Series Chart ───────────────────────────────────────────

export function IcSeriesChart({ series, color, label }: { series: { time: string; ic: number }[]; color: string; label: string }) {
  return (
    <ResponsiveContainer width="100%" height={140}>
      <AreaChart data={series} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
        <defs>
          <linearGradient id={`ic-grad-${label}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 9 }} tickFormatter={(v) => v.slice(0, 7)} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={40} domain={[-1, 1]} tickFormatter={(v) => v.toFixed(1)} />
        <ReferenceLine y={0} stroke="#6e7681" />
        <ReferenceLine y={0.05} stroke={color} strokeDasharray="3 3" opacity={0.5} />
        <ReferenceLine y={-0.05} stroke="#f85149" strokeDasharray="3 3" opacity={0.5} />
        <Tooltip
          formatter={(v: number) => [v.toFixed(4), `IC (${label}日)`]}
          contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
          labelStyle={{ color: "#8b949e" }}
          itemStyle={{ color }}
        />
        <Area type="monotone" dataKey="ic" stroke={color} strokeWidth={1.5} fill={`url(#ic-grad-${label})`} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

// ── Cumulative IC Chart ────────────────────────────────────────────

export function CumulativeIcChart({ data, periods }: { data: Record<string, { time: string; cum_ic: number }[]>; periods: number[] }) {
  // Merge all series by time
  const timeSet = new Set<string>()
  for (const p of periods) {
    (data[String(p)] ?? []).forEach((d) => timeSet.add(d.time))
  }
  const times = Array.from(timeSet).sort()

  const merged = times.map((t) => {
    const row: Record<string, number | string> = { time: t }
    for (const p of periods) {
      const series = data[String(p)] ?? []
      const pt = series.find((d) => d.time === t)
      if (pt) row[`p${p}`] = pt.cum_ic
    }
    return row
  })

  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={merged} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 9 }} tickFormatter={(v: string) => v.slice(0, 7)} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={48} />
        <ReferenceLine y={0} stroke="#6e7681" />
        <Tooltip
          formatter={(v: number, name: string) => [v.toFixed(3), `累计 IC (${name.replace("p", "")}日)`]}
          contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
          labelStyle={{ color: "#8b949e" }}
        />
        {periods.map((p) => (
          <Line key={p} dataKey={`p${p}`} stroke={PERIOD_COLORS[p]} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── Factor Series Chart ────────────────────────────────────────────

export function FactorSeriesChart({ data }: { data: { time: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
        <defs>
          <linearGradient id="factor-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#bc8cff" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#bc8cff" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 9 }} tickFormatter={(v: string) => v.slice(0, 7)} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={52} />
        <ReferenceLine y={0} stroke="#6e7681" />
        <Tooltip
          formatter={(v: number) => [v.toFixed(4), "因子值"]}
          contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
          labelStyle={{ color: "#8b949e" }}
          itemStyle={{ color: "#bc8cff" }}
        />
        <Area type="monotone" dataKey="value" stroke="#bc8cff" strokeWidth={1.5} fill="url(#factor-grad)" dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
