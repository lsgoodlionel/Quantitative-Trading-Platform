// 启动 / 调整重跑表单：选策略、标的、周期与结构化策略参数，提交后跑模拟盘。
import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useStartStrategy } from "@/hooks/useLiveStrategy"
import type { Market, Frequency } from "@/types"
import { FREQS, MARKETS, STRATEGY_LABELS } from "./shared"
import { DEFAULT_PARAMS, STRATEGY_PARAM_DEFS, type ParamDef } from "./strategyParams"

// ── 参数字段编辑器 ─────────────────────────────────────────────
function ParamField({ def: d, value, onChange }: { def: ParamDef; value: number | string; onChange: (v: number | string) => void }) {
  if (d.type === "select") {
    return (
      <div>
        <label className="block text-[10px] text-[#6e7681] mb-1">{d.label}</label>
        <select className="select w-full text-xs" value={String(value)}
          onChange={(e) => onChange(e.target.value)}>
          {d.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
    )
  }
  return (
    <div>
      <label className="block text-[10px] text-[#6e7681] mb-1">
        {d.label}
        {d.hint && <span className="ml-1 text-[#6e7681]/70">· {d.hint}</span>}
      </label>
      <div className="flex items-center gap-2">
        <input type="number" className="input w-full text-xs font-mono"
          value={Number(value)}
          min={d.min} max={d.max} step={d.step ?? (d.type === "float" ? 0.1 : 1)}
          onChange={(e) => onChange(d.type === "float" ? parseFloat(e.target.value) : parseInt(e.target.value))}
        />
        {d.min !== undefined && d.max !== undefined && (
          <span className="text-[9px] text-[#6e7681] shrink-0 whitespace-nowrap">
            {d.min}–{d.max}
          </span>
        )}
      </div>
    </div>
  )
}

// ── 启动 / 调整表单 ───────────────────────────────────────────
interface LaunchFormProps {
  strategies: { name: string; description: string }[]
  onClose: () => void
  /** 调整重跑时的初始值 */
  initialValues?: {
    strategy_name?: string
    symbol?: string
    market?: Market
    frequency?: Frequency
    params?: Record<string, unknown>
    sim_days?: number
  }
}

const SIM_DAY_OPTIONS = [
  { value: 30,  label: "30 天" },
  { value: 60,  label: "60 天" },
  { value: 90,  label: "90 天" },
  { value: 120, label: "120 天" },
  { value: 180, label: "180 天" },
  { value: 365, label: "1 年" },
]

export function LaunchForm({ strategies, onClose, initialValues }: LaunchFormProps) {
  const { toast } = useToast()
  const { mutate: startStrategy, isPending } = useStartStrategy()

  const firstStrategy = strategies[0]?.name ?? "double_ma"
  const initStrat = initialValues?.strategy_name ?? firstStrategy

  const [stratName, setStratName]   = useState(initStrat)
  const [symbol, setSymbol]         = useState(initialValues?.symbol ?? "AAPL")
  const [market, setMarket]         = useState<Market>(initialValues?.market ?? "US")
  const [frequency, setFrequency]   = useState<Frequency>(initialValues?.frequency ?? "1d")
  const [simDays, setSimDays]       = useState(initialValues?.sim_days ?? 60)
  const isRerun = !!initialValues?.strategy_name

  // 参数状态：从 initialValues 或策略默认值初始化
  const [params, setParams] = useState<Record<string, number | string>>(() => {
    const defs = DEFAULT_PARAMS[initStrat] ?? {}
    const init = initialValues?.params ?? {}
    return { ...defs, ...init } as Record<string, number | string>
  })

  // 切换策略时重置参数（保留标的/市场/频率）
  function handleStrategyChange(name: string) {
    setStratName(name)
    setParams(DEFAULT_PARAMS[name] ?? {})
  }

  function updateParam(key: string, val: number | string) {
    setParams((prev) => ({ ...prev, [key]: val }))
  }

  const paramDefs = STRATEGY_PARAM_DEFS[stratName] ?? []
  const warmupDays = Math.max(simDays + 60, 120)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!symbol.trim()) { toast("请填写标的代码", "warning"); return }
    startStrategy(
      {
        strategy_name: stratName,
        symbol: symbol.trim().toUpperCase(),
        market,
        frequency,
        params,
        warmup_days: warmupDays,
        sim_days: simDays,
      },
      {
        onSuccess: (inst) => {
          toast(`模拟盘已启动：${inst.instance_id.slice(0, 24)}…`, "success")
          onClose()
        },
        onError: (e) => toast(e.message, "error"),
      }
    )
  }

  return (
    <form onSubmit={handleSubmit}
      className={`rounded-xl border p-4 space-y-4 ${isRerun ? "bg-[#161b22] border-[#e3b341]/25" : "bg-[#161b22] border-[#58a6ff]/20"}`}>
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            {isRerun ? "⚙️ 调整参数 · 重新模拟" : "▶ 启动模拟盘"}
          </h3>
          {isRerun && (
            <p className="text-[10px] text-[#e3b341] mt-0.5">
              已预填当前实例参数，修改后启动新模拟
            </p>
          )}
        </div>
        <button type="button" onClick={onClose} className="text-[#6e7681] hover:text-[#e6edf3] text-xl leading-none">×</button>
      </div>

      {/* 策略选择 */}
      <div>
        <label className="block text-[10px] text-[#6e7681] mb-1.5">策略</label>
        <select className="select w-full" value={stratName}
          onChange={(e) => handleStrategyChange(e.target.value)}>
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>{STRATEGY_LABELS[s.name] ?? s.name}</option>
          ))}
        </select>
      </div>

      {/* 标的 + 市场 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">标的代码</label>
          <input className="input w-full font-mono text-sm uppercase" value={symbol}
            onChange={(e) => setSymbol(e.target.value)} placeholder="AAPL / 0700.HK / 600519" />
        </div>
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">市场</label>
          <div className="flex gap-1">
            {MARKETS.map((m) => (
              <button key={m.value} type="button" onClick={() => setMarket(m.value)}
                className={`flex-1 py-1.5 rounded text-xs border transition-colors ${
                  market === m.value
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                    : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>{m.value}</button>
            ))}
          </div>
        </div>
      </div>

      {/* 周期 + 模拟天数 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">K 线周期</label>
          <select className="select w-full" value={frequency}
            onChange={(e) => setFrequency(e.target.value as Frequency)}>
            {FREQS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">
            模拟天数
            <span className="ml-1 text-[#58a6ff]">{simDays} 天</span>
          </label>
          <select className="select w-full" value={simDays}
            onChange={(e) => setSimDays(parseInt(e.target.value))}>
            {SIM_DAY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 策略参数（结构化字段） */}
      {paramDefs.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-[10px] text-[#6e7681]">策略参数</label>
            <button type="button" className="text-[9px] text-[#58a6ff] hover:underline"
              onClick={() => setParams(DEFAULT_PARAMS[stratName] ?? {})}>
              重置默认值
            </button>
          </div>
          <div className={`grid gap-3 ${paramDefs.length <= 2 ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-3"}`}>
            {paramDefs.map((d) => (
              <ParamField key={d.key} def={d}
                value={params[d.key] ?? d.default}
                onChange={(v) => updateParam(d.key, v)} />
            ))}
          </div>
        </div>
      )}

      {/* 提示 */}
      <div className="bg-[#0d1117] rounded-lg p-3 text-[10px] text-[#6e7681] flex items-start gap-2">
        <span>💡</span>
        <span>
          将在最近 <strong className="text-[#e6edf3]">{simDays} 天</strong> 历史数据上运行模拟
          （前 {warmupDays - simDays} 天用于指标预热）。
          {isRerun ? " 新模拟将作为独立实例保存，可与旧结果对比。" : " 几秒内可看到净值曲线和交易记录。"}
        </span>
      </div>

      {/* 操作按钮 */}
      <div className="flex gap-2">
        <button type="button" onClick={onClose}
          className="flex-1 btn border border-[#30363d] text-[#8b949e] text-sm">
          取消
        </button>
        <button type="submit" disabled={isPending}
          className={`flex-1 btn text-sm ${isRerun ? "bg-[#e3b341]/15 border border-[#e3b341]/40 text-[#e3b341] hover:bg-[#e3b341]/20" : "btn-primary"}`}>
          {isPending
            ? <Spinner size="sm" className="mx-auto" />
            : isRerun ? "⚙️ 重新模拟" : "▶ 启动模拟"}
        </button>
      </div>
    </form>
  )
}
