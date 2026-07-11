import { useMemo, useState } from "react"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"
import type { Market, Frequency } from "@/types"
import {
  useFactorLibraryCatalog, useFactorLibraryAnalyze,
  type FactorICRow, type LibraryMethod, type FactorLibraryAnalyzeResult,
} from "@/hooks/useFactorLibrary"
import { MARKETS, FREQS, parseUniverse, fmt } from "./universeConfig"

const METHODS: { key: LibraryMethod; label: string; hint: string }[] = [
  { key: "rank_ic", label: "RankIC", hint: "按秩相关排序，抗离群更稳健" },
  { key: "ic",      label: "IC",     hint: "按 Pearson 相关排序" },
]

const TOP_K_OPTIONS = [15, 30, 60]

function icColor(v: number | null): string {
  if (v == null || isNaN(v)) return "#8b949e"
  return Math.abs(v) >= 0.05 ? (v > 0 ? "#3fb950" : "#f85149") : "#e6edf3"
}

interface FactorLibraryProps {
  market: Market
  freq: Frequency
}

export function FactorLibrary({ market: initMarket, freq: initFreq }: FactorLibraryProps) {
  const { data: catalog, isLoading: catalogLoading } = useFactorLibraryCatalog()
  const { mutate, isPending, data: result, error } = useFactorLibraryAnalyze()
  const { toast } = useToast()

  const [universe, setUniverse] = useState("AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AVGO")
  const [market, setMarket] = useState<Market>(initMarket)
  const [freq, setFreq] = useState<Frequency>(initFreq)
  const [forwardPeriod, setForwardPeriod] = useState(10)
  const [method, setMethod] = useState<LibraryMethod>("rank_ic")
  const [topK, setTopK] = useState(30)
  const [selectedGroups, setSelectedGroups] = useState<string[]>([])

  const symbolCount = parseUniverse(universe).length

  function toggleGroup(g: string) {
    setSelectedGroups((cur) => (cur.includes(g) ? cur.filter((x) => x !== g) : [...cur, g]))
  }

  function handleRun() {
    const symbols = parseUniverse(universe)
    if (symbols.length < 3) { toast("横截面 IC 至少需要 3 只标的", "warning"); return }
    mutate({
      symbols, market, frequency: freq,
      forward_period: forwardPeriod,
      groups: selectedGroups.length > 0 ? selectedGroups : undefined,
      method, top_k: topK,
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      {/* ── 配置 ── */}
      <div className="xl:col-span-1 space-y-4">
        <div className="card space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="label">标的池（Universe）</label>
              <span className="text-[10px] text-[#6e7681]">{symbolCount} 只 · 需 ≥ 3</span>
            </div>
            <textarea
              className="input w-full font-mono text-xs h-16 resize-none uppercase"
              value={universe}
              onChange={(e) => setUniverse(e.target.value)}
              placeholder="AAPL, MSFT, GOOGL, ..."
            />
            <p className="text-[10px] text-[#6e7681] mt-1">因子库在每个交易日跨标的算横截面 IC，标的越多越稳</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">市场</label>
              <div className="flex gap-1">
                {MARKETS.map((m) => (
                  <button key={m} onClick={() => setMarket(m)}
                    className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                      market === m
                        ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                        : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                    }`}>{m}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="label block mb-1">频率</label>
              <div className="flex gap-1">
                {FREQS.map((f) => (
                  <button key={f} onClick={() => setFreq(f)}
                    className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                      freq === f
                        ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                        : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                    }`}>{f}</button>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">前瞻收益期</label>
              <input type="number" min={1} max={60} className="input w-full text-xs"
                value={forwardPeriod} onChange={(e) => setForwardPeriod(Number(e.target.value))} />
            </div>
            <div>
              <label className="label block mb-1">取前</label>
              <div className="flex gap-1">
                {TOP_K_OPTIONS.map((k) => (
                  <button key={k} onClick={() => setTopK(k)}
                    className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                      topK === k
                        ? "bg-[#bc8cff]/25 text-[#bc8cff] border border-[#bc8cff]/30"
                        : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                    }`}>{k}</button>
                ))}
              </div>
            </div>
          </div>

          <div>
            <label className="label block mb-1">排序口径</label>
            <div className="flex gap-1">
              {METHODS.map((m) => (
                <button key={m.key} onClick={() => setMethod(m.key)} title={m.hint}
                  className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                    method === m.key
                      ? "bg-[#3fb950]/20 text-[#3fb950] border border-[#3fb950]/30"
                      : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                  }`}>{m.label}</button>
              ))}
            </div>
          </div>
        </div>

        {/* 分组过滤 */}
        <div className="card">
          <div className="flex items-center justify-between mb-2">
            <label className="label">因子分组</label>
            <span className="text-[10px] text-[#6e7681]">
              {catalog ? `${catalog.n_factors} 个因子` : "…"} · {selectedGroups.length === 0 ? "全部" : `${selectedGroups.length} 组`}
            </span>
          </div>
          {catalogLoading ? (
            <Spinner size="sm" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {(catalog?.groups ?? []).map((g) => {
                const active = selectedGroups.includes(g.name)
                return (
                  <button key={g.name} onClick={() => toggleGroup(g.name)} title={g.description}
                    className={`px-2 py-1 rounded text-[11px] transition-colors border ${
                      active
                        ? "bg-[#58a6ff]/20 text-[#58a6ff] border-[#58a6ff]/40"
                        : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                    }`}>
                    {g.name} <span className="text-[9px] opacity-60">{g.count}</span>
                  </button>
                )
              })}
            </div>
          )}
          <p className="text-[10px] text-[#6e7681] mt-2">不选则分析全部分组；缩小范围可加速计算</p>
        </div>

        <button className="btn btn-primary w-full" onClick={handleRun} disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "计算因子库 IC 排行"}
        </button>
        {error && <p className="text-xs text-[#f85149] leading-snug">{error.message}</p>}
      </div>

      {/* ── 结果 ── */}
      <div className="xl:col-span-3 space-y-6">
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-64 text-[#6e7681] text-sm">
            配置标的池后点击「计算因子库 IC 排行」，从数百个因子中挑出预测力最强的
          </div>
        )}
        {isPending && <div className="card flex items-center justify-center h-64"><Spinner /></div>}
        {result && <LibraryResultView result={result} method={method} />}
      </div>
    </div>
  )
}

// ── 结果视图 ────────────────────────────────────────────────────────

function LibraryResultView({ result, method }: { result: FactorLibraryAnalyzeResult; method: LibraryMethod }) {
  const primaryKey = method === "rank_ic" ? "rank_ic_mean" : "ic_mean"

  const chartData = useMemo(
    () => result.ranking.slice(0, 15).map((r) => ({
      name: r.name,
      value: (r[primaryKey] as number | null) ?? 0,
    })),
    [result, primaryKey],
  )

  return (
    <>
      {/* 概览 */}
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-2">
        <Stat label="参与因子" value={String(result.n_factors)} />
        <Stat label="标的数" value={String(result.n_symbols)} />
        <Stat label="横截面日数" value={String(result.n_dates)} />
        <Stat label="前瞻期" value={`${result.forward_period}日`} />
        {result.best && (
          <div className="ml-auto text-right">
            <p className="text-[10px] text-[#6e7681]">最强因子（{method === "rank_ic" ? "RankIC" : "IC"}）</p>
            <p className="text-sm font-mono font-semibold text-[#bc8cff]">
              {result.best.name} · {fmt((result.best[primaryKey] as number | null), 4)}
            </p>
          </div>
        )}
      </div>

      {/* Top15 条形图 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          Top 15 因子 · {method === "rank_ic" ? "RankIC 均值" : "IC 均值"}
        </h3>
        <ResponsiveContainer width="100%" height={Math.max(200, chartData.length * 26)}>
          <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" horizontal={false} />
            <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 10 }} domain={["auto", "auto"]} />
            <YAxis type="category" dataKey="name" tick={{ fill: "#e6edf3", fontSize: 10 }} width={64} />
            <ReferenceLine x={0} stroke="#6e7681" />
            <Tooltip formatter={(v: number) => [v.toFixed(4), method === "rank_ic" ? "RankIC" : "IC"]}
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
              itemStyle={{ color: "#e6edf3" }} cursor={{ fill: "#ffffff08" }} />
            <Bar dataKey="value" radius={[0, 3, 3, 0]} isAnimationActive={false}>
              {chartData.map((d, i) => <Cell key={i} fill={icColor(d.value)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* 排行表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">因子 IC 排行</h3>
        <RankingTable rows={result.ranking} />
      </div>

      <LibraryInsight result={result} method={method} />
    </>
  )
}

function RankingTable({ rows }: { rows: FactorICRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#8b949e] border-b border-[#21262d]">
            <th className="text-left py-2 pr-3">#</th>
            <th className="text-left py-2 pr-3">因子</th>
            <th className="text-left py-2 pr-3">分组</th>
            <th className="text-right py-2 pr-3">RankIC</th>
            <th className="text-right py-2 pr-3">IC 均值</th>
            <th className="text-right py-2 pr-3">ICIR</th>
            <th className="text-right py-2 pr-3">正比率</th>
            <th className="text-right py-2">覆盖率</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.name} className="border-b border-[#21262d]/40 last:border-0 hover:bg-[#21262d]/30">
              <td className="py-1.5 pr-3 text-[#6e7681] font-mono">{i + 1}</td>
              <td className="py-1.5 pr-3">
                <span className="font-mono text-[#e6edf3]">{r.name}</span>
                <span className="block text-[9px] text-[#6e7681] font-mono">{r.expr}</span>
              </td>
              <td className="py-1.5 pr-3 text-[#8b949e]">{r.group}</td>
              <td className="py-1.5 pr-3 text-right font-mono" style={{ color: icColor(r.rank_ic_mean) }}>{fmt(r.rank_ic_mean)}</td>
              <td className="py-1.5 pr-3 text-right font-mono" style={{ color: icColor(r.ic_mean) }}>{fmt(r.ic_mean)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{fmt(r.icir, 3)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">
                {r.positive_rate == null ? "—" : `${(r.positive_rate * 100).toFixed(0)}%`}
              </td>
              <td className="py-1.5 text-right font-mono text-[#8b949e]">
                {r.coverage == null ? "—" : `${(r.coverage * 100).toFixed(0)}%`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] text-[#6e7681]">{label}</p>
      <p className="text-base font-mono font-semibold text-[#e6edf3]">{value}</p>
    </div>
  )
}

function LibraryInsight({ result, method }: { result: FactorLibraryAnalyzeResult; method: LibraryMethod }) {
  const primaryKey = method === "rank_ic" ? "rank_ic_mean" : "ic_mean"
  const best = result.best
  const bestVal = (best?.[primaryKey] as number | null) ?? 0
  const strong = result.ranking.filter((r) => Math.abs((r[primaryKey] as number | null) ?? 0) >= 0.05)

  // 分组胜出统计
  const groupWins = new Map<string, number>()
  for (const r of result.ranking.slice(0, 10)) {
    groupWins.set(r.group, (groupWins.get(r.group) ?? 0) + 1)
  }
  const topGroup = [...groupWins.entries()].sort((a, b) => b[1] - a[1])[0]

  const verdict: InsightVerdict =
    Math.abs(bestVal) >= 0.05 ? "good" : Math.abs(bestVal) >= 0.03 ? "warn" : "bad"

  const summary = best
    ? `在 ${result.n_symbols} 只标的、${result.n_dates} 个横截面日下扫描 ${result.n_factors} 个因子，最强因子「${best.name}」${method === "rank_ic" ? "RankIC" : "IC"}均值为 ${bestVal.toFixed(4)}，共 ${strong.length} 个因子达到 0.05 有效门槛。${verdict === "bad" ? "整体预测力偏弱，建议扩大标的池或更换前瞻期。" : "可作为多因子模型的候选池。"}`
    : "未产生有效排行，请检查标的池与数据可用性。"

  const findings: InsightItem[] = [
    {
      text: `最强因子 ${best?.name ?? "—"}（${best?.group ?? "—"}）${method === "rank_ic" ? "RankIC" : "IC"} ${bestVal.toFixed(4)} — ${Math.abs(bestVal) >= 0.05 ? "超过 0.05，具备横截面选股能力" : "低于 0.05，选股能力有限"}`,
      type: Math.abs(bestVal) >= 0.05 ? "good" : "warn",
    },
    {
      text: `有效因子 ${strong.length} / ${result.ranking.length} — ${strong.length >= 5 ? "有效因子充足，可构建稳健多因子组合" : strong.length >= 1 ? "有效因子偏少，组合需谨慎" : "无因子越过有效门槛"}`,
      type: strong.length >= 5 ? "good" : strong.length >= 1 ? "warn" : "bad",
    },
    ...(topGroup ? [{
      text: `Top10 中「${topGroup[0]}」类因子占 ${topGroup[1]} 席 — 该风格在当前标的池上信息含量最高`,
      type: "neutral" as const,
    }] : []),
  ]

  const recommendations: InsightItem[] = [
    {
      text: "对头部因子做横截面处理",
      sub: "在「🧪 截面处理流水线」页签对候选因子做 CSRankNorm / RobustZScore，去量纲后再入模",
      type: "neutral",
    },
    ...(strong.length >= 5 ? [{
      text: "组合正交化后纳入多因子模型",
      sub: "挑选低相关的头部因子做正交化，避免风格集中",
      type: "good" as const,
    }] : []),
    {
      text: "样本外验证 IC 稳定性",
      sub: "更换时间窗口或标的池复算，关注 ICIR 是否维持，避免过拟合",
      type: "warn",
    },
  ]

  return <InsightBox verdict={verdict} summary={summary} findings={findings} recommendations={recommendations} />
}
