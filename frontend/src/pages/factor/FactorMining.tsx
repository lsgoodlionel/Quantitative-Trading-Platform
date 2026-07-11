import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"
import type { Market, Frequency } from "@/types"
import {
  useFactorMine, useRecordExperiment,
  type MineResult, type MinedCandidate,
} from "@/hooks/useFactorMining"
import { MARKETS, FREQS, parseUniverse, fmt } from "./universeConfig"

// ── 遗传算法预设档位（简化用户心智：无需逐个调超参）────────────────
interface GaPreset {
  key: string
  label: string
  hint: string
  population: number
  generations: number
}

const GA_PRESETS: GaPreset[] = [
  { key: "fast", label: "快速", hint: "小种群少代数，秒级出结果", population: 16, generations: 8 },
  { key: "balanced", label: "均衡", hint: "推荐档位，兼顾速度与质量", population: 24, generations: 12 },
  { key: "thorough", label: "深挖", hint: "大种群多代数，更充分但更慢", population: 40, generations: 20 },
]

const FORWARD_PERIODS = [5, 10, 20]

function fitnessColor(v: number | null): string {
  if (v == null || isNaN(v)) return "#8b949e"
  return v > 0 ? "#3fb950" : v <= -5 ? "#f85149" : "#e6edf3"
}

interface FactorMiningProps {
  market: Market
  freq: Frequency
}

export function FactorMining({ market: initMarket, freq: initFreq }: FactorMiningProps) {
  const { mutate, isPending, data: result, error } = useFactorMine()
  const { toast } = useToast()

  const [universe, setUniverse] = useState("AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AVGO")
  const [market, setMarket] = useState<Market>(initMarket)
  const [freq, setFreq] = useState<Frequency>(initFreq)
  const [forwardPeriod, setForwardPeriod] = useState(5)
  const [preset, setPreset] = useState("balanced")
  const [maxDepth, setMaxDepth] = useState(4)
  const [seed, setSeed] = useState(42)
  const [recordBest, setRecordBest] = useState(true)

  const symbolCount = parseUniverse(universe).length

  function handleRun() {
    const symbols = parseUniverse(universe)
    if (symbols.length < 3) { toast("遗传挖掘至少需要 3 只标的", "warning"); return }
    const ga = GA_PRESETS.find((p) => p.key === preset) ?? GA_PRESETS[1]
    mutate({
      symbols, market, frequency: freq,
      forward_period: forwardPeriod,
      population_size: ga.population,
      generations: ga.generations,
      max_depth: maxDepth,
      top_k: 12,
      seed,
      record_best: recordBest,
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
            <p className="text-[10px] text-[#6e7681] mt-1">
              遗传算法在标的池上进化 RPN 公式，用成本感知适应度打分选优
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <MarketPicker market={market} onMarket={setMarket} />
            <FreqPicker freq={freq} onFreq={setFreq} />
          </div>

          <div>
            <label className="label block mb-1">前瞻收益期</label>
            <div className="flex gap-1">
              {FORWARD_PERIODS.map((p) => (
                <button key={p} onClick={() => setForwardPeriod(p)}
                  className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                    forwardPeriod === p
                      ? "bg-[#bc8cff]/25 text-[#bc8cff] border border-[#bc8cff]/30"
                      : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                  }`}>{p}日</button>
              ))}
            </div>
          </div>
        </div>

        {/* 进化强度 */}
        <div className="card space-y-3">
          <label className="label">进化强度</label>
          <div className="space-y-1.5">
            {GA_PRESETS.map((p) => (
              <button key={p.key} onClick={() => setPreset(p.key)}
                className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${
                  preset === p.key
                    ? "bg-[#3fb950]/15 border-[#3fb950]/40"
                    : "border-[#30363d] hover:border-[#484f58]"
                }`}>
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-medium ${preset === p.key ? "text-[#3fb950]" : "text-[#e6edf3]"}`}>
                    {p.label}
                  </span>
                  <span className="text-[9px] text-[#6e7681] font-mono">
                    {p.population}×{p.generations}
                  </span>
                </div>
                <p className="text-[10px] text-[#6e7681] mt-0.5">{p.hint}</p>
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3 pt-1">
            <div>
              <label className="label block mb-1">最大公式深度</label>
              <input type="number" min={2} max={6} className="input w-full text-xs"
                value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))} />
            </div>
            <div>
              <label className="label block mb-1">随机种子</label>
              <input type="number" min={0} className="input w-full text-xs"
                value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
            </div>
          </div>
          <p className="text-[10px] text-[#6e7681]">相同种子 + 相同参数 → 相同结果（可复现）</p>

          <label className="flex items-center gap-2 text-xs text-[#8b949e] cursor-pointer pt-1">
            <input type="checkbox" checked={recordBest}
              onChange={(e) => setRecordBest(e.target.checked)} className="accent-[#bc8cff]" />
            自动记录最优个体到排行榜
          </label>
        </div>

        <button className="btn btn-primary w-full" onClick={handleRun} disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🧬 开始遗传挖掘"}
        </button>
        {error && <p className="text-xs text-[#f85149] leading-snug">{error.message}</p>}
      </div>

      {/* ── 结果 ── */}
      <div className="xl:col-span-3 space-y-6">
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-64 text-[#6e7681] text-sm text-center px-6">
            配置标的池与进化强度后点击「开始遗传挖掘」，算法会自动进化出成本感知适应度最高的因子公式
          </div>
        )}
        {isPending && (
          <div className="card flex flex-col items-center justify-center h-64 gap-3">
            <Spinner />
            <p className="text-xs text-[#6e7681]">种群进化中，深挖档位可能需要数十秒…</p>
          </div>
        )}
        {result && <MiningResultView result={result} />}
      </div>
    </div>
  )
}

// ── 结果视图 ──────────────────────────────────────────────────────

function MiningResultView({ result }: { result: MineResult }) {
  const historyData = useMemo(
    () => result.history.map((h) => ({
      gen: h.generation + 1,
      best: h.best_fitness,
      mean: h.mean_fitness,
    })),
    [result],
  )

  return (
    <>
      {/* 概览 */}
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-2">
        <Stat label="评估个体" value={String(result.n_evaluated)} />
        <Stat label="去重公式" value={String(result.n_unique)} />
        <Stat label="标的数" value={String(result.symbols.length)} />
        <Stat label="前瞻期" value={`${result.forward_period}日`} />
        {result.recorded_id && (
          <div className="ml-auto text-right">
            <p className="text-[10px] text-[#6e7681]">已记录</p>
            <Link to="#" className="text-xs font-mono text-[#3fb950]">
              ✓ 最优已入排行榜
            </Link>
          </div>
        )}
      </div>

      {/* 最优公式 */}
      {result.best && <BestCard best={result.best} />}

      {/* 进化曲线 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">适应度进化曲线</h3>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={historyData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis dataKey="gen" tick={{ fill: "#8b949e", fontSize: 10 }}
              label={{ value: "代", position: "insideBottomRight", offset: -2, fill: "#6e7681", fontSize: 10 }} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={48} />
            <ReferenceLine y={0} stroke="#6e7681" />
            <Tooltip
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
              labelFormatter={(v) => `第 ${v} 代`}
              formatter={(val: number, name: string) => [val?.toFixed(3), name === "best" ? "最优" : "平均"]}
            />
            <Line dataKey="best" stroke="#3fb950" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="mean" stroke="#58a6ff" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
        <div className="flex gap-4 mt-2 text-xs text-[#8b949e]">
          <span className="flex items-center gap-1.5"><span className="w-2 h-0.5 inline-block bg-[#3fb950]" />最优适应度</span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-0.5 inline-block bg-[#58a6ff]" />种群平均</span>
        </div>
      </div>

      {/* 候选榜 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">候选因子公式（按适应度）</h3>
        <CandidateTable candidates={result.candidates} market={result.market} symbols={result.symbols} forwardPeriod={result.forward_period} />
      </div>

      <MiningInsight result={result} />
    </>
  )
}

function BestCard({ best }: { best: MinedCandidate }) {
  return (
    <div className="card border-[#3fb950]/30 bg-[#0d2018]/40">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[#3fb950]">🏆 最优因子公式</h3>
        <span className="text-[10px] text-[#6e7681]">RPN 逆波兰表达式</span>
      </div>
      <div className="bg-[#0d1117] rounded-lg p-3 mb-3 overflow-x-auto">
        <code className="text-xs font-mono text-[#e6edf3] whitespace-nowrap">
          {best.tokens.map((t, i) => (
            <span key={i} className="inline-block px-1.5 py-0.5 mr-1 rounded bg-[#21262d] text-[#bc8cff]">{t}</span>
          ))}
        </code>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Metric label="适应度" value={fmt(best.fitness, 3)} color={fitnessColor(best.fitness)} />
        <Metric label="IC 均值" value={fmt(best.ic_mean)} />
        <Metric label="RankIC" value={fmt(best.rank_ic_mean)} />
        <Metric label="ICIR" value={fmt(best.icir, 3)} />
      </div>
    </div>
  )
}

function CandidateTable({
  candidates, market, symbols, forwardPeriod,
}: {
  candidates: MinedCandidate[]
  market: string
  symbols: string[]
  forwardPeriod: number
}) {
  const { mutate: record, isPending } = useRecordExperiment()
  const { toast } = useToast()
  const [recordedExprs, setRecordedExprs] = useState<Set<string>>(new Set())

  function handleRecord(c: MinedCandidate) {
    record(
      {
        kind: "genetic_mining",
        name: c.expr.slice(0, 120),
        market,
        symbols,
        tokens: c.tokens,
        params: { forward_period: forwardPeriod },
        metrics: {
          ic_mean: c.ic_mean, rank_ic_mean: c.rank_ic_mean,
          icir: c.icir, fitness: c.fitness, mean_net_return: c.mean_net_return,
        },
        note: "手动收藏候选",
      },
      {
        onSuccess: () => {
          setRecordedExprs((cur) => new Set(cur).add(c.expr))
          toast("已记录到排行榜", "success")
        },
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#8b949e] border-b border-[#21262d]">
            <th className="text-left py-2 pr-3">#</th>
            <th className="text-left py-2 pr-3">公式（RPN）</th>
            <th className="text-right py-2 pr-3">适应度</th>
            <th className="text-right py-2 pr-3">IC</th>
            <th className="text-right py-2 pr-3">RankIC</th>
            <th className="text-right py-2 pr-3">ICIR</th>
            <th className="text-right py-2 pr-3">换手</th>
            <th className="text-right py-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c, i) => (
            <tr key={c.expr} className="border-b border-[#21262d]/40 last:border-0 hover:bg-[#21262d]/30">
              <td className="py-1.5 pr-3 text-[#6e7681] font-mono">{i + 1}</td>
              <td className="py-1.5 pr-3 font-mono text-[#e6edf3] max-w-[220px] truncate" title={c.expr}>{c.expr}</td>
              <td className="py-1.5 pr-3 text-right font-mono" style={{ color: fitnessColor(c.fitness) }}>{fmt(c.fitness, 3)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{fmt(c.ic_mean)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{fmt(c.rank_ic_mean)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{fmt(c.icir, 3)}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-[#6e7681]">{fmt(c.turnover, 1)}</td>
              <td className="py-1.5 text-right">
                {recordedExprs.has(c.expr) ? (
                  <span className="text-[10px] text-[#3fb950]">✓ 已记录</span>
                ) : (
                  <button onClick={() => handleRecord(c)} disabled={isPending}
                    className="text-[10px] px-2 py-1 rounded border border-[#30363d] text-[#8b949e] hover:text-[#bc8cff] hover:border-[#bc8cff]/40 transition-colors">
                    记录
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MiningInsight({ result }: { result: MineResult }) {
  const best = result.best
  const bestFit = best?.fitness ?? null
  const bestRankIc = best?.rank_ic_mean ?? null
  const improved = result.history.length >= 2
    && (result.history[result.history.length - 1].best_fitness ?? -Infinity)
       > (result.history[0].best_fitness ?? -Infinity)

  const effective = bestFit != null && bestFit > 0
  const verdict: InsightVerdict = effective ? "good" : bestFit != null && bestFit > -5 ? "warn" : "bad"

  const summary = best
    ? `遗传算法评估 ${result.n_evaluated} 个个体（去重 ${result.n_unique} 条公式），最优公式「${best.expr}」适应度为 ${fmt(bestFit, 3)}，RankIC ${fmt(bestRankIc)}。${effective ? "净收益为正，具备成本后盈利潜力。" : "净收益未转正，建议扩大标的池、延长前瞻期或提高进化强度。"}`
    : "未产生有效候选，请检查标的池与数据可用性。"

  const findings: InsightItem[] = [
    {
      text: `最优适应度 ${fmt(bestFit, 3)} — ${effective ? "扣除费用/滑点后净收益为正" : "尚未跨越成本门槛，因子毛收益不足以覆盖交易成本"}`,
      type: effective ? "good" : "warn",
    },
    {
      text: `进化收敛：末代最优 ${improved ? "高于" : "未高于"}首代 — ${improved ? "种群持续朝高分方向进化" : "可能过早收敛，尝试提高变异或换种子"}`,
      type: improved ? "good" : "neutral",
    },
    {
      text: `搜索覆盖 ${result.n_unique} 条不同公式 — ${result.n_unique >= 30 ? "搜索空间探索充分" : "探索偏少，深挖档位可覆盖更多结构"}`,
      type: result.n_unique >= 30 ? "good" : "neutral",
    },
  ]

  const recommendations: InsightItem[] = [
    {
      text: "把最优公式带回「公式因子」页签细看",
      sub: "复制 RPN token 到公式构建器，查看 IC 时序与分位单调性",
      type: "neutral",
    },
    ...(effective ? [{
      text: "对头部候选做成本感知适应度复核",
      sub: "在「成本感知适应度」页签用不同费率/门槛压力测试稳健性",
      type: "good" as const,
    }] : [{
      text: "调整搜索设置后重试",
      sub: "扩大标的池、切换前瞻期或选「深挖」档位，给算法更多探索空间",
      type: "warn" as const,
    }]),
    {
      text: "在实验记录页对比历史挖掘",
      sub: "切到「实验记录」页签查看排行榜，横向比较不同标的池/参数下的最优因子",
      type: "neutral",
    },
  ]

  return <InsightBox verdict={verdict} summary={summary} findings={findings} recommendations={recommendations} />
}

// ── 小组件 ────────────────────────────────────────────────────────

function MarketPicker({ market, onMarket }: { market: Market; onMarket: (m: Market) => void }) {
  return (
    <div>
      <label className="label block mb-1">市场</label>
      <div className="flex gap-1">
        {MARKETS.map((m) => (
          <button key={m} onClick={() => onMarket(m)}
            className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
              market === m
                ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
            }`}>{m}</button>
        ))}
      </div>
    </div>
  )
}

function FreqPicker({ freq, onFreq }: { freq: Frequency; onFreq: (f: Frequency) => void }) {
  return (
    <div>
      <label className="label block mb-1">频率</label>
      <div className="flex gap-1">
        {FREQS.map((f) => (
          <button key={f} onClick={() => onFreq(f)}
            className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
              freq === f
                ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
            }`}>{f}</button>
        ))}
      </div>
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

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <p className="text-[10px] text-[#6e7681]">{label}</p>
      <p className="text-sm font-mono font-semibold" style={{ color: color ?? "#e6edf3" }}>{value}</p>
    </div>
  )
}
