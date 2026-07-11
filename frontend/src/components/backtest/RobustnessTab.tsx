import { useState } from "react"
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Line,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts"
import {
  useMcRobustness, useSignificance, MC_METRIC_LABELS,
  type McMethod, type McRobustnessResult, type SignificanceResult,
} from "@/hooks/useBacktestRobustness"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { MARKET_CFGS, today, yearsAgo } from "./config"

interface StrategyOpt {
  name: string
  description: string
}

type SubTab = "mc" | "sig"

// ── 容器：稳健性 / 显著性 二级切换 ────────────────────────────────
export function RobustnessTab({ strategies }: { strategies: StrategyOpt[] }) {
  const [sub, setSub] = useState<SubTab>("mc")

  const SUBS: { key: SubTab; label: string }[] = [
    { key: "mc", label: "🎰 蒙特卡洛稳健性" },
    { key: "sig", label: "🧪 统计显著性" },
  ]

  return (
    <div className="space-y-5">
      <div className="flex gap-2">
        {SUBS.map(({ key, label }) => (
          <button key={key}
            className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
              sub === key
                ? "border-[#58a6ff] text-[#58a6ff] bg-[#58a6ff]/10"
                : "border-[#21262d] text-[#8b949e] hover:text-[#e6edf3]"
            }`}
            onClick={() => setSub(key)}>
            {label}
          </button>
        ))}
      </div>

      {sub === "mc" ? <McPanel strategies={strategies} /> : <SigPanel strategies={strategies} />}
    </div>
  )
}

// ── 共享的基础配置字段 ───────────────────────────────────────────
interface BaseForm {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
}

function baseDefaults(): BaseForm {
  return {
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(3),
    end_date: today(),
    initial_cash: 100000,
  }
}

function BaseFields<T extends BaseForm>({
  form, strategies, set,
}: {
  form: T
  strategies: StrategyOpt[]
  set: (patch: Partial<T>) => void
}) {
  return (
    <>
      <div>
        <label className="label">策略</label>
        <select className="select w-full mt-1" value={form.strategy_name}
          onChange={(e) => set({ strategy_name: e.target.value } as Partial<T>)}>
          {strategies.map((s) => <option key={s.name} value={s.name}>{s.description || s.name}</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">市场</label>
          <select className="select w-full mt-1" value={form.market}
            onChange={(e) => set({ market: e.target.value } as Partial<T>)}>
            {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <label className="label">标的</label>
          <input className="input w-full mt-1 font-mono uppercase" value={form.symbol}
            onChange={(e) => set({ symbol: e.target.value.toUpperCase() } as Partial<T>)} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">开始日期</label>
          <input className="input w-full mt-1" type="date" value={form.start_date}
            onChange={(e) => set({ start_date: e.target.value } as Partial<T>)} />
        </div>
        <div>
          <label className="label">结束日期</label>
          <input className="input w-full mt-1" type="date" value={form.end_date}
            onChange={(e) => set({ end_date: e.target.value } as Partial<T>)} />
        </div>
      </div>
    </>
  )
}

// ══════════════════════════════════════════════════════════════════
// C4 — 蒙特卡洛稳健性
// ══════════════════════════════════════════════════════════════════

function McPanel({ strategies }: { strategies: StrategyOpt[] }) {
  const { mutate: run, isPending, data: result, error } = useMcRobustness()
  const [form, setForm] = useState({ ...baseDefaults(), method: "bootstrap" as McMethod, n_scenarios: 1000 })
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }))

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    run({ ...form, params: {} })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">蒙特卡洛稳健性</h2>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">
          对回测的逐笔盈亏做重采样，产出收益/最大回撤的置信区间，判断这条曲线有多少来自运气。
        </p>

        <BaseFields form={form} strategies={strategies} set={set} />

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">重采样方法</label>
            <select className="select w-full mt-1" value={form.method}
              onChange={(e) => set({ method: e.target.value as McMethod })}>
              <option value="bootstrap">有放回 (bootstrap)</option>
              <option value="shuffle">无放回打乱 (shuffle)</option>
            </select>
          </div>
          <div>
            <label className="label">模拟场景数</label>
            <input className="input w-full mt-1 font-mono" type="number" min={50} max={5000} step={50}
              value={form.n_scenarios}
              onChange={(e) => set({ n_scenarios: parseInt(e.target.value) || 1000 })} />
          </div>
        </div>

        <p className="text-[10px] text-[#6e7681] leading-relaxed">
          {form.method === "bootstrap"
            ? "有放回：交易集合本身改变，收益与回撤都会波动 → 真正的置信区间。"
            : "无放回：仅打乱顺序，总收益不变、回撤路径改变 → 检验回撤是否只是排序运气。"}
        </p>

        {error && <ErrorBox message={error.message} />}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🎰 运行稳健性分析"}
        </button>
      </form>

      <div className="xl:col-span-2">
        {isPending && <LoadingCard text="逐笔重采样计算中…" />}
        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置后运行蒙特卡洛稳健性"
              description="将回测的每笔盈亏反复重采样，量化收益与回撤的不确定性区间" />
          </div>
        )}
        {result && !isPending && <McView result={result} />}
      </div>
    </div>
  )
}

function McView({ result }: { result: McRobustnessResult }) {
  const chartData = result.envelope.map((e, i) => ({
    step: e.step,
    band95: [e.p5, e.p95] as [number, number],
    band50: [e.p25, e.p75] as [number, number],
    p50: e.p50,
    original: result.original_curve[i] ?? null,
  }))

  return (
    <div className="space-y-4">
      {/* 概率头条 */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            稳健性概览
            <span className="text-xs text-[#6e7681] font-normal ml-2">
              {result.n_scenarios} 场景 · {result.n_trades} 笔 · {result.method === "bootstrap" ? "有放回" : "打乱"}
            </span>
          </h3>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <SummaryStat label="盈利场景占比" value={`${(result.prob_profit * 100).toFixed(1)}%`}
            tone={result.prob_profit >= 0.5 ? "good" : "bad"} />
          <SummaryStat label="≥ 原始收益占比" value={`${(result.prob_beat_original * 100).toFixed(1)}%`} tone="neutral" />
          <SummaryStat label="交易笔数" value={String(result.n_trades)} tone="neutral" />
        </div>
        <p className="text-[11px] text-[#6e7681] mt-3 leading-relaxed">
          盈利场景占比越接近 100%，说明策略收益越不依赖特定交易的运气；偏低则脆弱。
          {result.prob_profit < 0.6 && (
            <span className="text-[#d29922]"> ⚠️ 盈利概率不高，结果稳健性有限。</span>
          )}
        </p>
      </div>

      {/* 净值包络带 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">净值包络带（按交易步）</h3>
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="step" stroke="#6e7681" tick={{ fontSize: 11 }} />
            <YAxis stroke="#6e7681" tick={{ fontSize: 11 }} width={60}
              tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} domain={["auto", "auto"]} />
            <Tooltip contentStyle={TOOLTIP_STYLE}
              formatter={(v: unknown) => (Array.isArray(v) ? v.map((x) => Math.round(Number(x))).join(" ~ ") : Math.round(Number(v)))} />
            <Area dataKey="band95" stroke="none" fill="#58a6ff" fillOpacity={0.1} name="5%~95%" />
            <Area dataKey="band50" stroke="none" fill="#58a6ff" fillOpacity={0.2} name="25%~75%" />
            <Line dataKey="p50" stroke="#58a6ff" dot={false} strokeWidth={1.5} name="中位数" />
            <Line dataKey="original" stroke="#3fb950" dot={false} strokeWidth={2} name="原始序列" />
          </ComposedChart>
        </ResponsiveContainer>
        <p className="text-[10px] text-[#6e7681] mt-2">
          浅蓝带为 5%~95% / 25%~75% 分位区间，蓝线为中位数，绿线为实际交易顺序的净值。
        </p>
      </div>

      {/* 指标置信区间表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">指标置信区间</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[#8b949e] border-b border-[#21262d]">
                <th className="text-left py-2 pr-3">指标</th>
                <th className="text-right py-2 pr-3">原始</th>
                <th className="text-right py-2 pr-3">中位数</th>
                <th className="text-right py-2 pr-3">均值±标准差</th>
                <th className="text-right py-2 pr-3">90% 区间</th>
                <th className="text-right py-2">95% 区间</th>
              </tr>
            </thead>
            <tbody>
              {result.metrics.map((m) => (
                <tr key={m.name} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                  <td className="py-1.5 pr-3 text-[#e6edf3]">{MC_METRIC_LABELS[m.name] ?? m.name}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{m.original.toFixed(2)}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{m.p50.toFixed(2)}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{m.mean.toFixed(2)} ± {m.std.toFixed(2)}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">[{m.ci90_lower.toFixed(2)}, {m.ci90_upper.toFixed(2)}]</td>
                  <td className="py-1.5 text-right font-mono text-[#6e7681]">[{m.ci95_lower.toFixed(2)}, {m.ci95_upper.toFixed(2)}]</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════
// C5 — 统计显著性
// ══════════════════════════════════════════════════════════════════

function SigPanel({ strategies }: { strategies: StrategyOpt[] }) {
  const { mutate: run, isPending, data: result, error } = useSignificance()
  const [form, setForm] = useState({ ...baseDefaults(), n_simulations: 2000 })
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }))

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    run({ ...form, params: {} })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">统计显著性检验</h2>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">
          Bootstrap 假设检验：判断策略 edge（逐笔平均盈亏 &gt; 0）是真信号还是随机噪声，并分解各规则贡献度。
        </p>

        <BaseFields form={form} strategies={strategies} set={set} />

        <div>
          <label className="label">Bootstrap 次数</label>
          <input className="input w-full mt-1 font-mono" type="number" min={100} max={20000} step={100}
            value={form.n_simulations}
            onChange={(e) => set({ n_simulations: parseInt(e.target.value) || 2000 })} />
        </div>

        {error && <ErrorBox message={error.message} />}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🧪 运行显著性检验"}
        </button>
      </form>

      <div className="xl:col-span-2">
        {isPending && <LoadingCard text="Bootstrap 重采样计算中…" />}
        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置后运行统计显著性检验"
              description="用 Bootstrap 构造零假设分布，给出策略 edge 的 p 值与规则贡献度" />
          </div>
        )}
        {result && !isPending && <SigView result={result} />}
      </div>
    </div>
  )
}

function verdict(p: number): { text: string; tone: "good" | "bad" | "warn" } {
  if (p < 0.01) return { text: "高度显著 (p < 0.01)", tone: "good" }
  if (p < 0.05) return { text: "显著 (p < 0.05)", tone: "good" }
  if (p < 0.1) return { text: "边缘显著 (p < 0.10)", tone: "warn" }
  return { text: "不显著 (p ≥ 0.10)", tone: "bad" }
}

function SigView({ result }: { result: SignificanceResult }) {
  const v = verdict(result.p_value)
  const toneColor = v.tone === "good" ? "text-[#3fb950]" : v.tone === "warn" ? "text-[#d29922]" : "text-[#f85149]"

  return (
    <div className="space-y-4">
      {/* 结论头条 */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            显著性结论
            <span className="text-xs text-[#6e7681] font-normal ml-2">{result.n_trades} 笔 · {result.n_simulations} 次重采样</span>
          </h3>
          <span className={`text-sm font-bold ${toneColor}`}>{v.text}</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SummaryStat label="p 值" value={result.p_value.toFixed(4)} tone={result.p_value < 0.05 ? "good" : "bad"} />
          <SummaryStat label="平均每笔盈亏" value={result.observed_mean_pnl.toFixed(1)}
            tone={result.observed_mean_pnl > 0 ? "good" : "bad"} />
          <SummaryStat label="t 统计量" value={result.t_stat.toFixed(2)} tone="neutral" />
          <SummaryStat label="胜率" value={`${(result.win_rate * 100).toFixed(1)}%`} tone="neutral" />
        </div>
        <p className="text-[11px] text-[#6e7681] mt-3 leading-relaxed">
          均值 95% 置信区间 [{result.ci95_mean_lower.toFixed(1)}, {result.ci95_mean_upper.toFixed(1)}]，效应量 {result.effect_size.toFixed(3)}。
          {result.ci95_mean_lower > 0
            ? <span className="text-[#3fb950]"> 置信区间完全为正，edge 稳健。</span>
            : <span className="text-[#d29922]"> 置信区间跨越 0，edge 存在不确定性。</span>}
        </p>
      </div>

      {/* 零假设分布 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">零假设分布（H0: 无 edge）</h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={result.null_hist} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="center" stroke="#6e7681" tick={{ fontSize: 10 }}
              tickFormatter={(v) => Number(v).toFixed(1)} />
            <YAxis stroke="#6e7681" tick={{ fontSize: 11 }} width={40} />
            <Tooltip contentStyle={TOOLTIP_STYLE}
              labelFormatter={(v) => `均值 ${Number(v).toFixed(2)}`} />
            <Bar dataKey="count" fill="#6e7681" fillOpacity={0.7} />
            <ReferenceLine x={nearestBin(result)} stroke="#3fb950" strokeWidth={2}
              label={{ value: "观测", fill: "#3fb950", fontSize: 11, position: "top" }} />
          </BarChart>
        </ResponsiveContainer>
        <p className="text-[10px] text-[#6e7681] mt-2">
          灰色为「策略无 edge」时平均盈亏的模拟分布；绿线为实际观测均值。观测越靠右尾，p 值越小、越可信。
        </p>
      </div>

      {/* 规则贡献度 */}
      {result.rule_contributions.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">规则贡献度（按开仓标签）</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#21262d]">
                  <th className="text-left py-2 pr-3">规则标签</th>
                  <th className="text-right py-2 pr-3">笔数</th>
                  <th className="text-right py-2 pr-3">总盈亏</th>
                  <th className="text-right py-2 pr-3">占比</th>
                  <th className="text-right py-2 pr-3">胜率</th>
                  <th className="text-right py-2 pr-3">p 值</th>
                  <th className="text-center py-2">显著</th>
                </tr>
              </thead>
              <tbody>
                {result.rule_contributions.map((c) => (
                  <tr key={c.entry_tag} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                    <td className="py-1.5 pr-3 font-mono text-[#e6edf3]">{c.entry_tag}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{c.n_trades}</td>
                    <td className={`py-1.5 pr-3 text-right font-mono ${c.total_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                      {c.total_pnl >= 0 ? "+" : ""}{c.total_pnl.toFixed(0)}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{c.pnl_share_pct.toFixed(1)}%</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{(c.win_rate * 100).toFixed(0)}%</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{c.tested ? c.p_value.toFixed(3) : "—"}</td>
                    <td className="py-1.5 text-center">
                      {!c.tested ? <span className="text-[#6e7681]">样本少</span>
                        : c.is_significant_5pct ? <span className="text-[#3fb950]">✓</span>
                        : <span className="text-[#6e7681]">✗</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-[#6e7681] mt-2">
            分解各开仓规则的盈亏占比与独立显著性，定位真正在赚钱的规则；「样本少」表示该组笔数不足未做检验。
          </p>
        </div>
      )}
    </div>
  )
}

// 在直方图桶中心里找与观测均值最接近的 x，用于对齐 ReferenceLine
function nearestBin(result: SignificanceResult): number {
  if (result.null_hist.length === 0) return result.observed_marker
  let best = result.null_hist[0].center
  let bestDist = Math.abs(best - result.observed_marker)
  for (const b of result.null_hist) {
    const d = Math.abs(b.center - result.observed_marker)
    if (d < bestDist) { bestDist = d; best = b.center }
  }
  return best
}

// ── 共享小组件 ───────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  backgroundColor: "#161b22",
  border: "1px solid #21262d",
  borderRadius: "6px",
  fontSize: "11px",
  color: "#e6edf3",
} as const

function LoadingCard({ text }: { text: string }) {
  return (
    <div className="card flex items-center justify-center h-48">
      <div className="text-center">
        <Spinner size="lg" className="mx-auto mb-3" />
        <p className="text-[#8b949e] text-sm">{text}</p>
      </div>
    </div>
  )
}

function ErrorBox({ message }: { message: string }) {
  return (
    <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
      {message}
    </p>
  )
}

interface SummaryStatProps {
  label: string
  value: string
  tone?: "good" | "bad" | "neutral"
}

function SummaryStat({ label, value, tone = "neutral" }: SummaryStatProps) {
  const toneColor = tone === "good" ? "text-[#3fb950]" : tone === "bad" ? "text-[#f85149]" : "text-[#e6edf3]"
  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2.5">
      <div className="text-[10px] text-[#6e7681] mb-1">{label}</div>
      <div className={`font-mono font-bold text-lg ${toneColor}`}>{value}</div>
    </div>
  )
}
