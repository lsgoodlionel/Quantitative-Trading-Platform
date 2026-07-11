import { Spinner } from "@/components/ui/Spinner"
import type { BacktestRequest, Market, Frequency } from "@/types"
import { MARKET_CFGS, FREQ_LABELS, yearsAgo } from "./config"

// ── 回测配置面板 ──────────────────────────────────────────────
interface ConfigPanelProps {
  form: BacktestRequest
  strategies: { name: string; description: string }[]
  isLoading: boolean
  error: Error | null
  onChange: (key: keyof BacktestRequest, val: BacktestRequest[keyof BacktestRequest]) => void
  onSubmit: (e: React.FormEvent) => void
  submitLabel?: string
}

export function ConfigPanel({ form, strategies, isLoading, error, onChange, onSubmit, submitLabel = "▶ 开始回测" }: ConfigPanelProps) {
  const marketCfg = MARKET_CFGS.find((c) => c.value === form.market) ?? MARKET_CFGS[0]

  function handleMarketChange(m: string) {
    const cfg = MARKET_CFGS.find((c) => c.value === m) ?? MARKET_CFGS[0]
    onChange("market", m as Market)
    onChange("frequency", cfg.defaultFreq)
  }

  return (
    <form onSubmit={onSubmit} className="card space-y-4">
      <h2 className="text-sm font-semibold text-[#e6edf3]">策略配置</h2>

      {/* 策略选择 */}
      <div>
        <label className="label">策略</label>
        <select className="select w-full mt-1" value={form.strategy_name}
          onChange={(e) => onChange("strategy_name", e.target.value)}>
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>{s.description || s.name}</option>
          ))}
        </select>
      </div>

      {/* 市场 + 标的 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">市场</label>
          <select className="select w-full mt-1" value={form.market}
            onChange={(e) => handleMarketChange(e.target.value)}>
            {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <label className="label">
            标的
            <span className="text-[10px] text-[#6e7681] ml-1">
              {form.market === "A" ? "如 000001" : form.market === "HK" ? "如 00700" : "如 AAPL"}
            </span>
          </label>
          <input className="input w-full mt-1 font-mono uppercase"
            value={form.symbol}
            onChange={(e) => onChange("symbol", e.target.value.toUpperCase())}
          />
        </div>
      </div>

      {/* 周期 */}
      <div>
        <label className="label">K线周期</label>
        <select className="select w-full mt-1" value={form.frequency}
          onChange={(e) => onChange("frequency", e.target.value as Frequency)}>
          {marketCfg.allowedFreqs.map((f) => (
            <option key={f} value={f}>{FREQ_LABELS[f]}</option>
          ))}
        </select>
      </div>

      {/* 快捷日期 */}
      <div>
        <label className="label mb-1.5">日期范围</label>
        <div className="flex gap-1 mb-2">
          {[
            { label: "1年", fn: () => yearsAgo(1) },
            { label: "2年", fn: () => yearsAgo(2) },
            { label: "3年", fn: () => yearsAgo(3) },
            { label: "5年", fn: () => yearsAgo(5) },
          ].map(({ label, fn }) => (
            <button key={label} type="button"
              className="btn btn-ghost text-xs px-2 py-0.5"
              onClick={() => onChange("start_date", fn())}>
              {label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input className="input" type="date" value={form.start_date}
            onChange={(e) => onChange("start_date", e.target.value)} />
          <input className="input" type="date" value={form.end_date}
            onChange={(e) => onChange("end_date", e.target.value)} />
        </div>
      </div>

      {/* 初始资金 */}
      <div>
        <label className="label">初始资金</label>
        <input className="input w-full mt-1 font-mono" type="number"
          value={form.initial_cash}
          onChange={(e) => onChange("initial_cash", Number(e.target.value))}
          min={1000} step={10000}
        />
      </div>

      {error && (
        <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          {error.message}
        </p>
      )}

      <button type="submit" className="btn btn-primary w-full" disabled={isLoading}>
        {isLoading ? <Spinner size="sm" className="mx-auto" /> : submitLabel}
      </button>
    </form>
  )
}
