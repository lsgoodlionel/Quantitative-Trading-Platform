import { useState, useMemo } from "react"
import { Link } from "react-router-dom"
import {
  AreaChart, Area, BarChart, Bar as RBar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  Cell,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useFactorList, useFactorAnalysis, useFormulaMeta, useFormulaFactor,
  type FactorAnalysisResult,
} from "@/hooks/useFactorAnalysis"
import type { Market, Frequency } from "@/types"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"
import { FormulaBuilder } from "./factor/FormulaBuilder"
import { ProcessorPipeline } from "./factor/ProcessorPipeline"
import { FactorFitness } from "./factor/FactorFitness"

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

type FactorMode = "preset" | "formula" | "processors" | "fitness"

const MODE_TABS: { key: FactorMode; label: string; accent: string }[] = [
  { key: "preset",     label: "预设因子",       accent: "#bc8cff" },
  { key: "formula",    label: "⚡ 公式因子",     accent: "#58a6ff" },
  { key: "processors", label: "🧪 截面处理流水线", accent: "#3fb950" },
  { key: "fitness",    label: "🎯 成本感知适应度", accent: "#e3b341" },
]

export function FactorAnalysis() {
  const { data: factorList = [], isLoading: factorsLoading } = useFactorList()
  const { mutate: runAnalysis, isPending: presetPending, data: presetResult, error: presetError } = useFactorAnalysis()
  const { data: formulaMeta, isLoading: metaLoading } = useFormulaMeta()
  const { mutate: runFormula, isPending: formulaPending, data: formulaResult, error: formulaError } = useFormulaFactor()
  const { toast } = useToast()

  const [mode, setMode] = useState<FactorMode>("preset")
  const [symbol, setSymbol] = useState("AAPL")
  const [market, setMarket] = useState<Market>("US")
  const [freq, setFreq] = useState<Frequency>("1d")
  const [factorName, setFactorName] = useState("momentum_20")
  const [tokens, setTokens] = useState<string[]>([])
  const [selectedPeriod, setSelectedPeriod] = useState<number>(20)

  // 当前模式的结果 / 状态（统一为 FactorAnalysisResult 结构）
  const result: FactorAnalysisResult | undefined = mode === "preset" ? presetResult : formulaResult
  const isPending = mode === "preset" ? presetPending : formulaPending

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

  function handleRunFormula() {
    if (!symbol.trim()) { toast("请输入股票代码", "warning"); return }
    if (tokens.length === 0) { toast("请先构建公式", "warning"); return }
    runFormula({
      symbol: symbol.trim().toUpperCase(),
      market,
      frequency: freq,
      tokens,
      forward_periods: FORWARD_PERIODS,
    })
  }

  const icSeries = result?.ic_series[String(selectedPeriod)] ?? []
  const qtlData = result?.quantile_returns[String(selectedPeriod)] ?? []

  return (
    <AppShell title="因子分析" help={PAGE_HELP.factor}>
      {/* ── 顶部页签 ── */}
      <div className="flex flex-wrap gap-1 bg-[#161b22] rounded-lg p-1 border border-[#21262d] mb-6">
        {MODE_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setMode(t.key)}
            className={`flex-1 min-w-[120px] py-1.5 rounded text-xs font-medium transition-colors ${
              mode === t.key ? "text-[#e6edf3]" : "text-[#8b949e] hover:text-[#e6edf3]"
            }`}
            style={mode === t.key ? { background: `${t.accent}22`, color: t.accent } : {}}
          >
            {t.label}
          </button>
        ))}
      </div>

      {mode === "processors" && (
        <ProcessorPipeline factorList={factorList} formulaTokens={tokens} />
      )}
      {mode === "fitness" && (
        <FactorFitness factorList={factorList} formulaTokens={tokens} />
      )}

      {(mode === "preset" || mode === "formula") && (
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">

        {/* ── Config Panel ── */}
        <div className="xl:col-span-1 space-y-4">
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">
              {mode === "preset" ? "分析参数" : "标的与周期"}
            </h3>

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

            {/* Factor Selector（仅预设模式）*/}
            {mode === "preset" && (
              <>
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

                {presetError && (
                  <p className="text-xs text-[#f85149] mt-2 leading-snug">{presetError.message}</p>
                )}
              </>
            )}
          </div>

          {/* 公式构建器（仅公式模式）*/}
          {mode === "formula" && (
            <FormulaBuilder
              meta={formulaMeta}
              metaLoading={metaLoading}
              tokens={tokens}
              onTokensChange={setTokens}
              onRun={handleRunFormula}
              isRunning={formulaPending}
              error={formulaError?.message ?? null}
            />
          )}
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
                  {result.symbol}
                </h2>
                <span className="badge text-[#8b949e] border-[#30363d]">{result.market}</span>
                {mode === "formula" ? (
                  <span className="badge text-[#58a6ff] border-[#58a6ff]/30 font-mono">
                    ⚡ {result.factor_name}
                  </span>
                ) : (
                  <span className="badge text-[#bc8cff] border-[#bc8cff]/30">
                    {factorList.find((f) => f.name === result.factor_name)?.label ?? result.factor_name}
                  </span>
                )}
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

              {/* 因子结论 */}
              {(() => {
                // 取最长前瞻期的 IC 作为主要评判依据
                const periods = result.forward_periods
                const bestPeriod = periods[periods.length - 1]
                const bk = String(bestPeriod)
                const icMean = result.ic_mean[bk] ?? 0
                const icIr = result.ic_ir[bk] ?? 0
                const icPosRate = result.ic_positive_rate[bk] ?? 0

                // 短周期 IC（5日）
                const shortIc = result.ic_mean[String(periods[0])] ?? 0

                // 分位数单调性检查（Q1 < Q5 为正向因子）
                const qtl20 = result.quantile_returns[bk] ?? []
                const isMonotonic = qtl20.length >= 2 && qtl20[qtl20.length - 1] > qtl20[0]
                const q1q5Spread = qtl20.length >= 2
                  ? qtl20[qtl20.length - 1] - qtl20[0]
                  : 0

                const strength =
                  Math.abs(icMean) >= 0.05 && Math.abs(icIr) >= 0.5 ? "强有效"
                  : Math.abs(icMean) >= 0.03 ? "弱有效"
                  : "无效"

                const verdict: InsightVerdict =
                  strength === "强有效" ? "good"
                  : strength === "弱有效" ? "warn"
                  : "bad"

                const factorLabel = factorList.find((f) => f.name === result.factor_name)?.label ?? result.factor_name
                const direction = icMean >= 0 ? "正向" : "反向"

                const summary = `因子「${factorLabel}」在 ${result.symbol} ${bestPeriod}日前瞻期下IC均值为 ${icMean.toFixed(4)}，IR为 ${icIr.toFixed(3)}，综合评定为「${strength}」${direction}因子。${strength === "无效" ? "建议放弃该因子或对其进行变换处理。" : "具备一定预测能力。"}`

                const findings: InsightItem[] = [
                  {
                    text: `IC均值 ${icMean.toFixed(4)} — ${Math.abs(icMean) >= 0.05 ? "超过 0.05 门槛，因子具有统计预测力" : Math.abs(icMean) >= 0.03 ? "在 0.03~0.05 之间，预测力较弱" : "低于 0.03，预测力不显著"}`,
                    type: Math.abs(icMean) >= 0.05 ? "good" : Math.abs(icMean) >= 0.03 ? "warn" : "bad",
                  },
                  {
                    text: `IC IR ${icIr.toFixed(3)} — ${Math.abs(icIr) >= 0.5 ? "信息比率良好，因子信号稳定" : Math.abs(icIr) >= 0.3 ? "信息比率尚可" : "信息比率偏低，信号不稳定噪声大"}`,
                    type: Math.abs(icIr) >= 0.5 ? "good" : Math.abs(icIr) >= 0.3 ? "warn" : "bad",
                  },
                  {
                    text: `IC正比率 ${(icPosRate * 100).toFixed(1)}% — ${icPosRate >= 0.6 ? "超过60%，因子方向一致性好" : icPosRate >= 0.5 ? "方向略占优" : "方向不稳定，因子时效性差"}`,
                    type: icPosRate >= 0.6 ? "good" : icPosRate >= 0.5 ? "warn" : "bad",
                  },
                  {
                    text: `分位数单调性：Q1→Q5 ${isMonotonic ? `呈单调递增（价差 ${q1q5Spread.toFixed(2)}%），多空策略可行` : "未呈单调分布，分组选股效果不稳定"}`,
                    type: isMonotonic ? "good" : "warn",
                  },
                  ...(Math.abs(shortIc) < Math.abs(icMean) * 0.5 ? [{
                    text: `短周期（${periods[0]}日）IC ${shortIc.toFixed(4)} 显著低于长周期，说明该因子属于中长周期信号`,
                    type: "neutral" as const,
                  }] : []),
                ]

                const recommendations: InsightItem[] = [
                  ...(strength === "无效" ? [{
                    text: "放弃或变换因子",
                    sub: "尝试对因子取对数、排名变换（rank IC）或与其他因子组合使用",
                    type: "bad" as const,
                  }] : []),
                  ...(strength === "强有效" ? [{
                    text: "纳入多因子模型",
                    sub: "与其他有效因子做正交化处理后组合，可在「策略 → 多因子模型」中使用",
                    type: "good" as const,
                  }] : []),
                  {
                    text: "扩展至更多标的做截面验证",
                    sub: "单标的 IC 受样本限制，建议对同市场多只股票计算截面 IC 以验证普适性",
                    type: "neutral" as const,
                  },
                  {
                    text: "关注因子衰退",
                    sub: "查看累计 IC 曲线斜率，若近期趋势转负说明因子有效性在衰减，应及时替换",
                    type: "warn" as const,
                  },
                ]

                return (
                  <InsightBox
                    verdict={verdict}
                    summary={summary}
                    findings={findings}
                    recommendations={recommendations}
                  />
                )
              })()}

              {/* 因子有效性 → 策略类型推荐 */}
              {(() => {
                const icMean5 = result.ic_mean?.[5] ?? 0
                const isPositive = icMean5 > 0
                const isStrong = Math.abs(icMean5) >= 0.05
                const strategies = isStrong
                  ? (isPositive
                    ? [{ name: "momentum", label: "价格动量", reason: "因子正向有效，适合趋势跟踪" },
                       { name: "double_ma", label: "双均线交叉", reason: "结合均线捕捉趋势方向" }]
                    : [{ name: "rsi_mean_reversion", label: "RSI 均值回归", reason: "因子负向，价格往往超买后回归" },
                       { name: "bollinger", label: "布林带回归", reason: "结合波动率通道捕捉反转" }])
                  : [{ name: "multi_factor", label: "多因子模型", reason: "单因子 IC 不足，建议组合多因子提升信号稳定性" }]

                return (
                  <div className="card border-[#30363d] space-y-3">
                    <p className="text-xs font-semibold text-[#8b949e]">
                      🎯 因子结果 → 推荐策略类型
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {strategies.map((s) => (
                        <Link key={s.name}
                          to={`/backtest?strategy=${s.name}`}
                          className="flex-1 min-w-[140px] px-3 py-2.5 rounded-lg border border-[#58a6ff]/25 bg-[#111d2e] hover:bg-[#58a6ff]/10 transition-colors">
                          <p className="text-xs font-medium text-[#58a6ff]">{s.label}</p>
                          <p className="text-[10px] text-[#6e7681] mt-0.5">{s.reason}</p>
                          <p className="text-[9px] text-[#58a6ff]/60 mt-1">→ 点击前往回测</p>
                        </Link>
                      ))}
                      <Link to="/portfolio-optimizer"
                        className="flex-1 min-w-[140px] px-3 py-2.5 rounded-lg border border-[#3fb950]/25 bg-[#0d2018] hover:bg-[#3fb950]/10 transition-colors">
                        <p className="text-xs font-medium text-[#3fb950]">组合权重优化</p>
                        <p className="text-[10px] text-[#6e7681] mt-0.5">用因子筛选结果调整持仓权重</p>
                        <p className="text-[9px] text-[#3fb950]/60 mt-1">→ 点击前往组合优化</p>
                      </Link>
                    </div>
                  </div>
                )
              })()}

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
      )}
    </AppShell>
  )
}
