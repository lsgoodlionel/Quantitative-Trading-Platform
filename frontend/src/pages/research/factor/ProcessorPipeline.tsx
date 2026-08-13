import { useMemo, useState } from "react"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import type { FactorInfo } from "@/hooks/useFactorAnalysis"
import {
  useProcessorMeta, useProcessorPreview,
  type ProcessorMeta, type ProcessorConfig, type FactorStats, type PanelCell,
} from "@/hooks/useFactorProcessors"
import type { Market, Frequency } from "@/types"
import { UniverseHeader, parseUniverse, fmt, FORMULA_SENTINEL } from "./universeConfig"

// 契约 §6 默认防泄漏预设
const DEFAULT_INFER: ProcessorConfig[] = [{ name: "CSRankNorm", params: {} }]
const DEFAULT_LEARN: ProcessorConfig[] = [
  { name: "RobustZScoreNorm", params: { clip_outlier: true } },
  { name: "DropnaLabel", params: {} },
  { name: "Fillna", params: { fill_value: 0 } },
]

const STAT_ROWS: { key: keyof FactorStats; label: string }[] = [
  { key: "count", label: "有效数" },
  { key: "mean", label: "均值" },
  { key: "std", label: "标准差" },
  { key: "min", label: "最小值" },
  { key: "p25", label: "P25" },
  { key: "median", label: "中位数" },
  { key: "p75", label: "P75" },
  { key: "max", label: "最大值" },
  { key: "nan_rate", label: "缺失率" },
]

interface ProcessorPipelineProps {
  factorList: FactorInfo[]
  formulaTokens: string[]
}

export function ProcessorPipeline({ factorList, formulaTokens }: ProcessorPipelineProps) {
  const { data: meta = [] } = useProcessorMeta()
  const { mutate, isPending, data: result, error } = useProcessorPreview()
  const { toast } = useToast()

  const [universe, setUniverse] = useState("AAPL, MSFT, GOOGL, AMZN, NVDA")
  const [market, setMarket] = useState<Market>("US")
  const [freq, setFreq] = useState<Frequency>("1d")
  const [baseFactor, setBaseFactor] = useState("momentum_20")
  const [fitEnd, setFitEnd] = useState("2024-06-30")
  const [forwardPeriod, setForwardPeriod] = useState(10)
  const [infer, setInfer] = useState<ProcessorConfig[]>(DEFAULT_INFER)
  const [learn, setLearn] = useState<ProcessorConfig[]>(DEFAULT_LEARN)

  const metaByName = useMemo(() => {
    const m: Record<string, ProcessorMeta> = {}
    for (const p of meta) m[p.name] = p
    return m
  }, [meta])

  const inferOptions = meta.filter((p) => p.kind === "infer")
  const learnOptions = meta.filter((p) => p.kind === "learn")

  function addProcessor(list: "infer" | "learn", name: string) {
    const pm = metaByName[name]
    if (!pm) return
    const defaults: Record<string, unknown> = {}
    for (const p of pm.params) {
      if (p.default !== null && p.default !== undefined) defaults[p.name] = p.default
    }
    const cfg: ProcessorConfig = { name, params: defaults }
    if (list === "infer") setInfer((c) => [...c, cfg])
    else setLearn((c) => [...c, cfg])
  }

  function removeProcessor(list: "infer" | "learn", idx: number) {
    if (list === "infer") setInfer((c) => c.filter((_, i) => i !== idx))
    else setLearn((c) => c.filter((_, i) => i !== idx))
  }

  function updateParam(list: "infer" | "learn", idx: number, key: string, value: unknown) {
    const setter = list === "infer" ? setInfer : setLearn
    setter((c) => c.map((cfg, i) => (i === idx ? { ...cfg, params: { ...cfg.params, [key]: value } } : cfg)))
  }

  function handleRun() {
    const symbols = parseUniverse(universe)
    if (symbols.length < 2) { toast("至少输入 2 只标的", "warning"); return }
    if (!fitEnd) { toast("请设置训练截止日 fit_end", "warning"); return }
    if (baseFactor === FORMULA_SENTINEL && formulaTokens.length === 0) {
      toast("请先在「公式因子」页签构建公式", "warning"); return
    }
    mutate({
      symbols, market, frequency: freq, fit_end: fitEnd,
      base_factor: baseFactor,
      tokens: baseFactor === FORMULA_SENTINEL ? formulaTokens : undefined,
      infer_processors: infer, learn_processors: learn,
      forward_period: forwardPeriod,
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">训练截止 fit_end</label>
              <input type="date" className="input w-full text-xs"
                value={fitEnd} onChange={(e) => setFitEnd(e.target.value)} />
            </div>
            <div>
              <label className="label block mb-1">前瞻期</label>
              <input type="number" min={1} max={60} className="input w-full text-xs"
                value={forwardPeriod} onChange={(e) => setForwardPeriod(Number(e.target.value))} />
            </div>
          </div>
          <p className="text-[10px] text-[#6e7681] leading-snug">
            fit_end 之前为训练窗，learn 处理器仅在此拟合尺度参数，避免前视泄漏。
          </p>
        </div>

        <ProcessorList
          title="Infer 处理器（截面·无泄漏）" accent="#3fb950"
          list="infer" configs={infer} metaByName={metaByName} options={inferOptions}
          onAdd={addProcessor} onRemove={removeProcessor} onParam={updateParam}
        />
        <ProcessorList
          title="Learn 处理器（训练窗拟合）" accent="#e3b341"
          list="learn" configs={learn} metaByName={metaByName} options={learnOptions}
          onAdd={addProcessor} onRemove={removeProcessor} onParam={updateParam}
        />

        <button className="btn btn-primary w-full" onClick={handleRun} disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "运行处理流水线"}
        </button>
        {error && <p className="text-xs text-[#f85149] leading-snug">{error.message}</p>}
      </div>

      {/* ── 结果 ── */}
      <div className="xl:col-span-3 space-y-6">
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-64 text-[#6e7681] text-sm">
            配置流水线后点击「运行处理流水线」查看前后分布对比
          </div>
        )}
        {isPending && <div className="card flex items-center justify-center h-64"><Spinner /></div>}
        {result && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatChip label="输入行数" value={result.n_rows_in.toLocaleString()} />
              <StatChip label="输出行数" value={result.n_rows_out.toLocaleString()} />
              <StatChip label="丢弃行数" value={result.dropped_rows.toLocaleString()} accent="#f85149" />
              <StatChip label="标的数" value={String(result.symbols.length)} accent="#58a6ff" />
            </div>

            {result.fitted_learn.length > 0 && (
              <div className="card">
                <p className="text-xs text-[#8b949e] mb-2">已在训练窗 [{"→"} {result.fit_end}] 拟合的 learn 处理器</p>
                <div className="flex flex-wrap gap-1.5">
                  {result.fitted_learn.map((n) => (
                    <span key={n} className="badge text-[#e3b341] border-[#e3b341]/30 font-mono">{n}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">分布统计对比</h3>
                <StatsCompareTable raw={result.raw_stats} processed={result.processed_stats} />
              </div>
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">因子值直方图（尾部样本）</h3>
                <HistogramCompare before={result.sample_before} after={result.sample_after} />
                <p className="text-[10px] text-[#6e7681] mt-2">
                  处理后应更接近标准化分布（均值≈0、单位尺度、极端值被裁剪）。
                </p>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── 子组件 ─────────────────────────────────────────────────────────

interface ProcessorListProps {
  title: string
  accent: string
  list: "infer" | "learn"
  configs: ProcessorConfig[]
  metaByName: Record<string, ProcessorMeta>
  options: ProcessorMeta[]
  onAdd: (list: "infer" | "learn", name: string) => void
  onRemove: (list: "infer" | "learn", idx: number) => void
  onParam: (list: "infer" | "learn", idx: number, key: string, value: unknown) => void
}

function ProcessorList({ title, accent, list, configs, metaByName, options, onAdd, onRemove, onParam }: ProcessorListProps) {
  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold" style={{ color: accent }}>{title}</h3>
        <select
          className="input text-[11px] py-1"
          value=""
          onChange={(e) => { if (e.target.value) onAdd(list, e.target.value) }}
        >
          <option value="">+ 添加</option>
          {options.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
        </select>
      </div>
      {configs.length === 0 && <p className="text-[10px] text-[#6e7681]">（空）按执行顺序应用</p>}
      {configs.map((cfg, idx) => {
        const pm = metaByName[cfg.name]
        return (
          <div key={idx} className="rounded-lg border border-[#30363d] bg-[#0d1117] p-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-[#e6edf3]">
                <span className="text-[9px] text-[#6e7681] mr-1">{idx + 1}</span>{cfg.name}
              </span>
              <button onClick={() => onRemove(list, idx)}
                className="text-[10px] text-[#f85149] hover:underline">移除</button>
            </div>
            {pm && pm.params.length > 0 && (
              <div className="space-y-1.5">
                {pm.params.filter((p) => p.type !== "list[str]").map((p) => (
                  <ParamInput key={p.name} param={p} value={cfg.params[p.name]}
                    onChange={(v) => onParam(list, idx, p.name, v)} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ParamInput({ param, value, onChange }: {
  param: ProcessorMeta["params"][number]
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (param.type === "bool") {
    return (
      <label className="flex items-center justify-between text-[11px] text-[#8b949e]" title={param.description}>
        {param.name}
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
      </label>
    )
  }
  if (param.type === "str") {
    return (
      <label className="flex items-center justify-between gap-2 text-[11px] text-[#8b949e]" title={param.description}>
        {param.name}
        <input className="input text-[11px] py-0.5 w-28" value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)} />
      </label>
    )
  }
  // int / float
  return (
    <label className="flex items-center justify-between gap-2 text-[11px] text-[#8b949e]" title={param.description}>
      {param.name}
      <input type="number" step={param.type === "int" ? 1 : "any"}
        className="input text-[11px] py-0.5 w-24"
        value={value === undefined || value === null ? "" : Number(value)}
        onChange={(e) => onChange(param.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value))} />
    </label>
  )
}

function StatChip({ label, value, accent = "#e6edf3" }: { label: string; value: string; accent?: string }) {
  return (
    <div className="card py-3">
      <p className="text-[10px] text-[#6e7681]">{label}</p>
      <p className="text-lg font-mono font-semibold" style={{ color: accent }}>{value}</p>
    </div>
  )
}

function StatsCompareTable({ raw, processed }: { raw: FactorStats; processed: FactorStats }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
            <th className="text-left py-2 pr-4">指标</th>
            <th className="text-right py-2 pr-4">处理前</th>
            <th className="text-right py-2">处理后</th>
          </tr>
        </thead>
        <tbody>
          {STAT_ROWS.map(({ key, label }) => {
            const isRate = key === "nan_rate"
            const rv = raw[key], pv = processed[key]
            const disp = (v: number) => (isRate ? `${(v * 100).toFixed(1)}%` : key === "count" ? String(v) : fmt(v))
            return (
              <tr key={key} className="border-b border-[#21262d]/40 last:border-0">
                <td className="py-1.5 pr-4 text-[#8b949e]">{label}</td>
                <td className="py-1.5 pr-4 text-right font-mono text-[#8b949e]">{disp(rv)}</td>
                <td className="py-1.5 text-right font-mono text-[#e6edf3]">{disp(pv)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function HistogramCompare({ before, after }: { before: PanelCell[]; after: PanelCell[] }) {
  const data = useMemo(() => buildHistogram(before, after), [before, after])
  if (data.length === 0) return <p className="text-xs text-[#6e7681] py-8 text-center">无样本数据</p>
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
        <XAxis dataKey="bin" tick={{ fill: "#8b949e", fontSize: 9 }} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={36} />
        <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
          itemStyle={{ color: "#e6edf3" }} labelStyle={{ color: "#8b949e" }} />
        <Legend wrapperStyle={{ fontSize: 10 }} />
        <Bar dataKey="处理前" fill="#6e7681" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="处理后" fill="#58a6ff" radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function buildHistogram(before: PanelCell[], after: PanelCell[], bins = 16) {
  const bv = before.map((c) => c.value).filter((v): v is number => v != null && !isNaN(v))
  const av = after.map((c) => c.value).filter((v): v is number => v != null && !isNaN(v))
  const all = [...bv, ...av]
  if (all.length === 0) return []
  const lo = Math.min(...all), hi = Math.max(...all)
  const span = hi - lo || 1
  const width = span / bins
  const rows = Array.from({ length: bins }, (_, i) => ({
    bin: (lo + width * (i + 0.5)).toFixed(1),
    处理前: 0,
    处理后: 0,
  }))
  const idxOf = (v: number) => Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / width)))
  for (const v of bv) rows[idxOf(v)].处理前 += 1
  for (const v of av) rows[idxOf(v)].处理后 += 1
  return rows
}
