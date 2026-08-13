import { useEffect, useState } from "react"
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts"
import { Modal } from "@/components/ui/Modal"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useFactorStrategyBacktest, usePromoteFactorStrategy, usePortfolioMethods,
  validateSpec, METHOD_LABELS,
  type FactorStrategySpec, type FactorBacktestResult,
} from "@/hooks/useFactorStrategy"
import type { Market, Frequency } from "@/types"
import { parseUniverse } from "./universeConfig"

interface FactorStrategyDialogProps {
  open: boolean
  onClose: () => void
  /** 打开时的初始 spec（因子库条目 / 挖掘结果行把自己的公式填进来） */
  initialSpec: FactorStrategySpec
  market: Market
  freq: Frequency
  /** 来源实验记录 id（可选，仅溯源；策略独立存完整 spec） */
  experimentId?: string
  defaultName?: string
  title?: string
}

/**
 * 因子 → 策略的操作台：编辑 spec、一键回测、注册为命名策略。
 *
 * 「一键回测」不需要先注册 —— 这正是 V3 想打通的主动脉：
 * 看到一个因子，立刻知道它能不能赚钱。
 */
export function FactorStrategyDialog({
  open, onClose, initialSpec, market, freq, experimentId, defaultName,
  title = "因子 → 策略",
}: FactorStrategyDialogProps) {
  const [spec, setSpec] = useState<FactorStrategySpec>(initialSpec)
  const [universeText, setUniverseText] = useState(initialSpec.universe.join(", "))
  const [name, setName] = useState(defaultName ?? "")
  const { toast } = useToast()

  const { data: methodData } = usePortfolioMethods()
  const backtest = useFactorStrategyBacktest()
  const promote = usePromoteFactorStrategy()

  // 每次重新打开都回到调用方给的初始 spec，避免上一次的编辑残留
  useEffect(() => {
    if (!open) return
    setSpec(initialSpec)
    setUniverseText(initialSpec.universe.join(", "))
    setName(defaultName ?? "")
    backtest.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialSpec, defaultName])

  function patch(change: Partial<FactorStrategySpec>) {
    setSpec((prev) => ({ ...prev, ...change }))
  }

  function currentSpec(): FactorStrategySpec {
    return { ...spec, universe: parseUniverse(universeText) }
  }

  function handleBacktest() {
    const next = currentSpec()
    const problem = validateSpec(next)
    if (problem) { toast(problem, "warning"); return }
    backtest.mutate(
      { spec: next, market, frequency: freq, initial_cash: 1_000_000, allow_short: next.short_quantile != null },
      { onError: (e) => toast(e.message, "error") },
    )
  }

  function handlePromote() {
    const next = currentSpec()
    const problem = validateSpec(next)
    if (problem) { toast(problem, "warning"); return }
    if (!name.trim()) { toast("请给策略起个名字", "warning"); return }
    promote.mutate(
      { name: name.trim(), spec: next, experiment_id: experimentId },
      {
        onSuccess: () => toast(`已注册为策略「${name.trim()}」`, "success"),
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  const methods = methodData?.methods ?? ["equal_weight"]

  return (
    <Modal open={open} onClose={onClose} title={title} className="max-w-3xl">
      <div className="space-y-4">
        <SpecEditor
          spec={spec}
          universeText={universeText}
          methods={methods}
          onPatch={patch}
          onUniverseText={setUniverseText}
        />

        <div className="flex flex-wrap items-end gap-2 border-t border-[#21262d] pt-4">
          <button
            onClick={handleBacktest}
            disabled={backtest.isPending}
            className="btn-primary text-xs px-4 py-2 disabled:opacity-50"
          >
            {backtest.isPending ? "回测中…" : "▶ 一键回测"}
          </button>
          <div className="flex-1 min-w-[160px]">
            <label className="label block mb-1">策略名</label>
            <input
              className="input w-full text-xs"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：动量风险调整组合"
            />
          </div>
          <button
            onClick={handlePromote}
            disabled={promote.isPending}
            className="text-xs px-4 py-2 rounded border border-[#3fb950]/40 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors disabled:opacity-50"
          >
            {promote.isPending ? "注册中…" : "★ 注册为策略"}
          </button>
        </div>

        {backtest.isPending && (
          <div className="flex items-center justify-center h-40"><Spinner /></div>
        )}
        {backtest.data && <BacktestReport result={backtest.data} />}
      </div>
    </Modal>
  )
}

// ── spec 编辑器 ────────────────────────────────────────────────────

interface SpecEditorProps {
  spec: FactorStrategySpec
  universeText: string
  methods: string[]
  onPatch: (change: Partial<FactorStrategySpec>) => void
  onUniverseText: (v: string) => void
}

function SpecEditor({ spec, universeText, methods, onPatch, onUniverseText }: SpecEditorProps) {
  const count = parseUniverse(universeText).length
  return (
    <div className="space-y-3">
      {spec.library_factor ? (
        // 因子库条目走 compute 直接打分，没有可编辑的表达式 —— 展示即可。
        // 它的 expr 是 Qlib 风格的展示标注，与 RPN 不是同一种语言，不能填进上面的输入框。
        <div>
          <label className="label block mb-1">因子库条目</label>
          <div className="input w-full font-mono text-xs flex items-center bg-[#0d1117] text-[#bc8cff]">
            {spec.library_factor}
          </div>
          <p className="text-[10px] text-[#6e7681] mt-1">
            来自声明式因子库，直接用其内置算子打分，无需 RPN 表达式
          </p>
        </div>
      ) : (
        <div>
          <label className="label block mb-1">RPN 公式</label>
          <input
            className="input w-full font-mono text-xs"
            value={spec.formula}
            onChange={(e) => onPatch({ formula: e.target.value })}
            placeholder="MOM20 ATR_RATIO DIV"
          />
          <p className="text-[10px] text-[#6e7681] mt-1">空格分隔的逆波兰表达式，可在「公式因子」页签构建</p>
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label">标的池</label>
          <span className="text-[10px] text-[#6e7681]">{count} 只 · 需 ≥ 2</span>
        </div>
        <textarea
          className="input w-full font-mono text-xs h-14 resize-none uppercase"
          value={universeText}
          onChange={(e) => onUniverseText(e.target.value)}
          placeholder="AAPL, MSFT, NVDA, AMZN, TSLA"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <NumberField
          label="多头比例" hint="0.2 = 分数最高的前 20%"
          value={spec.long_quantile} step={0.05} min={0.05} max={1}
          onChange={(v) => onPatch({ long_quantile: v ?? 0.2 })}
        />
        <NumberField
          label="空头比例" hint="留空 = 纯多头"
          value={spec.short_quantile} step={0.05} min={0} max={0.95} nullable
          onChange={(v) => onPatch({ short_quantile: v })}
        />
        <NumberField
          label="再平衡（天）" hint="每 N 天重排一次"
          value={spec.rebalance_days} step={1} min={1} max={250}
          onChange={(v) => onPatch({ rebalance_days: v ?? 1 })}
        />
        <NumberField
          label="持仓上限" hint="留空 = 不限"
          value={spec.max_positions} step={1} min={1} max={100} nullable
          onChange={(v) => onPatch({ max_positions: v })}
        />
      </div>

      <div>
        <label className="label block mb-1">组合权重方法</label>
        <select
          className="input w-full text-xs"
          value={spec.portfolio_method}
          onChange={(e) => onPatch({ portfolio_method: e.target.value })}
        >
          {methods.map((m) => (
            <option key={m} value={m}>{METHOD_LABELS[m] ?? m}</option>
          ))}
        </select>
      </div>
    </div>
  )
}

interface NumberFieldProps {
  label: string
  hint?: string
  value: number | null
  step: number
  min: number
  max: number
  nullable?: boolean
  onChange: (v: number | null) => void
}

function NumberField({ label, hint, value, step, min, max, nullable, onChange }: NumberFieldProps) {
  return (
    <div>
      <label className="label block mb-1">{label}</label>
      <input
        type="number"
        className="input w-full text-xs"
        value={value ?? ""}
        step={step}
        min={min}
        max={max}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === "") { onChange(nullable ? null : min); return }
          const parsed = Number(raw)
          onChange(Number.isFinite(parsed) ? parsed : null)
        }}
      />
      {hint && <p className="text-[10px] text-[#6e7681] mt-0.5">{hint}</p>}
    </div>
  )
}

// ── 回测结果 ──────────────────────────────────────────────────────

function pct(v: number | undefined): string {
  return v == null || isNaN(v) ? "—" : `${v.toFixed(2)}%`
}

function num(v: number | undefined, d = 2): string {
  return v == null || isNaN(v) ? "—" : v.toFixed(d)
}

export function BacktestReport({ result }: { result: FactorBacktestResult }) {
  const m = result.metrics
  const positive = m.total_return_pct >= 0
  return (
    <div className="space-y-3 border-t border-[#21262d] pt-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Metric label="总收益" value={pct(m.total_return_pct)} color={positive ? "#3fb950" : "#f85149"} />
        <Metric label="年化" value={pct(m.annual_return_pct)} />
        <Metric label="夏普" value={num(m.sharpe_ratio)} />
        <Metric label="最大回撤" value={pct(m.max_drawdown_pct)} color="#f85149" />
        <Metric label="胜率" value={pct(m.win_rate_pct)} />
        <Metric label="交易数" value={String(m.total_trades)} />
        <Metric label="成交笔数" value={String(result.n_fills)} />
        <Metric label="买入持有" value={pct(m.buy_hold_return_pct)} />
      </div>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={result.equity_curve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="factor-strategy-equity" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#58a6ff" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#58a6ff" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6e7681" }} tickFormatter={(d: string) => d.slice(0, 10)} minTickGap={40} />
            <YAxis tick={{ fontSize: 9, fill: "#6e7681" }} domain={["auto", "auto"]} width={56} />
            <Tooltip
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
              labelFormatter={(d) => String(d).slice(0, 10)}
              formatter={(v: number) => [v.toLocaleString(), "净值"]}
            />
            <Area type="monotone" dataKey="value" stroke="#58a6ff" strokeWidth={1.5} fill="url(#factor-strategy-equity)" dot={false} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <p className="text-[10px] text-[#6e7681]">
        标的 {result.symbols.join(", ")} · {result.start_date.slice(0, 10)} → {result.end_date.slice(0, 10)}
        {" "}· 期末净值 {result.final_value.toLocaleString()}
      </p>
    </div>
  )
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#0d1117] border border-[#21262d] rounded px-2.5 py-1.5">
      <p className="text-[9px] text-[#6e7681]">{label}</p>
      <p className="text-xs font-mono" style={{ color: color ?? "#e6edf3" }}>{value}</p>
    </div>
  )
}
