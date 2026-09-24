import { useMemo, useState } from "react"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"
import type { FactorInfo } from "@/hooks/useFactorAnalysis"
import { useFactorFitness, type FactorFitnessResult } from "@/hooks/useFactorProcessors"
import type { Market, Frequency } from "@/types"
import { UniverseHeader, parseUniverse, fmt, FORMULA_SENTINEL } from "./universeConfig"

interface CostParam {
  key: "fee_rate" | "max_impact" | "trade_notional" | "entry_threshold" | "drawdown_bar" | "drawdown_penalty" | "min_activity"
  label: string
  step: number
  hint: string
}

const COST_PARAMS: CostParam[] = [
  { key: "fee_rate", label: "单边费率", step: 0.0001, hint: "0.001 = 10bps" },
  { key: "entry_threshold", label: "开仓门槛", step: 0.01, hint: "sigmoid(信号) > 阈值 才开仓" },
  { key: "max_impact", label: "冲击上限", step: 0.001, hint: "每笔滑点上限" },
  { key: "trade_notional", label: "订单规模", step: 1000, hint: "冲击模型假设名义金额" },
  { key: "drawdown_bar", label: "大回撤阈值", step: 0.01, hint: "单 bar 亏损超过则计一次" },
  { key: "drawdown_penalty", label: "回撤惩罚", step: 0.5, hint: "每次大回撤扣分权重" },
  { key: "min_activity", label: "最少活跃数", step: 1, hint: "低于则适应度触底" },
]

const DEFAULTS: Record<CostParam["key"], number> = {
  fee_rate: 0.001, max_impact: 0.02, trade_notional: 10000,
  entry_threshold: 0.85, drawdown_bar: 0.05, drawdown_penalty: 2, min_activity: 5,
}

interface FactorFitnessProps {
  factorList: FactorInfo[]
  formulaTokens: string[]
}

export function FactorFitness({ factorList, formulaTokens }: FactorFitnessProps) {
  const { mutate, isPending, data: result, error } = useFactorFitness()
  const { toast } = useToast()

  const [universe, setUniverse] = useState("AAPL, MSFT, GOOGL, AMZN, NVDA")
  const [market, setMarket] = useState<Market>("US")
  const [freq, setFreq] = useState<Frequency>("1d")
  const [baseFactor, setBaseFactor] = useState("momentum_20")
  const [forwardPeriod, setForwardPeriod] = useState(5)
  const [params, setParams] = useState<Record<CostParam["key"], number>>({ ...DEFAULTS })
  const [showAdvanced, setShowAdvanced] = useState(false)

  function handleRun() {
    const symbols = parseUniverse(universe)
    if (symbols.length < 2) { toast("至少输入 2 只标的", "warning"); return }
    if (baseFactor === FORMULA_SENTINEL && formulaTokens.length === 0) {
      toast("请先在「公式因子」页签构建公式", "warning"); return
    }
    mutate({
      symbols, market, frequency: freq,
      base_factor: baseFactor,
      tokens: baseFactor === FORMULA_SENTINEL ? formulaTokens : undefined,
      forward_period: forwardPeriod,
      ...params,
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      {/* ── 配置 ── */}
      <div className="xl:col-span-1 space-y-4">
        <UniverseHeader
          universe={universe} onUniverse={setUniverse}
          market={market} onMarket={setMarket}
          freq={freq} onFreq={setFreq}
          baseFactor={baseFactor} onBaseFactor={setBaseFactor}
          factorList={factorList}
        />

        <div className="card space-y-3">
          <div>
            <label className="label block mb-1">前瞻收益期</label>
            <input type="number" min={1} max={60} className="input w-full text-xs"
              value={forwardPeriod} onChange={(e) => setForwardPeriod(Number(e.target.value))} />
          </div>

          <button onClick={() => setShowAdvanced((s) => !s)}
            className="text-xs text-[#58a6ff] hover:underline">
            {showAdvanced ? "▾ 隐藏成本模型参数" : "▸ 成本模型参数（高级）"}
          </button>

          {showAdvanced && (
            <div className="space-y-2 pt-1">
              {COST_PARAMS.map((p) => (
                <label key={p.key} className="flex items-center justify-between gap-2 text-[11px] text-[#8b949e]" title={p.hint}>
                  <span>{p.label}</span>
                  <input type="number" step={p.step} className="input text-[11px] py-0.5 w-24"
                    value={params[p.key]}
                    onChange={(e) => setParams((c) => ({ ...c, [p.key]: Number(e.target.value) }))} />
                </label>
              ))}
              <button onClick={() => setParams({ ...DEFAULTS })}
                className="text-[10px] text-[#6e7681] hover:text-[#e6edf3]">重置为默认</button>
            </div>
          )}
        </div>

        <button className="btn btn-primary w-full" onClick={handleRun} disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "计算因子适应度"}
        </button>
        {error && <p className="text-xs text-[#f85149] leading-snug">{error.message}</p>}
      </div>

      {/* ── 结果 ── */}
      <div className="xl:col-span-3 space-y-6">
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-64 text-[#6e7681] text-sm">
            配置标的池与成本模型后点击「计算因子适应度」
          </div>
        )}
        {isPending && <div className="card flex items-center justify-center h-64"><Spinner /></div>}
        {result && <FitnessResultView result={result} />}
      </div>
    </div>
  )
}

// ── 结果视图 ────────────────────────────────────────────────────────

function FitnessResultView({ result }: { result: FactorFitnessResult }) {
  const scoreColor = result.fitness >= 0 ? "#3fb950" : "#f85149"
  const perInst = useMemo(
    () => Object.entries(result.per_instrument_score)
      .map(([instrument, score]) => ({ instrument, score }))
      .sort((a, b) => b.score - a.score),
    [result],
  )

  return (
    <>
      {/* 适应度标量 */}
      <div className="card flex flex-col sm:flex-row items-center gap-6">
        <div className="text-center sm:border-r sm:border-[#21262d] sm:pr-6">
          <p className="text-xs text-[#8b949e] mb-1">因子适应度（成本感知）</p>
          <p className="text-4xl font-mono font-bold" style={{ color: scoreColor }}>
            {fmt(result.fitness, 3)}
          </p>
          <p className="text-[10px] text-[#6e7681] mt-1">跨 universe 中位数 · 越高越好</p>
        </div>
        <div className="flex-1 grid grid-cols-2 sm:grid-cols-3 gap-3 w-full">
          <Metric label="净收益均值" value={fmt(result.mean_net_return, 3)} color={result.mean_net_return >= 0 ? "#3fb950" : "#f85149"} />
          <Metric label="毛收益" value={fmt(result.gross_return, 3)} />
          <Metric label="总成本" value={fmt(result.total_cost, 3)} color="#f85149" />
          <Metric label="换手" value={fmt(result.turnover, 1)} />
          <Metric label="平均活跃度" value={fmt(result.avg_activity, 1)} />
          <Metric label="大回撤次数" value={String(result.n_big_drawdowns)} color="#e3b341" />
        </div>
      </div>

      {/* 活跃度门槛 */}
      <div className="card flex items-center gap-3">
        <span className={`badge ${result.activity_gate_passed
          ? "text-[#3fb950] border-[#3fb950]/30" : "text-[#f85149] border-[#f85149]/30"}`}>
          {result.activity_gate_passed ? "✓ 活跃度门槛通过" : "✗ 活跃度不足（适应度触底）"}
        </span>
        <span className="text-xs text-[#6e7681]">
          最少活跃数要求 {result.config_used.min_activity} · 开仓门槛 {fmt(result.config_used.entry_threshold, 2)} · 费率 {(result.config_used.fee_rate * 10000).toFixed(0)}bps
        </span>
      </div>

      {/* 逐标的评分 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">逐标的评分（稳健性视图）</h3>
        <ResponsiveContainer width="100%" height={Math.max(160, perInst.length * 28)}>
          <BarChart data={perInst} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" horizontal={false} />
            <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 10 }} />
            <YAxis type="category" dataKey="instrument" tick={{ fill: "#e6edf3", fontSize: 11 }} width={64} />
            <ReferenceLine x={0} stroke="#6e7681" />
            <Tooltip formatter={(v: number) => [v.toFixed(3), "评分"]}
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
              itemStyle={{ color: "#e6edf3" }} cursor={{ fill: "#ffffff08" }} />
            <Bar dataKey="score" radius={[0, 3, 3, 0]} isAnimationActive={false}>
              {perInst.map((d, i) => <Cell key={i} fill={d.score >= 0 ? "#3fb950" : "#f85149"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <p className="text-[10px] text-[#6e7681] mt-2">
          适应度取逐标的评分的中位数，避免少数幸运标的主导整体评价。
        </p>
      </div>

      <FitnessInsight result={result} />
    </>
  )
}

function Metric({ label, value, color = "#e6edf3" }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg border border-[#21262d] bg-[#0d1117] px-3 py-2">
      <p className="text-[10px] text-[#6e7681]">{label}</p>
      <p className="text-base font-mono font-semibold" style={{ color }}>{value}</p>
    </div>
  )
}

function FitnessInsight({ result }: { result: FactorFitnessResult }) {
  const costRatio = result.gross_return !== 0 ? result.total_cost / Math.abs(result.gross_return) : 0
  const verdict: InsightVerdict =
    !result.activity_gate_passed ? "bad"
    : result.fitness > 0 ? "good"
    : "warn"

  const summary = !result.activity_gate_passed
    ? `因子「${result.base_factor}」活跃度不足，未触发足够交易，适应度触底为 ${fmt(result.fitness, 2)}，不具备实盘可交易性。`
    : `因子「${result.base_factor}」在 ${result.symbols.length} 只标的、${result.forward_period} 日前瞻期下成本感知适应度为 ${fmt(result.fitness, 3)}，${result.fitness > 0 ? "扣除交易成本后仍为正，具备实盘潜力" : "扣除成本后转负，边际收益难以覆盖交易摩擦"}。`

  const findings: InsightItem[] = [
    {
      text: `毛收益 ${fmt(result.gross_return, 3)} → 净收益 ${fmt(result.mean_net_return, 3)}，交易成本吞噬 ${(costRatio * 100).toFixed(1)}% 毛利`,
      type: costRatio < 0.3 ? "good" : costRatio < 0.7 ? "warn" : "bad",
    },
    {
      text: `换手 ${fmt(result.turnover, 1)} · 总成本 ${fmt(result.total_cost, 3)} — ${result.turnover > 0 && result.total_cost / result.turnover > 0.005 ? "高频交易，成本敏感" : "换手成本可控"}`,
      type: "neutral",
    },
    {
      text: `大回撤 ${result.n_big_drawdowns} 次 — ${result.n_big_drawdowns === 0 ? "无显著单期大幅回撤" : "存在单期大幅回撤，稳定性受损"}`,
      type: result.n_big_drawdowns === 0 ? "good" : "warn",
    },
  ]

  const recommendations: InsightItem[] = [
    ...(result.fitness <= 0 && result.activity_gate_passed ? [{
      text: "降低换手或提高开仓门槛",
      sub: "当前成本侵蚀过多，可提高 entry_threshold 只保留强信号，或延长前瞻期减少调仓",
      type: "warn" as const,
    }] : []),
    ...(!result.activity_gate_passed ? [{
      text: "放宽开仓门槛或更换因子",
      sub: "sigmoid(信号) 极少超过阈值，可调低 entry_threshold 或选用信号更强的因子",
      type: "bad" as const,
    }] : []),
    ...(result.fitness > 0 ? [{
      text: "纳入多因子组合并做样本外验证",
      sub: "成本感知适应度为正，建议扩大 universe 并在不同时间段验证稳健性",
      type: "good" as const,
    }] : []),
  ]

  return <InsightBox verdict={verdict} summary={summary} findings={findings} recommendations={recommendations} />
}
