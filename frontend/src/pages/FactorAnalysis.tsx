import { useState, useMemo } from "react"
import {
  AreaChart, Area, BarChart, Bar as RBar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  Cell,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useFactorList, useFactorAnalysis } from "@/hooks/useFactorAnalysis"
import type { Market, Frequency } from "@/types"

// ── Constants ──────────────────────────────────────────────────────

const MARKETS: Market[] = ["US", "HK", "A"]
const FREQS: Frequency[] = ["1d", "1w"]
const FORWARD_PERIODS = [5, 10, 20]

const PERIOD_COLORS: Record<number, string> = {
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

function IcStatsTable({ periods, icMean, icStd, icIr, icPosRate, icAbsMean }: IcStatsProps) {
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

function QuantileChart({ data }: { data: number[] }) {
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

function IcSeriesChart({ series, color, label }: { series: { time: string; ic: number }[]; color: string; label: string }) {
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

function CumulativeIcChart({ data, periods }: { data: Record<string, { time: string; cum_ic: number }[]>; periods: number[] }) {
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

function FactorSeriesChart({ data }: { data: { time: string; value: number }[] }) {
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

// ── Main Page ──────────────────────────────────────────────────────

export function FactorAnalysis() {
  const { data: factorList = [], isLoading: factorsLoading } = useFactorList()
  const { mutate: runAnalysis, isPending, data: result, error } = useFactorAnalysis()
  const { toast } = useToast()

  const [symbol, setSymbol] = useState("AAPL")
  const [market, setMarket] = useState<Market>("US")
  const [freq, setFreq] = useState<Frequency>("1d")
  const [factorName, setFactorName] = useState("momentum_20")
  const [selectedPeriod, setSelectedPeriod] = useState<number>(20)

  const groupedFactors = useMemo(() => {
    const groups: Record<string, typeof factorList> = {}
    for (const f of factorList) {
      if (!groups[f.group]) groups[f.group] = []
      groups[f.group].push(f)
    }
    return groups
  }, [factorList])

  function handleRun() {
    if (!symbol.trim()) { toast("请输入股票代码", "warning"); return }
    runAnalysis({
      symbol: symbol.trim().toUpperCase(),
      market,
      frequency: freq,
      factor_name: factorName,
      forward_periods: FORWARD_PERIODS,
    })
  }

  const icSeries = result?.ic_series[String(selectedPeriod)] ?? []
  const qtlData = result?.quantile_returns[String(selectedPeriod)] ?? []

  return (
    <AppShell title="因子分析">
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">

        {/* ── Config Panel ── */}
        <div className="xl:col-span-1 space-y-4">
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">分析参数</h3>

            {/* Symbol */}
            <div className="mb-3">
              <label className="label block mb-1">股票代码</label>
              <input
                className="input w-full font-mono uppercase"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="AAPL / 600519 / 0700"
              />
            </div>

            {/* Market */}
            <div className="mb-3">
              <label className="label block mb-1">市场</label>
              <div className="flex gap-1">
                {MARKETS.map((m) => (
                  <button
                    key={m}
                    onClick={() => setMarket(m)}
                    className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                      market === m
                        ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                        : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>

            {/* Freq */}
            <div className="mb-3">
              <label className="label block mb-1">频率</label>
              <div className="flex gap-1">
                {FREQS.map((f) => (
                  <button
                    key={f}
                    onClick={() => setFreq(f)}
                    className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                      freq === f
                        ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                        : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>

            {/* Factor Selector */}
            <div className="mb-4">
              <label className="label block mb-1">因子</label>
              {factorsLoading ? (
                <Spinner size="sm" />
              ) : (
                <div className="space-y-2">
                  {Object.entries(groupedFactors).map(([group, factors]) => (
                    <div key={group}>
                      <p className="text-xs text-[#6e7681] mb-1">{group}</p>
                      {factors.map((f) => (
                        <button
                          key={f.name}
                          onClick={() => setFactorName(f.name)}
                          className={`w-full text-left px-2 py-1 rounded text-xs transition-colors ${
                            factorName === f.name
                              ? "bg-[#bc8cff]/20 text-[#bc8cff]"
                              : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
                          }`}
                        >
                          {f.label}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <button
              className="btn btn-primary w-full"
              onClick={handleRun}
              disabled={isPending}
            >
              {isPending ? <Spinner size="sm" className="mx-auto" /> : "运行因子分析"}
            </button>

            {error && (
              <p className="text-xs text-[#f85149] mt-2 leading-snug">{error.message}</p>
            )}
          </div>
        </div>

        {/* ── Results ── */}
        <div className="xl:col-span-3 space-y-6">
          {!result && !isPending && (
            <div className="card flex items-center justify-center h-64 text-[#6e7681] text-sm">
              选择参数后点击「运行因子分析」
            </div>
          )}

          {isPending && (
            <div className="card flex items-center justify-center h-64">
              <Spinner />
            </div>
          )}

          {result && (
            <>
              {/* Header */}
              <div className="flex items-center gap-3 flex-wrap">
                <h2 className="text-base font-semibold text-[#e6edf3]">
                  {result.symbol} · {result.factor_name}
                </h2>
                <span className="badge text-[#8b949e] border-[#30363d]">{result.market}</span>
                <span className="badge text-[#bc8cff] border-[#bc8cff]/30">
                  {factorList.find((f) => f.name === result.factor_name)?.label ?? result.factor_name}
                </span>
              </div>

              {/* IC Stats Table */}
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">IC 统计汇总</h3>
                <IcStatsTable
                  periods={result.forward_periods}
                  icMean={result.ic_mean}
                  icStd={result.ic_std}
                  icIr={result.ic_ir}
                  icPosRate={result.ic_positive_rate}
                  icAbsMean={result.ic_abs_mean}
                />
              </div>

              {/* Factor series */}
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">因子值时序</h3>
                <FactorSeriesChart data={result.factor_series} />
              </div>

              {/* Period selector + IC series */}
              <div className="card">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-[#e6edf3]">滚动 IC 时序</h3>
                  <div className="flex gap-1">
                    {result.forward_periods.map((p) => (
                      <button
                        key={p}
                        onClick={() => setSelectedPeriod(p)}
                        className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                          selectedPeriod === p
                            ? "text-white"
                            : "text-[#8b949e] hover:text-[#e6edf3]"
                        }`}
                        style={selectedPeriod === p ? { background: PERIOD_COLORS[p] } : {}}
                      >
                        {p}日
                      </button>
                    ))}
                  </div>
                </div>
                <IcSeriesChart
                  series={icSeries}
                  color={PERIOD_COLORS[selectedPeriod]}
                  label={String(selectedPeriod)}
                />
              </div>

              {/* Cumulative IC + Quantile Returns side by side */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="card">
                  <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">累计 IC</h3>
                  <CumulativeIcChart data={result.cumulative_ic} periods={result.forward_periods} />
                  <div className="flex gap-4 mt-2">
                    {result.forward_periods.map((p) => (
                      <div key={p} className="flex items-center gap-1.5 text-xs text-[#8b949e]">
                        <span className="w-2 h-0.5 inline-block" style={{ background: PERIOD_COLORS[p] }} />
                        {p}日
                      </div>
                    ))}
                  </div>
                </div>

                <div className="card">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-[#e6edf3]">分位数收益率</h3>
                    <div className="flex gap-1">
                      {result.forward_periods.map((p) => (
                        <button
                          key={p}
                          onClick={() => setSelectedPeriod(p)}
                          className={`px-2 py-0.5 rounded text-xs transition-colors ${
                            selectedPeriod === p ? "text-white" : "text-[#6e7681]"
                          }`}
                          style={selectedPeriod === p ? { background: PERIOD_COLORS[p] } : {}}
                        >
                          {p}日
                        </button>
                      ))}
                    </div>
                  </div>
                  {qtlData.length > 0
                    ? <QuantileChart data={qtlData} />
                    : <p className="text-xs text-[#6e7681] py-4 text-center">数据不足</p>
                  }
                  <p className="text-xs text-[#6e7681] mt-2">Q1=因子最低分位，Q5=最高分位。单调递增表明因子有效。</p>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}
