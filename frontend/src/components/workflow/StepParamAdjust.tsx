// ── 策略参数调整组件 ──────────────────────────────────────────────
// 回测不合格后点击「调整参数」时渲染。
// 显示本轮回测诊断、每个参数的滑块，
// 点击「重跑回测」后用新参数重新触发 handleStrategyConfirm。

import { useState } from "react"
import type { StrategyOption, BacktestVerdict, WorkflowData } from "./workflowTypes"
import { STRATEGY_PARAMS } from "@/data/strategyDefs"

interface Props {
  strategy: StrategyOption
  backtestResult: WorkflowData["backtestResult"]
  verdict: BacktestVerdict | null
  onRetry: (updated: StrategyOption) => void
  onChangeStrategy: () => void
}

export function StepParamAdjust({ strategy, backtestResult, verdict, onRetry, onChangeStrategy }: Props) {
  const paramDefs = STRATEGY_PARAMS[strategy.id] ?? []

  // Initialize slider values from the strategy's current params
  const [params, setParams] = useState<Record<string, number>>(() => {
    const init: Record<string, number> = {}
    for (const def of paramDefs) {
      const v = strategy.params[def.key]
      init[def.key] = typeof v === "number" ? v : def.default
    }
    return init
  })

  const setParam = (key: string, val: number) =>
    setParams(prev => ({ ...prev, [key]: val }))

  // Derive actionable hints from backtest metrics
  const insights: string[] = []
  if (backtestResult) {
    const m = backtestResult.metrics
    const dd = Math.abs(m.max_drawdown_pct)
    if (dd >= 25)            insights.push(`回撤 ${dd.toFixed(1)}% 偏高 — 建议加大周期参数，减少频繁入场`)
    if (m.sharpe_ratio < 0.5) insights.push(`Sharpe ${m.sharpe_ratio.toFixed(2)} 偏低 — 可尝试收紧入场条件`)
    if (m.win_rate_pct < 45)  insights.push(`胜率 ${m.win_rate_pct.toFixed(1)}% 偏低 — 考虑放宽超卖/超买阈值`)
    if (m.profit_factor < 1.0)insights.push(`盈亏比 ${m.profit_factor.toFixed(2)} < 1 — 建议调整止盈止损比例`)
  }

  const handleRetry = () => {
    onRetry({ ...strategy, params: { ...strategy.params, ...params } })
  }

  // Strategy has no numeric params (e.g. pairs_trading without numeric defs)
  if (paramDefs.length === 0) {
    return (
      <div className="space-y-3">
        <p className="text-xs text-[#8b949e]">「{strategy.name}」暂不支持参数手动调整。</p>
        <button
          onClick={onChangeStrategy}
          className="w-full py-2.5 rounded-lg border border-[#58a6ff]/40 text-[#58a6ff] text-xs
                     hover:bg-[#1f3d5e]/40 transition-colors"
        >
          ← 重新选择策略
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <p className="text-xs font-semibold text-[#e6edf3]">调整「{strategy.name}」参数</p>
        <p className="text-[10px] text-[#8b949e] mt-0.5">
          修改下方参数后点击「重跑回测」，系统将重新运行 2 年历史验证。
        </p>
      </div>

      {/* Diagnostics from previous run */}
      {verdict && verdict !== "pass" && insights.length > 0 && (
        <div className="bg-[#2a1f00] border border-[#e3b341]/30 rounded-lg p-3 space-y-1.5">
          <p className="text-[10px] font-medium text-[#e3b341]">⚠ 上次回测诊断</p>
          {insights.map((s, i) => (
            <p key={i} className="text-[10px] text-[#c9a227] leading-relaxed">▸ {s}</p>
          ))}
        </div>
      )}

      {/* Param sliders */}
      <div className="space-y-3">
        {paramDefs.map(def => {
          const val = params[def.key] ?? def.default
          const displayVal =
            def.type === "float"
              ? val.toFixed(def.step < 0.01 ? 3 : def.step < 0.1 ? 2 : 1)
              : String(val)

          return (
            <div key={def.key} className="bg-[#0d1117] rounded-lg p-3">
              <div className="flex justify-between items-center mb-2">
                <span className="text-xs text-[#e6edf3]">{def.label}</span>
                <span className="font-mono text-sm font-bold text-[#58a6ff]">{displayVal}</span>
              </div>

              <input
                type="range"
                min={def.min}
                max={def.max}
                step={def.step}
                value={val}
                onChange={e => setParam(def.key, Number(e.target.value))}
                className="w-full h-1.5 rounded cursor-pointer accent-[#58a6ff]"
                style={{ background: `linear-gradient(to right, #58a6ff ${((val - def.min) / (def.max - def.min)) * 100}%, #30363d 0%)` }}
              />

              <div className="flex justify-between text-[9px] text-[#6e7681] mt-1.5">
                <span>{def.min}</span>
                <span className="text-center max-w-[60%] leading-tight">{def.hint}</span>
                <span>{def.max}</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* Actions */}
      <div className="flex gap-2 pt-1">
        <button
          onClick={onChangeStrategy}
          className="flex-1 py-2.5 rounded-lg border border-[#30363d] text-[#8b949e] text-xs
                     hover:bg-[#21262d] transition-colors"
        >
          ← 换策略
        </button>
        <button
          onClick={handleRetry}
          className="flex-[2] py-2.5 rounded-lg bg-[#1f6feb] text-white text-xs font-medium
                     hover:bg-[#388bfd] transition-colors"
        >
          重跑回测 →
        </button>
      </div>
    </div>
  )
}
