// ── 策略参数调整组件 ──────────────────────────────────────────────
// 回测不合格后点击「调整参数」时渲染。
// 显示本轮回测诊断 + 针对该策略的具体调参建议，
// 点击「重跑回测」后用新参数重新触发 handleStrategyConfirm。

import { useState } from "react"
import type { StrategyOption, BacktestVerdict, WorkflowData } from "./workflowTypes"
import { STRATEGY_PARAMS } from "@/data/strategyDefs"
import type { ParamDef } from "@/data/strategyDefs"

interface Props {
  strategy: StrategyOption
  backtestResult: WorkflowData["backtestResult"]
  verdict: BacktestVerdict | null
  onRetry: (updated: StrategyOption) => void
  onChangeStrategy: () => void
}

// ── 策略诊断类型 ─────────────────────────────────────────────────
interface DiagFlag {
  highDrawdown: boolean
  lowSharpe: boolean
  lowWinRate: boolean
  lowProfitFactor: boolean
}

// ── 具体调参建议生成（每策略定制） ──────────────────────────────────
interface TuneHint {
  paramKey: string
  label: string
  from: number
  to: number
  reason: string
}

function buildTuneHints(
  strategyId: string,
  currentParams: Record<string, number>,
  diag: DiagFlag,
  paramDefs: ParamDef[],
): TuneHint[] {
  const hints: TuneHint[] = []

  const get = (key: string, fallback: number) =>
    currentParams[key] ?? paramDefs.find(d => d.key === key)?.default ?? fallback

  const clamp = (v: number, def: ParamDef) =>
    Math.round(Math.min(def.max, Math.max(def.min, v)) / def.step) * def.step

  const suggest = (key: string, rawVal: number, reason: string) => {
    const def = paramDefs.find(d => d.key === key)
    if (!def) return
    const from = get(key, def.default)
    const to = parseFloat(clamp(rawVal, def).toFixed(def.step < 0.01 ? 3 : def.step < 0.1 ? 2 : 1))
    if (Math.abs(from - to) < def.step * 0.5) return   // 无变化则不推
    hints.push({ paramKey: key, label: def.label, from, to, reason })
  }

  switch (strategyId) {
    // ── 趋势跟踪类 ──────────────────────────────────────────────
    case "double_ma":
      if (diag.highDrawdown) suggest("slow_period", get("slow_period", 30) + 10, "加大慢线周期 → 趋势确认更稳健，减少频繁换手")
      if (diag.lowWinRate)   suggest("fast_period", get("fast_period", 10) + 5, "快线加长 → 减少假金叉，提升信号质量")
      if (diag.lowSharpe)    suggest("slow_period", get("slow_period", 30) + 15, "慢线继续加大 → 只跟随主趋势，降低波动")
      break

    case "triple_ma":
      if (diag.highDrawdown) suggest("slow_period", get("slow_period", 34) + 8, "慢线拉长 → 三线排列要求更严格")
      if (diag.lowWinRate)   suggest("mid_period", get("mid_period", 13) + 5, "中线加长 → 过滤短期震荡噪声")
      if (diag.lowSharpe)    suggest("fast_period", get("fast_period", 5) + 3, "快线加长 → 减少频繁换仓")
      break

    case "macd":
      if (diag.highDrawdown)     suggest("slow", get("slow", 26) + 6, "慢 EMA 加大 → MACD 柱震荡幅度降低")
      if (diag.lowWinRate)       suggest("signal", get("signal", 9) + 3, "信号线加大 → MACD 金叉更可靠，假信号减少")
      if (diag.lowProfitFactor)  suggest("fast", get("fast", 12) + 3, "快 EMA 加大 → 拉大快慢线差距，强化趋势")
      break

    case "supertrend":
      if (diag.highDrawdown)    suggest("multiplier", get("multiplier", 3.0) + 1.0, "ATR 倍数加大 → 通道更宽，抗震荡能力增强")
      if (diag.lowWinRate)      suggest("multiplier", get("multiplier", 3.0) + 0.5, "倍数适当加大 → 减少误反转信号")
      if (diag.lowSharpe)       suggest("period", get("period", 10) + 4, "ATR 周期加长 → 通道更平滑，方向翻转更可靠")
      break

    case "adx_trend":
      if (diag.highDrawdown)    suggest("adx_threshold", get("adx_threshold", 25) + 5, "提高 ADX 入场门槛 → 只在更强趋势中交易")
      if (diag.lowWinRate)      suggest("adx_threshold", get("adx_threshold", 25) + 8, "大幅提高 ADX 门槛 → 严格过滤震荡行情")
      if (diag.lowSharpe)       suggest("slow_period", get("slow_period", 30) + 10, "慢线加长 → 与 ADX 配合更稳健")
      break

    // ── 均值回归类 ──────────────────────────────────────────────
    case "bollinger":
      if (diag.highDrawdown)    suggest("std_dev", get("std_dev", 2.0) + 0.5, "通道加宽（2.5σ）→ 只在极端偏离时入场")
      if (diag.lowWinRate)      suggest("std_dev", get("std_dev", 2.0) + 0.5, "加大标准差倍数 → 等待更确定的回归点")
      if (diag.lowProfitFactor) suggest("period", get("period", 20) + 10, "均线周期加长 → 通道基准更稳定")
      break

    case "rsi_mean_reversion":
      if (diag.highDrawdown)    suggest("oversold",   get("oversold", 30) - 5, "超卖阈值降低 → 只在极端超卖时买入")
      if (diag.lowWinRate)      suggest("overbought", get("overbought", 70) + 5, "超买阈值提高 → 持仓更长等待更充分反弹")
      if (diag.lowProfitFactor) suggest("oversold",   get("oversold", 30) - 8, "极端压低超卖线 → 信号减少但质量大幅提升")
      break

    case "stochastic":
      if (diag.highDrawdown)    suggest("oversold", get("oversold", 20) - 5, "超卖区间降低（15）→ 只在更深超卖时入场")
      if (diag.lowWinRate) {
        suggest("k_period", get("k_period", 14) + 4, "K 线周期加长 → 平滑随机指标，减少噪声")
        suggest("d_period", get("d_period", 3) + 2,  "D 线平滑加强 → %K/%D 交叉信号更可靠")
      }
      if (diag.lowProfitFactor) suggest("overbought", get("overbought", 80) + 5, "超买阈值提高（85）→ 持仓等待更充分上涨")
      break

    case "vwap_reversion":
      if (diag.highDrawdown)    suggest("dev_threshold", get("dev_threshold", 0.02) + 0.01, "偏离阈值提高到 3% → 只在更大偏差时入场")
      if (diag.lowWinRate)      suggest("dev_threshold", get("dev_threshold", 0.02) + 0.015, "偏离阈值提高到 3.5% → 确保均值回归有足够空间")
      if (diag.lowSharpe)       suggest("period", get("period", 20) + 10, "VWAP 周期加长 → 基准价格更稳定")
      break

    // ── 突破类 ──────────────────────────────────────────────────
    case "donchian_breakout":
      if (diag.highDrawdown)    suggest("exit_period", get("exit_period", 10) - 3, "退出周期缩短 → 更快止损，控制单次亏损")
      if (diag.lowWinRate) {
        suggest("period", get("period", 20) + 10, "突破周期加长（30 日）→ 只追真正的长期新高")
      }
      if (diag.lowProfitFactor) suggest("period", get("period", 20) + 15, "突破周期拉到 35 日 → 大幅提升突破质量")
      break

    case "keltner_breakout":
      if (diag.highDrawdown)    suggest("multiplier", get("multiplier", 2.0) + 0.5, "通道倍数加大 → 突破门槛更高，假突破减少")
      if (diag.lowWinRate) {
        suggest("ema_period", get("ema_period", 20) + 5, "中轨 EMA 加长 → 通道基准更稳定")
        suggest("multiplier", get("multiplier", 2.0) + 0.5, "加宽通道门槛 → 滤除小幅波动")
      }
      if (diag.lowSharpe)       suggest("atr_period", get("atr_period", 10) + 4, "ATR 周期加长 → 波动率估计更平稳")
      break

    case "atr_breakout":
      if (diag.highDrawdown)    suggest("multiplier", get("multiplier", 0.5) + 0.3, "突破加成倍数增大 → 门槛更高，只追强突破")
      if (diag.lowWinRate) {
        suggest("channel_period", get("channel_period", 20) + 10, "区间参考周期加长 → 突破历史基准更具代表性")
        suggest("multiplier", get("multiplier", 0.5) + 0.4, "提高 ATR 加成倍数 → 要求更大的突破幅度")
      }
      if (diag.lowProfitFactor) suggest("atr_period", get("atr_period", 14) + 4, "ATR 周期加大 → 动态阈值更准确")
      break

    // ── 动量类 ──────────────────────────────────────────────────
    case "momentum":
      if (diag.highDrawdown)    suggest("threshold", get("threshold", 0.03) + 0.02, "动量门槛提高到 5% → 只追强动量")
      if (diag.lowWinRate)      suggest("lookback", get("lookback", 20) + 10, "回望周期加长 → 动量更稳定")
      if (diag.lowProfitFactor) suggest("threshold", get("threshold", 0.03) + 0.03, "门槛提到 6% → 严格过滤弱动量")
      break

    // ── 复合/高级类 ─────────────────────────────────────────────
    case "multi_factor":
      if (diag.highDrawdown)    suggest("threshold", get("threshold", 0.6) + 0.1, "综合得分门槛提高到 0.7 → 三因子同时达标才入场")
      if (diag.lowWinRate)      suggest("rsi_low", get("rsi_low", 40) - 5, "RSI 低位线降低 → 等待更深超卖才加权")
      if (diag.lowSharpe)       suggest("momentum_lookback", get("momentum_lookback", 20) + 10, "动量周期加长 → 动量因子更稳定")
      break

    case "grid_trading":
      if (diag.highDrawdown)    suggest("grid_range", get("grid_range", 0.10) - 0.03, "网格区间收窄 → 偏离不大时不触发边缘格")
      if (diag.lowProfitFactor) suggest("grid_count", get("grid_count", 10) - 3, "减少网格数量 → 单格利润空间更大")
      if (diag.lowSharpe)       suggest("qty_per_grid", get("qty_per_grid", 10) - 3, "每格交易量减少 → 降低过度持仓风险")
      break

    case "pairs_trading":
      if (diag.highDrawdown)    suggest("entry_z", get("entry_z", 2.0) + 0.5, "开仓 Z 分数提高到 2.5 → 只在价差极度偏离时建仓")
      if (diag.lowWinRate) {
        suggest("entry_z",  get("entry_z", 2.0) + 0.5, "提高开仓门槛 → 选择更显著的套利机会")
        suggest("lookback", get("lookback", 60) + 20,   "协整回望加长 → 价差统计基准更稳定")
      }
      if (diag.lowProfitFactor) suggest("exit_z", get("exit_z", 0.5) - 0.2, "平仓 Z 分数降低 → 价差稍微回归即锁利")
      break
  }

  return hints
}

// ── 主组件 ──────────────────────────────────────────────────────
export function StepParamAdjust({
  strategy,
  backtestResult,
  verdict,
  onRetry,
  onChangeStrategy,
}: Props) {
  const paramDefs = STRATEGY_PARAMS[strategy.id] ?? []

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

  // ── 诊断 ────────────────────────────────────────────────────
  const diag: DiagFlag = {
    highDrawdown: false, lowSharpe: false, lowWinRate: false, lowProfitFactor: false,
  }
  const generalInsights: Array<{ icon: string; color: string; text: string }> = []

  if (backtestResult) {
    const m = backtestResult.metrics
    const dd = Math.abs(m.max_drawdown_pct)
    if (dd >= 25) {
      diag.highDrawdown = true
      generalInsights.push({ icon: "📉", color: "#f85149", text: `最大回撤 ${dd.toFixed(1)}% 过高（≥25%）— 需要降低风险敞口` })
    }
    if (m.sharpe_ratio < 0.5) {
      diag.lowSharpe = true
      generalInsights.push({ icon: "⚡", color: "#e3b341", text: `Sharpe ${m.sharpe_ratio.toFixed(2)} 偏低（<0.5）— 风险调整后收益不足` })
    }
    if (m.win_rate_pct < 45) {
      diag.lowWinRate = true
      generalInsights.push({ icon: "🎯", color: "#e3b341", text: `胜率 ${m.win_rate_pct.toFixed(1)}% 偏低（<45%）— 信号质量需要提升` })
    }
    if (m.profit_factor < 1.0) {
      diag.lowProfitFactor = true
      generalInsights.push({ icon: "⚖️", color: "#f85149", text: `盈亏比 ${m.profit_factor.toFixed(2)} < 1 — 总亏损大于总盈利` })
    }
  }

  // ── 策略具体调参建议 ─────────────────────────────────────────
  const tuneHints = buildTuneHints(strategy.id, params, diag, paramDefs)

  const applyAllHints = () => {
    const updated = { ...params }
    for (const h of tuneHints) updated[h.paramKey] = h.to
    setParams(updated)
  }

  const handleRetry = () => {
    onRetry({ ...strategy, params: { ...strategy.params, ...params } })
  }

  // 无参数策略
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
          调整参数后点击「重跑回测」，系统将重新运行 2 年历史验证。
        </p>
      </div>

      {/* ── 诊断摘要 ── */}
      {verdict !== "pass" && generalInsights.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-3 space-y-1.5">
          <p className="text-[10px] font-semibold text-[#8b949e] mb-2">📊 回测诊断</p>
          {generalInsights.map((item, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="text-[11px] leading-none mt-0.5">{item.icon}</span>
              <p className="text-[10px] leading-relaxed" style={{ color: item.color }}>{item.text}</p>
            </div>
          ))}
        </div>
      )}

      {/* ── 具体调参建议卡片 ── */}
      {tuneHints.length > 0 && (
        <div className="bg-[#0e1f0e] border border-[#2ea043]/30 rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between mb-1">
            <p className="text-[10px] font-semibold text-[#3fb950]">💡 针对本策略的调参建议</p>
            <button
              onClick={applyAllHints}
              className="text-[9px] px-2 py-0.5 rounded bg-[#238636]/40 border border-[#2ea043]/40
                         text-[#3fb950] hover:bg-[#238636]/60 transition-colors"
            >
              一键应用建议
            </button>
          </div>
          {tuneHints.map((h, i) => (
            <div key={i} className="flex flex-col gap-0.5">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-[#e6edf3] font-medium">{h.label}</span>
                <span className="text-[10px] text-[#6e7681] font-mono">{h.from}</span>
                <span className="text-[10px] text-[#8b949e]">→</span>
                <span className="text-[10px] text-[#58a6ff] font-mono font-bold">{h.to}</span>
              </div>
              <p className="text-[9px] text-[#6e7681] pl-0.5 leading-relaxed">▸ {h.reason}</p>
            </div>
          ))}
        </div>
      )}

      {/* ── 参数滑块 ── */}
      <div className="space-y-3">
        {paramDefs.map(def => {
          const val = params[def.key] ?? def.default
          const hint = tuneHints.find(h => h.paramKey === def.key)
          const displayVal =
            def.type === "float"
              ? val.toFixed(def.step < 0.01 ? 3 : def.step < 0.1 ? 2 : 1)
              : String(val)
          const pct = ((val - def.min) / (def.max - def.min)) * 100

          return (
            <div
              key={def.key}
              className={`rounded-lg p-3 transition-colors ${
                hint ? "bg-[#0a1a2e] border border-[#58a6ff]/20" : "bg-[#0d1117]"
              }`}
            >
              <div className="flex justify-between items-center mb-2">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-[#e6edf3]">{def.label}</span>
                  {hint && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-[#58a6ff]/15 text-[#58a6ff]">
                      建议 → {hint.to}
                    </span>
                  )}
                </div>
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
                style={{
                  background: `linear-gradient(to right, #58a6ff ${pct}%, #30363d 0%)`,
                }}
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

      {/* ── 操作按钮 ── */}
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
