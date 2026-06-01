import { useState, useCallback, useRef, useEffect } from "react"
import { Link } from "react-router-dom"
import type {
  WorkflowState, WorkflowStep, WorkflowData, StrategyOption, BacktestVerdict,
} from "./workflowTypes"
import {
  initWorkflowData, classifyCondition, buildStrategyOptions,
} from "./workflowTypes"
import { StepParamAdjust } from "./StepParamAdjust"
import { WorkflowHistory } from "./WorkflowHistory"
import { useSpotQuotes } from "@/hooks/useSpotQuotes"
import { useRunBacktest } from "@/hooks/useBacktest"
import { useKelly } from "@/hooks/useQuant"
import { useStartStrategy } from "@/hooks/useLiveStrategy"
import { Spinner } from "@/components/ui/Spinner"
import type { Market } from "@/types"
import { format, subYears } from "date-fns"
import {
  saveWorkflowState, loadWorkflowState, clearWorkflowState,
  appendWorkflowHistory, loadWorkflowHistory, clearWorkflowHistory,
} from "@/hooks/useWorkflowStorage"
import type { WorkflowHistoryEntry } from "@/hooks/useWorkflowStorage"

// ── 帮助工具 ──────────────────────────────────────────────────

const SUITABILITY_COLOR: Record<string, string> = {
  excellent: "#3fb950",
  good:      "#e3b341",
  fair:      "#8b949e",
  poor:      "#f85149",
}
const SUITABILITY_LABEL: Record<string, string> = {
  excellent: "强烈推荐",
  good:      "推荐",
  fair:      "一般",
  poor:      "不推荐",
}

const VERDICT_CFG: Record<BacktestVerdict, { color: string; label: string; icon: string }> = {
  pass: { color: "#3fb950", label: "合格，可继续", icon: "✓" },
  warn: { color: "#e3b341", label: "注意：指标偏弱",  icon: "⚠" },
  fail: { color: "#f85149", label: "不合格，建议调整", icon: "✗" },
}

function pct(v: number) { return `${(v * 100).toFixed(1)}%` }
function todayStr() { return format(new Date(), "yyyy-MM-dd") }
function yearsAgoStr(n: number) { return format(subYears(new Date(), n), "yyyy-MM-dd") }

// ── 步骤进度条 ────────────────────────────────────────────────

const STEP_DEFS = [
  { id: "input",    title: "选择标的",   type: "decision" },
  { id: "quote",    title: "行情快照",   type: "auto" },
  { id: "analysis", title: "技术分析",   type: "auto" },
  { id: "strategy", title: "策略选择",   type: "decision" },
  { id: "backtest", title: "回测验证",   type: "auto" },
  { id: "review",   title: "回测评估",   type: "decision" },
  { id: "kelly",    title: "风险计算",   type: "auto" },
  { id: "position", title: "仓位确认",   type: "decision" },
  { id: "paper",    title: "启动模拟盘", type: "decision" },
  { id: "live",     title: "切换实盘",   type: "decision" },
] as const

function buildInitSteps(): WorkflowStep[] {
  return STEP_DEFS.map((def, i) => {
    const step: WorkflowStep = {
      id: def.id,
      stepNumber: i + 1,
      title: def.title,
      type: def.type === "auto" ? "auto" : "decision",
      status: i === 0 ? "waiting_decision" : "pending",
    }
    return step
  })
}

function StepIndicator({ steps, currentIdx }: { steps: WorkflowStep[]; currentIdx: number }) {
  return (
    <div className="flex items-center gap-0 overflow-x-auto pb-1">
      {steps.map((step, i) => {
        const isActive = i === currentIdx
        const isDone   = step.status === "done"
        const isErr    = step.status === "error"
        return (
          <div key={step.id} className="flex items-center shrink-0">
            <div className="flex flex-col items-center">
              <div
                className={`
                  w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold
                  border transition-all
                  ${isDone ? "bg-[#1a3a1a] border-[#3fb950] text-[#3fb950]"
                  : isErr  ? "bg-[#3a1a1a] border-[#f85149] text-[#f85149]"
                  : isActive ? "bg-[#1f3d5e] border-[#58a6ff] text-[#58a6ff] shadow-[0_0_8px_#58a6ff40]"
                  : "bg-[#161b22] border-[#30363d] text-[#6e7681]"}
                `}
              >
                {isDone ? "✓" : isErr ? "!" : step.stepNumber}
              </div>
              <span className={`text-[9px] mt-0.5 whitespace-nowrap
                ${isDone ? "text-[#3fb950]" : isActive ? "text-[#58a6ff]" : "text-[#6e7681]"}
              `}>{step.title}</span>
            </div>
            {i < steps.length - 1 && (
              <div className={`w-6 h-px mx-0.5 mb-3 ${isDone ? "bg-[#3fb950]" : "bg-[#30363d]"}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: 选择标的 ──────────────────────────────────────────

function StepInput({ onConfirm }: { onConfirm: (symbol: string, market: Market) => void }) {
  const [symbol, setSymbol] = useState("")
  const [market, setMarket] = useState<Market>("US")

  const MARKETS: { value: Market; label: string; placeholder: string }[] = [
    { value: "US", label: "🇺🇸 美股", placeholder: "如 AAPL / TSLA / NVDA" },
    { value: "HK", label: "🇭🇰 港股", placeholder: "如 00700 / 09988 / 03690" },
    { value: "A",  label: "🇨🇳 A股",  placeholder: "如 000001 / 600519 / 300750" },
  ]
  const cfg = MARKETS.find(m => m.value === market)!

  return (
    <div className="space-y-4">
      <p className="text-xs text-[#8b949e] leading-relaxed">
        输入要分析的股票代码，系统将自动完成行情快照→技术分析→策略推荐→回测→风险评估的完整流程。
        <strong className="text-[#e3b341]">在需要人工判断的关键节点会暂停并给出建议。</strong>
      </p>

      <div className="flex gap-2">
        {MARKETS.map(m => (
          <button
            key={m.value}
            onClick={() => setMarket(m.value)}
            className={`flex-1 py-2 rounded-lg text-xs font-medium border transition-all ${
              market === m.value
                ? "bg-[#1f3d5e] border-[#58a6ff] text-[#58a6ff]"
                : "bg-[#161b22] border-[#30363d] text-[#8b949e] hover:border-[#58a6ff]/40"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div>
        <label className="text-[10px] text-[#8b949e] mb-1 block">股票代码</label>
        <input
          type="text"
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === "Enter" && symbol.trim() && onConfirm(symbol.trim(), market)}
          placeholder={cfg.placeholder}
          className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2.5
                     text-sm text-[#e6edf3] font-mono
                     focus:outline-none focus:border-[#58a6ff] focus:ring-1 focus:ring-[#58a6ff]/30
                     placeholder:text-[#6e7681] transition-colors"
        />
      </div>

      <button
        onClick={() => symbol.trim() && onConfirm(symbol.trim(), market)}
        disabled={!symbol.trim()}
        className="w-full py-2.5 rounded-lg bg-[#238636] text-white text-sm font-medium
                   hover:bg-[#2ea043] disabled:opacity-40 disabled:cursor-not-allowed
                   transition-colors"
      >
        开始分析 →
      </button>
    </div>
  )
}

// ── Step 2-3: 行情+分析结果卡 ────────────────────────────────

function QuoteAnalysisCard({ data }: { data: WorkflowData }) {
  const { spotQuote, conditionLabel, condition } = data
  const condCfg = condition ? classifyCondition(spotQuote?.change_pct ?? 0) : null

  return (
    <div className="grid grid-cols-2 gap-3 text-xs">
      <div className="bg-[#0d1117] rounded-lg p-3 space-y-1">
        <p className="text-[#6e7681]">当前价格</p>
        <p className="font-mono text-base font-bold text-[#e6edf3]">
          {spotQuote?.price != null ? spotQuote.price.toFixed(2) : "—"}
        </p>
        {spotQuote?.change_pct != null && (
          <p className={`font-mono text-xs ${spotQuote.change_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {spotQuote.change_pct >= 0 ? "+" : ""}{spotQuote.change_pct.toFixed(2)}%
          </p>
        )}
      </div>
      <div className="bg-[#0d1117] rounded-lg p-3 space-y-1">
        <p className="text-[#6e7681]">市场状态</p>
        <p className="font-bold" style={{ color: condCfg?.color ?? "#8b949e" }}>
          {conditionLabel || "—"}
        </p>
        <p className="text-[#6e7681] text-[10px]">基于今日涨跌幅判断</p>
      </div>
    </div>
  )
}

// ── Step 4: 策略选择（Decision）────────────────────────────

function StepStrategySelect({
  options,
  onConfirm,
}: {
  options: StrategyOption[]
  onConfirm: (s: StrategyOption) => void
}) {
  const [selected, setSelected] = useState<string>(options[0]?.id ?? "")
  const sel = options.find(o => o.id === selected)

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-[#8b949e]">
        基于当前市场状态，系统为您推荐以下策略。选择适合的策略后，将自动运行2年历史回测。
      </p>

      <div className="space-y-2">
        {options.map(opt => (
          <button
            key={opt.id}
            onClick={() => setSelected(opt.id)}
            className={`w-full text-left rounded-lg border p-3 transition-all ${
              selected === opt.id
                ? "border-[#58a6ff] bg-[#1f3d5e]/40"
                : "border-[#30363d] bg-[#0d1117] hover:border-[#58a6ff]/40"
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-[#e6edf3]">{opt.name}</span>
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded"
                    style={{ color: SUITABILITY_COLOR[opt.suitability],
                             background: SUITABILITY_COLOR[opt.suitability] + "20" }}>
                {SUITABILITY_LABEL[opt.suitability]}
              </span>
            </div>
            <p className="text-[10px] text-[#3fb950] leading-relaxed">
              ✓ {opt.reason}
            </p>
            {opt.warning && (
              <p className="text-[10px] text-[#e3b341] mt-0.5 leading-relaxed">
                ⚠ {opt.warning}
              </p>
            )}
          </button>
        ))}
      </div>

      <button
        onClick={() => sel && onConfirm(sel)}
        disabled={!sel}
        className="w-full py-2 rounded-lg bg-[#1f6feb] text-white text-xs font-medium
                   hover:bg-[#388bfd] disabled:opacity-40 transition-colors"
      >
        确认策略，开始回测 →
      </button>
    </div>
  )
}

// ── Step 6: 回测评估（Decision）────────────────────────────

function StepBacktestReview({
  result,
  verdict,
  onDecision,
}: {
  result: NonNullable<WorkflowData["backtestResult"]>
  verdict: BacktestVerdict
  onDecision: (action: "accept" | "retry" | "change_strategy") => void
}) {
  const vcfg = VERDICT_CFG[verdict]
  const m = result.metrics

  const metrics = [
    { label: "总收益率",   value: `${(m.total_return_pct).toFixed(1)}%`,      good: m.total_return_pct > 10 },
    { label: "年化收益",   value: `${(m.annual_return_pct).toFixed(1)}%`,     good: m.annual_return_pct > 8 },
    { label: "Sharpe比率", value: m.sharpe_ratio.toFixed(2),                  good: m.sharpe_ratio > 1.0 },
    { label: "最大回撤",   value: `${Math.abs(m.max_drawdown_pct).toFixed(1)}%`, good: Math.abs(m.max_drawdown_pct) < 25 },
    { label: "胜率",       value: `${m.win_rate_pct.toFixed(1)}%`,            good: m.win_rate_pct > 50 },
    { label: "盈亏比",     value: m.profit_factor.toFixed(2),                 good: m.profit_factor > 1.2 },
  ]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 py-2 px-3 rounded-lg border"
           style={{ borderColor: vcfg.color + "60", background: vcfg.color + "10" }}>
        <span className="text-lg" style={{ color: vcfg.color }}>{vcfg.icon}</span>
        <div>
          <p className="text-xs font-medium" style={{ color: vcfg.color }}>{vcfg.label}</p>
          {verdict === "warn" && (
            <p className="text-[10px] text-[#e3b341]">Sharpe偏低或回撤偏大，建议谨慎继续</p>
          )}
          {verdict === "fail" && (
            <p className="text-[10px] text-[#f85149]">核心指标不合格，建议换策略或调整参数</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {metrics.map(m => (
          <div key={m.label} className="bg-[#0d1117] rounded-lg px-2.5 py-2">
            <p className="text-[10px] text-[#6e7681]">{m.label}</p>
            <p className={`font-mono text-sm font-semibold ${m.good ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {m.value}
            </p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-2 pt-1">
        <button
          onClick={() => onDecision("accept")}
          className="py-2 rounded-lg text-[10px] font-medium bg-[#1a3a1a] text-[#3fb950]
                     border border-[#3fb950]/30 hover:bg-[#1e4a1e] transition-colors"
        >
          ✓ 接受结果
          <br /><span className="text-[9px] text-[#56d364]/70">继续下一步</span>
        </button>
        <button
          onClick={() => onDecision("retry")}
          className="py-2 rounded-lg text-[10px] font-medium bg-[#2a2200] text-[#e3b341]
                     border border-[#e3b341]/30 hover:bg-[#332800] transition-colors"
        >
          ↺ 调整参数
          <br /><span className="text-[9px] text-[#e3b341]/70">修改后重跑</span>
        </button>
        <button
          onClick={() => onDecision("change_strategy")}
          className="py-2 rounded-lg text-[10px] font-medium bg-[#2a1a1a] text-[#f85149]
                     border border-[#f85149]/30 hover:bg-[#3a1a1a] transition-colors"
        >
          ✗ 换策略
          <br /><span className="text-[9px] text-[#f85149]/70">重新选择</span>
        </button>
      </div>
    </div>
  )
}

// ── Step 8: 仓位确认（Decision）────────────────────────────

function StepPositionConfirm({
  kelly,
  onConfirm,
}: {
  kelly: NonNullable<WorkflowData["kellyResult"]>
  onConfirm: (pct: number) => void
}) {
  const [choice, setChoice] = useState<"full" | "half" | "quarter" | "custom">("half")
  const [custom, setCustom] = useState(10)

  const resolvedPct = choice === "full" ? kelly.full_kelly
    : choice === "half" ? kelly.half_kelly
    : choice === "quarter" ? kelly.quarter_kelly
    : custom / 100

  const opts = [
    { id: "full",    label: "完整Kelly",      value: pct(kelly.full_kelly), note: "最大化期望增长，风险最高", color: "#f85149" },
    { id: "half",    label: "半Kelly（推荐）", value: pct(kelly.half_kelly), note: "收益≈全Kelly的75%，回撤大幅降低", color: "#3fb950" },
    { id: "quarter", label: "1/4 Kelly",      value: pct(kelly.quarter_kelly), note: "最保守，适合新手", color: "#58a6ff" },
    { id: "custom",  label: "自定义",         value: `${custom}%`, note: "手动输入", color: "#8b949e" },
  ] as const

  return (
    <div className="space-y-3">
      <div className="bg-[#0d1117] rounded-lg p-3 text-xs space-y-1">
        <p className="text-[#6e7681]">凯利准则分析</p>
        <div className="grid grid-cols-3 gap-2 mt-1.5">
          <span className="text-[#8b949e]">历史胜率<br /><strong className="text-[#e6edf3]">{pct(kelly.win_rate)}</strong></span>
          <span className="text-[#8b949e]">盈亏比<br /><strong className="text-[#e6edf3]">{kelly.odds_ratio.toFixed(2)}</strong></span>
          <span className="text-[#8b949e]">期望收益<br /><strong className={kelly.edge > 0 ? "text-[#3fb950]" : "text-[#f85149]"}>{kelly.edge.toFixed(3)}</strong></span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {opts.map(opt => (
          <button
            key={opt.id}
            onClick={() => setChoice(opt.id)}
            className={`text-left p-2.5 rounded-lg border transition-all ${
              choice === opt.id ? `border-[${opt.color}] bg-[${opt.color}]/10` : "border-[#30363d] bg-[#0d1117]"
            }`}
            style={choice === opt.id ? { borderColor: opt.color, background: opt.color + "18" } : {}}
          >
            <div className="flex justify-between items-center mb-0.5">
              <span className="text-[10px] font-medium text-[#e6edf3]">{opt.label}</span>
              <span className="font-mono text-xs font-bold" style={{ color: opt.color }}>{opt.value}</span>
            </div>
            <p className="text-[9px] text-[#6e7681]">{opt.note}</p>
          </button>
        ))}
      </div>

      {choice === "custom" && (
        <div className="flex items-center gap-2">
          <input
            type="range" min={1} max={50} value={custom}
            onChange={e => setCustom(Number(e.target.value))}
            className="flex-1"
          />
          <span className="font-mono text-sm text-[#58a6ff] w-10">{custom}%</span>
        </div>
      )}

      <button
        onClick={() => onConfirm(resolvedPct)}
        className="w-full py-2.5 rounded-lg bg-[#1f6feb] text-white text-xs font-medium
                   hover:bg-[#388bfd] transition-colors"
      >
        确认仓位 {pct(resolvedPct)}，启动模拟盘 →
      </button>
    </div>
  )
}

// ── Step 9: 模拟盘确认（Decision）──────────────────────────

function StepPaperConfirm({
  data,
  onConfirm,
  onCancel,
}: {
  data: WorkflowData
  onConfirm: () => void
  onCancel: () => void
}) {
  const { symbol, market, selectedStrategy, confirmedPositionPct } = data
  return (
    <div className="space-y-3">
      <p className="text-xs text-[#8b949e]">请确认以下配置后，系统将自动在模拟盘中启动策略。</p>

      <div className="bg-[#0d1117] rounded-lg p-3 space-y-2 text-xs">
        {[
          { label: "标的",     value: `${symbol} (${market === "US" ? "美股" : market === "HK" ? "港股" : "A股"})` },
          { label: "策略",     value: selectedStrategy?.name ?? "—" },
          { label: "仓位比例", value: pct(confirmedPositionPct) },
          { label: "模式",     value: "模拟盘（Paper Trading）" },
        ].map(row => (
          <div key={row.label} className="flex justify-between">
            <span className="text-[#6e7681]">{row.label}</span>
            <span className="text-[#e6edf3] font-mono">{row.value}</span>
          </div>
        ))}
      </div>

      <div className="bg-[#1a2a1a] border border-[#3fb950]/30 rounded-lg p-2.5 text-[10px] text-[#3fb950]">
        ✓ 模拟盘不使用真实资金，请观察运行至少1~2周后再考虑切换实盘。
      </div>

      <div className="flex gap-2">
        <button onClick={onCancel} className="flex-1 py-2 rounded-lg border border-[#30363d] text-[#8b949e] text-xs hover:bg-[#21262d] transition-colors">
          取消
        </button>
        <button onClick={onConfirm} className="flex-2 flex-1 py-2 rounded-lg bg-[#238636] text-white text-xs font-medium hover:bg-[#2ea043] transition-colors">
          ✓ 启动模拟盘
        </button>
      </div>
    </div>
  )
}

// ── Step 10: 实盘确认（Decision）──────────────────────────

function StepLiveConfirm({ onConfirm, onKeepPaper }: { onConfirm: () => void; onKeepPaper: () => void }) {
  return (
    <div className="space-y-3">
      <div className="bg-[#2a1a1a] border border-[#f85149]/40 rounded-lg p-3 text-xs text-[#f85149]">
        ⚠️ 切换实盘将使用<strong>真实资金</strong>。请确认已在Alpaca实盘账户下配置好密钥，且充分了解风险。
      </div>

      <div className="text-xs text-[#8b949e] space-y-1">
        <p>✓ 建议切换实盘的条件：</p>
        <ul className="space-y-0.5 ml-3">
          <li>▸ 模拟盘运行≥2周，实际表现与回测差距&lt;20%</li>
          <li>▸ 风控规则已配置（最大回撤限制、日亏损限制）</li>
          <li>▸ 仓位不超过账户资金的50%</li>
          <li>▸ 清楚该策略在哪些市场条件下会失效</li>
        </ul>
      </div>

      <div className="flex gap-2">
        <button onClick={onKeepPaper}
                className="flex-1 py-2.5 rounded-lg border border-[#30363d] text-[#8b949e] text-xs hover:bg-[#21262d] transition-colors">
          继续观察模拟盘
        </button>
        <button onClick={onConfirm}
                className="flex-1 py-2.5 rounded-lg bg-[#f85149]/20 border border-[#f85149]/50 text-[#f85149] text-xs font-medium hover:bg-[#f85149]/30 transition-colors">
          确认切换实盘
        </button>
      </div>
    </div>
  )
}

// ── 主工作流组件 ──────────────────────────────────────────────

const INIT_STATE: WorkflowState = {
  phase: "idle",
  currentStepIndex: 0,
  steps: buildInitSteps(),
  data: initWorkflowData(),
}

export function TradingWorkflow() {
  // ── 恢复上次进度（localStorage）──────────────────────────────
  const [state, setState] = useState<WorkflowState>(() => {
    const saved = loadWorkflowState()
    return (saved && saved.phase === "running") ? saved : INIT_STATE
  })

  // 历史记录列表（只在空闲/完成态显示）
  const [history, setHistory] = useState<WorkflowHistoryEntry[]>(() => loadWorkflowHistory())

  // Ref to always access latest workflow data in async callbacks (avoids stale closure)
  const dataRef = useRef<WorkflowData>(state.data)
  useEffect(() => { dataRef.current = state.data }, [state.data])

  const { data: spotData } = useSpotQuotes(state.phase === "running")
  const runBacktest  = useRunBacktest()
  const calcKelly    = useKelly()
  const startStrategy = useStartStrategy()
  const timerRef     = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── 持久化：运行中时自动保存到 localStorage ────────────────
  useEffect(() => {
    if (state.phase === "running") saveWorkflowState(state)
  }, [state])

  // 工具：更新单个步骤状态
  const updateStep = useCallback((idx: number, patch: Partial<WorkflowStep>) => {
    setState(s => {
      const steps = s.steps.map((st, i) => i === idx ? { ...st, ...patch } : st)
      return { ...s, steps }
    })
  }, [])

  // ── Step 1: 用户确认标的 ────────────────────────────────────
  const handleInputConfirm = useCallback(async (symbol: string, market: Market) => {
    setState(s => ({
      ...s,
      phase: "running",
      data: { ...s.data, symbol, market },
    }))
    updateStep(0, { status: "done", summary: `${symbol} (${market})` })

    // 自动执行 Step 2: 行情快照
    setState(s => ({
      ...s,
      currentStepIndex: 1,
      steps: s.steps.map((st, i) => i === 1 ? { ...st, status: "running" } : st),
    }))

    // 给行情数据一点时间加载
    timerRef.current = setTimeout(async () => {
      const marketKey = market as "US" | "HK" | "A"
      const quotes = spotData?.[marketKey] ?? []
      const found = quotes.find(q => q.symbol === symbol || q.symbol === symbol.replace(/^0+/, "").split(".")[0])

      // Step 2 完成
      const condCfg = classifyCondition(found?.change_pct ?? 0)
      const condition = condCfg.condition
      const label     = condCfg.label

      setState(s => ({
        ...s,
        currentStepIndex: 2,
        steps: s.steps.map((st, i) => {
          if (i === 1) return { ...st, status: "done", summary: found ? `${found.price?.toFixed(2) ?? "—"}  ${found.change_pct != null ? (found.change_pct > 0 ? "+" : "") + found.change_pct.toFixed(2) + "%" : ""}` : "数据获取中" }
          if (i === 2) return { ...st, status: "running" }
          return st
        }),
        data: { ...s.data, spotQuote: found ?? null, condition, conditionLabel: label },
      }))

      // Step 3: 技术分析（基于行情状态自动完成）
      await new Promise(r => setTimeout(r, 1200))

      const options = buildStrategyOptions(condition)
      setState(s => ({
        ...s,
        currentStepIndex: 3,
        steps: s.steps.map((st, i) => {
          if (i === 2) return { ...st, status: "done", summary: `${label} — 已生成策略推荐` }
          if (i === 3) return { ...st, status: "waiting_decision" }
          return st
        }),
        data: { ...s.data, strategyOptions: options },
      }))
    }, 1500)
  }, [spotData, updateStep])

  // ── Step 4: 用户确认策略 → 自动运行回测 ───────────────────
  // NOTE: Uses dataRef (not state) to avoid stale closure in async context
  const handleStrategyConfirm = useCallback(async (strategy: StrategyOption) => {
    // Capture symbol/market from ref before any state transitions
    const { symbol, market } = dataRef.current

    updateStep(3, { status: "done", summary: strategy.name })
    setState(s => ({
      ...s,
      currentStepIndex: 4,
      steps: s.steps.map((st, i) => i === 4 ? { ...st, status: "running" } : st),
      data: { ...s.data, selectedStrategy: strategy },
    }))

    try {
      const result = await runBacktest.mutateAsync({
        strategy_name: strategy.id,
        symbol,
        market:        market as Market,
        start_date:    yearsAgoStr(2),
        end_date:      todayStr(),
        initial_cash:  100_000,
        params:        strategy.params,
        frequency:     "1d",
      })

      // 判断回测结论
      const m      = result.metrics
      const sharpe = m.sharpe_ratio
      const dd     = Math.abs(m.max_drawdown_pct)
      const wr     = m.win_rate_pct
      const verdict: BacktestVerdict =
        (sharpe > 1.0 && dd < 25 && wr > 50) ? "pass"
        : (sharpe > 0.5 && dd < 35)           ? "warn"
        : "fail"

      setState(s => ({
        ...s,
        currentStepIndex: 5,
        steps: s.steps.map((st, i) => {
          if (i === 4) return { ...st, status: "done", summary: `Sharpe ${sharpe.toFixed(2)} | 回撤 ${dd.toFixed(1)}%` }
          if (i === 5) return { ...st, status: "waiting_decision" }
          return st
        }),
        data: { ...s.data, backtestResult: result, backtestVerdict: verdict },
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : "回测失败，请检查标的或时间范围"
      updateStep(4, { status: "error", errorMsg: msg })
    }
  }, [runBacktest, updateStep])

  // ── Step 6: 回测评估决策 ────────────────────────────────────
  // NOTE: Uses dataRef to avoid stale closure
  const handleBacktestDecision = useCallback(async (action: "accept" | "retry" | "change_strategy") => {
    if (action === "change_strategy") {
      setState(s => ({
        ...s,
        currentStepIndex: 3,
        steps: s.steps.map((st, i) => {
          if (i === 5) return { ...st, status: "pending" }
          if (i === 4) return { ...st, status: "pending" }
          if (i === 3) return { ...st, status: "waiting_decision" }
          return st
        }),
      }))
      return
    }
    if (action === "retry") {
      setState(s => ({
        ...s,
        currentStepIndex: 4,
        steps: s.steps.map((st, i) => {
          if (i === 5) return { ...st, status: "pending" }
          if (i === 4) return { ...st, status: "waiting_decision" }
          return st
        }),
      }))
      return
    }

    // accept → 自动计算Kelly
    updateStep(5, { status: "done", summary: "已接受回测结果" })
    setState(s => ({
      ...s,
      currentStepIndex: 6,
      steps: s.steps.map((st, i) => i === 6 ? { ...st, status: "running" } : st),
      data: { ...s.data, userAcceptedBacktest: true },
    }))

    try {
      const bt = dataRef.current.backtestResult!
      const winRate  = Math.min(Math.max((bt.metrics.win_rate_pct / 100), 0.01), 0.99)
      const avgWin   = bt.metrics.profit_factor > 0 ? bt.metrics.profit_factor * 100 : 150
      const avgLoss  = 100

      const kelly = await calcKelly.mutateAsync({ win_rate: winRate, avg_win: avgWin, avg_loss: avgLoss, fraction: 0.5, max_f: 0.25 })

      setState(s => ({
        ...s,
        currentStepIndex: 7,
        steps: s.steps.map((st, i) => {
          if (i === 6) return { ...st, status: "done", summary: `推荐仓位 ${pct(kelly.half_kelly)}（半Kelly）` }
          if (i === 7) return { ...st, status: "waiting_decision" }
          return st
        }),
        data: { ...s.data, kellyResult: kelly, recommendedPositionPct: kelly.half_kelly },
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Kelly计算失败"
      updateStep(6, { status: "error", errorMsg: msg })
    }
  }, [calcKelly, updateStep])

  // ── Step 8: 仓位确认 ────────────────────────────────────────
  const handlePositionConfirm = useCallback((pctVal: number) => {
    setState(s => ({
      ...s,
      currentStepIndex: 8,
      steps: s.steps.map((st, i) => {
        if (i === 7) return { ...st, status: "done", summary: `确认仓位 ${pct(pctVal)}` }
        if (i === 8) return { ...st, status: "waiting_decision" }
        return st
      }),
      data: { ...s.data, confirmedPositionPct: pctVal },
    }))
  }, [])

  // ── Step 9: 模拟盘启动（调用真实 API）─────────────────────
  const handlePaperConfirm = useCallback(async () => {
    const { symbol, market, selectedStrategy } = dataRef.current
    if (!selectedStrategy) return

    // 先设为 running 显示 spinner
    setState(s => ({
      ...s,
      steps: s.steps.map((st, i) => i === 8 ? { ...st, status: "running" } : st),
    }))

    try {
      const inst = await startStrategy.mutateAsync({
        strategy_name: selectedStrategy.id,
        symbol,
        market: market as Market,
        frequency: "1d",
        params: selectedStrategy.params as Record<string, unknown>,
        warmup_days: 120,
        sim_days: 60,
      })
      setState(s => ({
        ...s,
        currentStepIndex: 9,
        steps: s.steps.map((st, i) => {
          if (i === 8) return { ...st, status: "done", summary: `模拟盘运行中 · ${inst.instance_id.slice(-8)}` }
          if (i === 9) return { ...st, status: "waiting_decision" }
          return st
        }),
        data: { ...s.data, paperStrategyId: inst.instance_id },
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : "模拟盘启动失败，请重试"
      updateStep(8, { status: "error", errorMsg: msg })
    }
  }, [startStrategy, updateStep])

  // ── 保存历史记录工具函数 ─────────────────────────────────────
  const saveHistory = useCallback((phase: WorkflowHistoryEntry["phase"]) => {
    const d = dataRef.current
    const entry: WorkflowHistoryEntry = {
      id: Date.now().toString(),
      timestamp: Date.now(),
      symbol: d.symbol,
      market: d.market,
      strategyName: d.selectedStrategy?.name ?? "—",
      strategyId:   d.selectedStrategy?.id   ?? "—",
      verdict:      d.backtestVerdict,
      sharpe:       d.backtestResult?.metrics.sharpe_ratio ?? null,
      drawdown:     d.backtestResult ? Math.abs(d.backtestResult.metrics.max_drawdown_pct) : null,
      winRate:      d.backtestResult?.metrics.win_rate_pct ?? null,
      positionPct:  d.confirmedPositionPct,
      instanceId:   d.paperStrategyId,
      phase,
    }
    const updated = appendWorkflowHistory(entry)
    setHistory(updated)
  }, [])

  // ── Step 10: 实盘确认 ───────────────────────────────────────
  const handleLiveConfirm = useCallback(() => {
    saveHistory("completed")
    clearWorkflowState()
    setState(s => ({
      ...s,
      phase: "completed",
      steps: s.steps.map((st, i) => i === 9 ? { ...st, status: "done", summary: "已切换实盘" } : st),
      data: { ...s.data, liveConfirmed: true },
    }))
  }, [saveHistory])

  // ── 继续观察模拟盘（也算完成一轮流程）──────────────────────
  const handleKeepPaper = useCallback(() => {
    saveHistory("paper_only")
    clearWorkflowState()
    setState(s => ({
      ...s,
      phase: "completed",
      steps: s.steps.map((st, i) => i === 9 ? { ...st, status: "done", summary: "继续模拟盘观察" } : st),
    }))
  }, [saveHistory])

  const handleReset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    clearWorkflowState()
    setState(INIT_STATE)
  }, [])

  // ── 渲染 ────────────────────────────────────────────────────

  if (state.phase === "idle") {
    // 检查是否存在未保存的 running 状态（用户手动触发 reset 后 savedState 为空，
    // 但页面reload时若有running状态则已自动在 useState 初始化中恢复）
    const savedRunning = loadWorkflowState()
    const hasResumable = savedRunning?.phase === "running"

    return (
      <div className="card">
        <div className="card-header mb-4">
          <div>
            <h2 className="text-sm font-semibold text-[#e6edf3]">🚀 智能交易引导</h2>
            <p className="text-[10px] text-[#6e7681] mt-0.5">从选股到实盘的全流程自动化引导，系统自动执行分析，关键决策由您把控</p>
          </div>
        </div>

        {/* 未完成流程恢复提示 */}
        {hasResumable && savedRunning && (
          <div className="mb-4 bg-[#1f3d5e]/30 border border-[#58a6ff]/30 rounded-lg p-3 space-y-2">
            <p className="text-xs font-medium text-[#58a6ff]">⚡ 上次有未完成的分析流程</p>
            <p className="text-[10px] text-[#8b949e]">
              {savedRunning.data.symbol} ({savedRunning.data.market}) ·
              第 {savedRunning.currentStepIndex + 1}/{savedRunning.steps.length} 步
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setState(savedRunning)}
                className="flex-1 py-1.5 rounded-lg bg-[#1f6feb] text-white text-xs font-medium hover:bg-[#388bfd] transition-colors"
              >
                继续上次
              </button>
              <button
                onClick={() => { clearWorkflowState(); setState(INIT_STATE) }}
                className="flex-1 py-1.5 rounded-lg border border-[#30363d] text-[#8b949e] text-xs hover:bg-[#21262d] transition-colors"
              >
                重新开始
              </button>
            </div>
          </div>
        )}

        <StepInput onConfirm={handleInputConfirm} />

        <WorkflowHistory
          entries={history}
          onClear={() => { clearWorkflowHistory(); setHistory([]) }}
        />
      </div>
    )
  }

  if (state.phase === "completed") {
    const d = state.data
    const m = d.backtestResult?.metrics
    return (
      <div className="card space-y-4">
        {/* 完成标题 */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#1a3a1a] border border-[#3fb950] flex items-center justify-center text-lg shrink-0">✓</div>
          <div>
            <p className="text-sm font-semibold text-[#3fb950]">全流程完成！</p>
            <p className="text-[10px] text-[#6e7681]">
              {d.symbol} · {d.selectedStrategy?.name ?? "—"} · 仓位 {pct(d.confirmedPositionPct)}
            </p>
          </div>
        </div>

        {/* 回测摘要 */}
        {m && (
          <div className="grid grid-cols-3 gap-2 text-xs bg-[#0d1117] rounded-lg p-3">
            <div><p className="text-[#6e7681]">Sharpe</p><p className="font-mono font-bold text-[#e6edf3]">{m.sharpe_ratio.toFixed(2)}</p></div>
            <div><p className="text-[#6e7681]">最大回撤</p><p className="font-mono font-bold text-[#f85149]">{Math.abs(m.max_drawdown_pct).toFixed(1)}%</p></div>
            <div><p className="text-[#6e7681]">胜率</p><p className="font-mono font-bold text-[#3fb950]">{m.win_rate_pct.toFixed(1)}%</p></div>
          </div>
        )}

        {/* 模拟盘实例信息 */}
        {d.paperStrategyId && (
          <div className="bg-[#1a2a1a] border border-[#3fb950]/30 rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-[#3fb950] font-medium">▶ 模拟盘运行中</p>
              <span className="font-mono text-[10px] text-[#6e7681]">{d.paperStrategyId.slice(-12)}</span>
            </div>
            <Link
              to="/live-strategy"
              className="flex items-center justify-between w-full py-2 px-3 rounded-lg bg-[#1f3d5e]/40 border border-[#58a6ff]/30 text-xs text-[#58a6ff] hover:bg-[#1f3d5e]/60 transition-colors"
            >
              <span>前往「实盘策略」查看运行状态</span>
              <span>→</span>
            </Link>
          </div>
        )}

        {/* 快捷导航 */}
        <div className="grid grid-cols-2 gap-2 text-[10px]">
          {[
            { to: "/live-strategy", icon: "▶", label: "实盘策略监控" },
            { to: "/risk",          icon: "⚑", label: "风控中心" },
            { to: "/orders",        icon: "≡", label: "订单记录" },
            { to: "/portfolio",     icon: "◈", label: "持仓分析" },
          ].map(item => (
            <Link
              key={item.to}
              to={item.to}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#58a6ff]/40 hover:bg-[#21262d] transition-colors"
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          ))}
        </div>

        <button
          onClick={handleReset}
          className="w-full py-2 rounded-lg bg-[#21262d] text-[#8b949e] text-xs hover:bg-[#30363d] transition-colors"
        >
          分析新标的
        </button>
      </div>
    )
  }

  const step = state.steps[state.currentStepIndex]
  const { data } = state

  return (
    <div className="card">
      <div className="card-header mb-4">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">
            🚀 智能交易引导
            {data.symbol && <span className="ml-2 font-mono text-[#58a6ff]">— {data.symbol}</span>}
          </h2>
          <p className="text-[10px] text-[#6e7681] mt-0.5">
            第 {state.currentStepIndex + 1} / {state.steps.length} 步 —
            {step?.type === "decision" ? " ⚡ 需要您的判断" : " ⚙ 系统自动执行中"}
          </p>
        </div>
        <button onClick={handleReset} className="text-[10px] text-[#6e7681] hover:text-[#e6edf3] transition-colors px-2 py-1 rounded hover:bg-[#21262d]">
          重置
        </button>
      </div>

      {/* 步骤进度条 */}
      <div className="mb-4 overflow-x-auto">
        <StepIndicator steps={state.steps} currentIdx={state.currentStepIndex} />
      </div>

      {/* 已完成步骤的摘要 */}
      {state.steps.filter(s => s.status === "done" && s.summary).length > 0 && (
        <div className="mb-3 space-y-1">
          {state.steps.filter(s => s.status === "done" && s.summary).map(s => (
            <div key={s.id} className="flex items-center gap-2 text-[10px] text-[#6e7681] bg-[#0d1117] rounded px-2 py-1">
              <span className="text-[#3fb950]">✓</span>
              <span className="font-medium text-[#8b949e]">{s.title}：</span>
              <span>{s.summary}</span>
            </div>
          ))}
        </div>
      )}

      {/* 当前步骤内容 */}
      <div className="border border-[#30363d] rounded-lg p-4 bg-[#0d1117]">
        {/* 步骤标题 */}
        <div className="flex items-center gap-2 mb-3">
          {step?.status === "running" && <Spinner />}
          {step?.type === "decision" && step?.status === "waiting_decision" && (
            <span className="text-[#e3b341] text-sm">⚡</span>
          )}
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            步骤 {step?.stepNumber}：{step?.title}
          </h3>
          {step?.type === "decision" && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2a2200] text-[#e3b341] border border-[#e3b341]/30">
              需判断
            </span>
          )}
        </div>

        {/* 步骤内容区 */}
        {step?.status === "running" && (
          <div className="flex items-center gap-3 py-6 text-sm text-[#8b949e]">
            <Spinner />
            <span>正在自动执行，请稍候...</span>
          </div>
        )}

        {step?.status === "error" && (
          <div className="space-y-3">
            <div className="bg-[#2a1a1a] border border-[#f85149]/40 rounded-lg p-3 text-xs text-[#f85149]">
              ✗ {step.errorMsg ?? "执行出错，请重试"}
            </div>
            <div className="flex gap-2">
              {step.id === "backtest" && data.selectedStrategy && (
                <button
                  onClick={() => handleStrategyConfirm(data.selectedStrategy!)}
                  className="flex-1 py-2 rounded-lg bg-[#1f3d5e] text-[#58a6ff] text-xs border border-[#58a6ff]/30 hover:bg-[#1f3d5e]/80 transition-colors"
                >
                  ↺ 重新回测
                </button>
              )}
              {step.id === "paper" && (
                <button
                  onClick={handlePaperConfirm}
                  className="flex-1 py-2 rounded-lg bg-[#1a2a1a] text-[#3fb950] text-xs border border-[#3fb950]/30 hover:bg-[#1e3a1e] transition-colors"
                >
                  ↺ 重新启动模拟盘
                </button>
              )}
              <button
                onClick={handleReset}
                className="flex-1 py-2 rounded-lg border border-[#30363d] text-[#8b949e] text-xs hover:bg-[#21262d] transition-colors"
              >
                重置流程
              </button>
            </div>
          </div>
        )}

        {/* Step 4: 策略选择（含行情概要）*/}
        {step?.id === "strategy" && step.status === "waiting_decision" && (
          <div className="space-y-3">
            <QuoteAnalysisCard data={data} />
            <StepStrategySelect options={data.strategyOptions} onConfirm={handleStrategyConfirm} />
          </div>
        )}

        {/* Step 5: 参数调整（点击「调整参数」后的 retry 路径）*/}
        {step?.id === "backtest" && step.status === "waiting_decision" && data.selectedStrategy && (
          <StepParamAdjust
            strategy={data.selectedStrategy}
            backtestResult={data.backtestResult}
            verdict={data.backtestVerdict}
            onRetry={handleStrategyConfirm}
            onChangeStrategy={() => handleBacktestDecision("change_strategy")}
          />
        )}

        {/* Step 6: 回测评估 — 决策步骤，需用户操作 */}
        {step?.id === "review" && step.status === "waiting_decision" && data.backtestResult && data.backtestVerdict && (
          <StepBacktestReview
            result={data.backtestResult}
            verdict={data.backtestVerdict}
            onDecision={handleBacktestDecision}
          />
        )}

        {/* Step 8: 仓位确认 */}
        {step?.id === "position" && step.status === "waiting_decision" && data.kellyResult && (
          <StepPositionConfirm kelly={data.kellyResult} onConfirm={handlePositionConfirm} />
        )}

        {/* Step 9: 模拟盘确认 */}
        {step?.id === "paper" && step.status === "waiting_decision" && (
          <StepPaperConfirm data={data} onConfirm={handlePaperConfirm} onCancel={handleReset} />
        )}

        {/* Step 10: 实盘确认 */}
        {step?.id === "live" && step.status === "waiting_decision" && (
          <StepLiveConfirm onConfirm={handleLiveConfirm} onKeepPaper={handleKeepPaper} />
        )}
      </div>
    </div>
  )
}
