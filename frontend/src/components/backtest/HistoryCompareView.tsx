import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import { useCompareBacktestHistory, type CompareResult } from "@/hooks/useBacktestHistory"

// ── 历史对比视图（V3 · H5）─────────────────────────────────────
// 多条净值曲线叠加（归一化为初始资金倍数，不同本金也可比）+ 指标表格并列。

const SERIES_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#39c5cf", "#ff7b72", "#a5d6ff"]

const METRIC_LABELS: Record<string, string> = {
  total_return_pct: "总收益%",
  annual_return_pct: "年化%",
  sharpe_ratio: "夏普",
  sortino_ratio: "索提诺",
  calmar_ratio: "卡玛",
  max_drawdown_pct: "最大回撤%",
  win_rate_pct: "胜率%",
  profit_factor: "盈亏比",
  total_trades: "交易数",
}

export function HistoryCompareView({ ids }: { ids: string[] }) {
  const { data, isPending, error } = useCompareBacktestHistory(ids)

  if (ids.length < 2) {
    return (
      <div className="card text-xs text-[#6e7681]">
        勾选至少 2 条历史记录以进行对比。
      </div>
    )
  }
  if (isPending) {
    return (
      <div className="card flex items-center justify-center h-40">
        <Spinner size="lg" />
      </div>
    )
  }
  if (error) {
    return (
      <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
        {error.message}
      </p>
    )
  }
  if (!data) return null

  return (
    <div className="space-y-4">
      <CurveOverlay data={data} />
      <MetricsTable data={data} />
      {data.missing_ids.length > 0 && (
        <p className="text-[11px] text-[#d29922]">
          ⚠️ {data.missing_ids.length} 条记录已不存在，未纳入对比。
        </p>
      )}
    </div>
  )
}

function CurveOverlay({ data }: { data: CompareResult }) {
  const rows = data.axis.map((time, idx) => {
    const row: Record<string, string | number | null> = { time }
    data.series.forEach((s) => { row[s.id] = s.values[idx] })
    return row
  })

  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-1">净值曲线叠加</h3>
      <p className="text-[11px] text-[#6e7681] mb-3">
        纵轴为「相对初始资金的倍数」，已对齐到公共时间轴；曲线起点之前留空。
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="time" tick={{ fill: "#6e7681", fontSize: 10 }} minTickGap={40} />
          <YAxis tick={{ fill: "#6e7681", fontSize: 10 }} domain={["auto", "auto"]} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #21262d", fontSize: 11 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {data.series.map((s, idx) => (
            <Line key={s.id} type="monotone" dataKey={s.id} name={s.name}
              stroke={SERIES_COLORS[idx % SERIES_COLORS.length]}
              dot={false} strokeWidth={1.6} connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function MetricsTable({ data }: { data: CompareResult }) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">指标并列</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[#8b949e] border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">指标</th>
              {data.metrics.rows.map((row) => (
                <th key={row.id} className="text-right py-2 pr-3 font-normal">{row.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.metrics.keys.map((key) => (
              <tr key={key} className="border-b border-[#21262d]/50 last:border-0">
                <td className="py-1.5 pr-3 text-[#8b949e]">{METRIC_LABELS[key] ?? key}</td>
                {data.metrics.rows.map((row) => (
                  <td key={row.id} className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">
                    {formatMetric(row.values[key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}
