import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import type { Frequency, Market } from "@/types"
import { FREQ_LABELS, MARKET_CFGS, yearsAgo } from "./config"
import { useSharedConfig } from "./SharedConfig"

const DATE_PRESETS = [
  { label: "1年", years: 1 },
  { label: "2年", years: 2 },
  { label: "3年", years: 3 },
  { label: "5年", years: 5 },
]

interface SharedConfigHeaderProps {
  onRunBacktest: () => void
  onRunFullValidation: () => void
  isBacktestPending: boolean
  isValidationPending: boolean
}

// ── 共享配置头（V3 · H2）───────────────────────────────────────
// 一处填写、全 Tab 生效；右侧是「运行回测」与「完整验证」两个主动作。
export function SharedConfigHeader({
  onRunBacktest,
  onRunFullValidation,
  isBacktestPending,
  isValidationPending,
}: SharedConfigHeaderProps) {
  const { config, update, strategies } = useSharedConfig()
  const marketCfg = MARKET_CFGS.find((c) => c.value === config.market) ?? MARKET_CFGS[0]
  const busy = isBacktestPending || isValidationPending

  function handleMarketChange(value: string) {
    const cfg = MARKET_CFGS.find((c) => c.value === value) ?? MARKET_CFGS[0]
    update("market", value as Market)
    update("frequency", cfg.defaultFreq)
  }

  return (
    <section aria-label="共享回测配置" className="card mb-5">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">共享配置</h2>
          <p className="text-[11px] text-[#6e7681] mt-0.5">
            以下配置对所有 Tab 生效，改一处即可，无需在每个表单重填。
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <button type="button" className="btn btn-ghost text-xs whitespace-nowrap"
            disabled={busy} onClick={onRunBacktest}>
            {isBacktestPending ? <Spinner size="sm" /> : "▶ 运行回测"}
          </button>
          <button type="button" className="btn btn-primary text-xs whitespace-nowrap"
            disabled={busy} onClick={onRunFullValidation}>
            {isValidationPending ? <Spinner size="sm" /> : "⚡ 完整验证"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <div className="col-span-2 xl:col-span-2">
          <label className="label" htmlFor="shared-strategy">策略</label>
          <select id="shared-strategy" className="select w-full mt-1" value={config.strategy_name}
            onChange={(e) => update("strategy_name", e.target.value)}>
            {strategies.map((s) => (
              <option key={s.name} value={s.name}>{s.description || s.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="shared-market">市场</label>
          <select id="shared-market" className="select w-full mt-1" value={config.market}
            onChange={(e) => handleMarketChange(e.target.value)}>
            {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="shared-symbol">标的</label>
          <input id="shared-symbol" className="input w-full mt-1 font-mono uppercase" value={config.symbol}
            onChange={(e) => update("symbol", e.target.value.toUpperCase())} />
        </div>

        <div>
          <label className="label" htmlFor="shared-frequency">K线周期</label>
          <select id="shared-frequency" className="select w-full mt-1" value={config.frequency}
            onChange={(e) => update("frequency", e.target.value as Frequency)}>
            {marketCfg.allowedFreqs.map((f) => <option key={f} value={f}>{FREQ_LABELS[f]}</option>)}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="shared-cash">初始资金</label>
          <input id="shared-cash" className="input w-full mt-1 font-mono" type="number"
            min={1000} step={10000} value={config.initial_cash}
            onChange={(e) => update("initial_cash", Number(e.target.value))} />
        </div>

        <div className="col-span-2 xl:col-span-3">
          <label className="label" htmlFor="shared-start">日期范围</label>
          <div className="flex gap-2 mt-1">
            <input id="shared-start" className="input flex-1" type="date" value={config.start_date}
              onChange={(e) => update("start_date", e.target.value)} />
            <input aria-label="结束日期" className="input flex-1" type="date" value={config.end_date}
              onChange={(e) => update("end_date", e.target.value)} />
          </div>
        </div>

        <div className="col-span-2 xl:col-span-3">
          <label className="label">快捷区间</label>
          <div className="flex gap-1 mt-1">
            {DATE_PRESETS.map(({ label, years }) => (
              <button key={label} type="button" className="btn btn-ghost text-xs px-2 py-1"
                onClick={() => update("start_date", yearsAgo(years))}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-3">
        <label className="label" htmlFor="shared-params">
          策略参数 <span className="text-[10px] text-[#6e7681]">JSON，留空则用策略默认值</span>
        </label>
        <ParamsInput />
      </div>
    </section>
  )
}

// 参数输入独立成组件：JSON 解析失败时就地提示，不 alert、不静默吞掉
function ParamsInput() {
  const { config, update } = useSharedConfig()
  const [isInvalid, setIsInvalid] = useState(false)

  function handleChange(value: string) {
    try {
      update("params", JSON.parse(value || "{}") as Record<string, unknown>)
      setIsInvalid(false)
    } catch {
      setIsInvalid(true)
    }
  }

  return (
    <>
      <input id="shared-params" className="input w-full mt-1 font-mono text-xs"
        defaultValue={JSON.stringify(config.params ?? {})}
        onBlur={(e) => handleChange(e.target.value)} />
      {isInvalid && (
        <p className="text-[#f85149] text-[11px] mt-1">参数 JSON 格式错误，已保留上一次的值。</p>
      )}
    </>
  )
}
