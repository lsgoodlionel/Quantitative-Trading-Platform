import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import {
  useLiveStrategies,
  useStartStrategy,
  useStopStrategy,
  useDeleteStrategyInstance,
} from "@/hooks/useLiveStrategy"
import { useStrategies } from "@/hooks/useBacktest"
import type { LiveStrategyInstance, LiveStrategyState, Market, Frequency } from "@/types"

// ── 常量 ──────────────────────────────────────────────────────

const MARKETS: { value: Market; label: string }[] = [
  { value: "US", label: "🇺🇸 美股" },
  { value: "HK", label: "🇭🇰 港股" },
  { value: "A",  label: "🇨🇳 A股"  },
]

const FREQS: { value: Frequency; label: string }[] = [
  { value: "1m",  label: "1分钟" },
  { value: "5m",  label: "5分钟" },
  { value: "15m", label: "15分钟" },
  { value: "1h",  label: "1小时" },
  { value: "1d",  label: "日线" },
]

const STATE_CFG: Record<LiveStrategyState, { label: string; dot: string; text: string }> = {
  idle:    { label: "待机", dot: "bg-[#30363d]",   text: "text-[#8b949e]" },
  running: { label: "运行中", dot: "bg-[#3fb950]",  text: "text-[#3fb950]" },
  stopped: { label: "已停止", dot: "bg-[#8b949e]",  text: "text-[#8b949e]" },
  error:   { label: "错误",   dot: "bg-[#f85149]",  text: "text-[#f85149]" },
}

const STRATEGY_LABELS: Record<string, string> = {
  double_ma:          "双均线交叉",
  bollinger:          "布林带",
  macd:               "MACD 信号",
  rsi_mean_reversion: "RSI 均值回归",
  momentum:           "动量策略",
  grid_trading:       "网格交易",
  pairs_trading:      "配对交易",
  multi_factor:       "多因子模型",
}

// ── 工具函数 ──────────────────────────────────────────────────

function elapsed(startedAt: string | null): string {
  if (!startedAt) return "—"
  const diff = Date.now() - new Date(startedAt).getTime()
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  const s = Math.floor((diff % 60_000) / 1_000)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

// ── 策略状态徽章 ──────────────────────────────────────────────

function StateBadge({ state }: { state: LiveStrategyState }) {
  const cfg = STATE_CFG[state]
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${cfg.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} ${state === "running" ? "animate-pulse" : ""}`} />
      {cfg.label}
    </span>
  )
}

// ── 策略实例卡片 ──────────────────────────────────────────────

interface InstanceCardProps {
  inst: LiveStrategyInstance
  onStop: (id: string) => void
  onDelete: (id: string) => void
  isStoping: boolean
}

function InstanceCard({ inst, onStop, onDelete, isStoping }: InstanceCardProps) {
  const [expanded, setExpanded] = useState(false)
  const isRunning = inst.state === "running"

  return (
    <div className={`card border rounded-xl transition-colors ${
      isRunning ? "border-[#3fb950]/20" : "border-[#30363d]"
    }`}>
      {/* 头部 */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StateBadge state={inst.state} />
            <span className="text-[10px] text-[#6e7681] font-mono truncate">
              {inst.instance_id}
            </span>
          </div>
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            {STRATEGY_LABELS[inst.strategy_name] ?? inst.strategy_name}
          </h3>
          <p className="text-xs text-[#8b949e] mt-0.5">
            {inst.symbol} · {inst.market} · {inst.frequency}
          </p>
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-2 shrink-0">
          <button
            onClick={() => setExpanded(!expanded)}
            className="px-2 py-1 rounded text-[10px] text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3] transition-colors"
          >
            {expanded ? "收起" : "详情"}
          </button>
          {isRunning ? (
            <button
              onClick={() => onStop(inst.instance_id)}
              disabled={isStoping}
              className="px-3 py-1 rounded text-xs font-medium bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/30 hover:bg-[#f85149]/10 disabled:opacity-50 transition-colors"
            >
              {isStoping ? <Spinner size="sm" className="inline-block" /> : "停止"}
            </button>
          ) : (
            <button
              onClick={() => onDelete(inst.instance_id)}
              className="px-3 py-1 rounded text-xs font-medium text-[#6e7681] border border-[#30363d] hover:text-[#f85149] hover:border-[#f85149]/30 transition-colors"
            >
              删除
            </button>
          )}
        </div>
      </div>

      {/* 统计指标 */}
      <div className="grid grid-cols-3 gap-3 mt-3 pt-3 border-t border-[#21262d]">
        <div>
          <p className="text-[10px] text-[#6e7681]">已处理 K 线</p>
          <p className="text-sm font-mono text-[#e6edf3]">{inst.bars_processed.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681]">已下单</p>
          <p className={`text-sm font-mono ${inst.orders_placed > 0 ? "text-[#3fb950]" : "text-[#8b949e]"}`}>
            {inst.orders_placed}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681]">运行时长</p>
          <p className="text-sm font-mono text-[#e6edf3]">{elapsed(inst.started_at)}</p>
        </div>
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-[#21262d] space-y-2">
          {inst.error && (
            <div className="bg-[#2a1b1b] border border-[#f85149]/30 rounded-lg p-2">
              <p className="text-[10px] text-[#f85149] font-mono">{inst.error}</p>
            </div>
          )}
          <div>
            <p className="text-[10px] text-[#6e7681] mb-1">策略参数</p>
            <pre className="text-[10px] text-[#8b949e] bg-[#161b22] rounded p-2 overflow-auto">
              {JSON.stringify(inst.params, null, 2) || "{}（默认）"}
            </pre>
          </div>
          {inst.started_at && (
            <p className="text-[10px] text-[#6e7681]">
              启动时间：{new Date(inst.started_at).toLocaleString("zh-CN")}
            </p>
          )}
          {inst.stopped_at && (
            <p className="text-[10px] text-[#6e7681]">
              停止时间：{new Date(inst.stopped_at).toLocaleString("zh-CN")}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── 启动策略表单 ──────────────────────────────────────────────

interface LaunchFormProps {
  strategies: { name: string; description: string }[]
  onClose: () => void
}

function LaunchForm({ strategies, onClose }: LaunchFormProps) {
  const { toast } = useToast()
  const { mutate: startStrategy, isPending } = useStartStrategy()

  const [stratName, setStratName] = useState(strategies[0]?.name ?? "double_ma")
  const [symbol, setSymbol] = useState("AAPL")
  const [market, setMarket] = useState<Market>("US")
  const [frequency, setFrequency] = useState<Frequency>("1d")
  const [warmupDays, setWarmupDays] = useState("120")
  const [paramsJson, setParamsJson] = useState("{}")
  const [paramsError, setParamsError] = useState("")

  function validateParams(raw: string): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(raw)
      setParamsError("")
      return parsed
    } catch {
      setParamsError("参数 JSON 格式错误")
      return null
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const params = validateParams(paramsJson)
    if (!params) return
    if (!symbol.trim()) { toast("请输入标的代码", "warning"); return }

    startStrategy(
      {
        strategy_name: stratName,
        symbol: symbol.trim().toUpperCase(),
        market,
        frequency,
        params,
        warmup_days: parseInt(warmupDays) || 120,
      },
      {
        onSuccess: (inst) => {
          toast(`策略已启动：${inst.instance_id}`, "success")
          onClose()
        },
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="card border border-[#58a6ff]/20 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#e6edf3]">启动策略实例</h3>
        <button type="button" onClick={onClose}
          className="text-[#6e7681] hover:text-[#e6edf3] text-lg leading-none">×</button>
      </div>

      {/* 策略选择 */}
      <div>
        <label className="label block mb-1.5">策略</label>
        <select className="select w-full" value={stratName} onChange={(e) => setStratName(e.target.value)}>
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>
              {STRATEGY_LABELS[s.name] ?? s.name}
            </option>
          ))}
        </select>
      </div>

      {/* 标的 + 市场 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label block mb-1.5">标的代码</label>
          <input className="input w-full font-mono uppercase" value={symbol}
            onChange={(e) => setSymbol(e.target.value)} placeholder="AAPL" />
        </div>
        <div>
          <label className="label block mb-1.5">市场</label>
          <div className="flex gap-1">
            {MARKETS.map((m) => (
              <button key={m.value} type="button" onClick={() => setMarket(m.value)}
                className={`flex-1 py-1 rounded text-xs border transition-colors ${
                  market === m.value
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                    : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>
                {m.value}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 频率 + 预热 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label block mb-1.5">K 线周期</label>
          <select className="select w-full" value={frequency}
            onChange={(e) => setFrequency(e.target.value as Frequency)}>
            {FREQS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label block mb-1.5">预热天数</label>
          <select className="select w-full" value={warmupDays}
            onChange={(e) => setWarmupDays(e.target.value)}>
            {["30", "60", "120", "252", "365"].map((d) => (
              <option key={d} value={d}>{d} 天</option>
            ))}
          </select>
        </div>
      </div>

      {/* 策略参数 JSON */}
      <div>
        <label className="label block mb-1.5">策略参数 (JSON)</label>
        <textarea
          className={`input w-full h-20 font-mono text-xs resize-none ${paramsError ? "border-[#f85149]" : ""}`}
          value={paramsJson}
          onChange={(e) => { setParamsJson(e.target.value); validateParams(e.target.value) }}
          placeholder='{"short_window": 10, "long_window": 30}'
        />
        {paramsError && <p className="text-[#f85149] text-[10px] mt-1">{paramsError}</p>}
        <p className="text-[10px] text-[#6e7681] mt-1">
          留空或 {"{}"}使用策略默认参数
        </p>
      </div>

      <div className="flex gap-2">
        <button type="button" onClick={onClose}
          className="flex-1 btn border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]">
          取消
        </button>
        <button type="submit" disabled={isPending} className="flex-1 btn btn-primary">
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 启动"}
        </button>
      </div>
    </form>
  )
}

// ── 主页面 ────────────────────────────────────────────────────

export function LiveStrategy() {
  const { toast } = useToast()
  const { data: instances, isLoading } = useLiveStrategies()
  const { data: strategies } = useStrategies()
  const { mutate: stopStrategy, isPending: isStopping, variables: stoppingId } = useStopStrategy()
  const { mutate: deleteInstance } = useDeleteStrategyInstance()
  const [showForm, setShowForm] = useState(false)

  const running = (instances ?? []).filter((i) => i.state === "running").length
  const stopped = (instances ?? []).filter((i) => i.state === "stopped" || i.state === "error").length

  function handleStop(id: string) {
    stopStrategy(id, {
      onSuccess: () => toast("策略已停止", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleDelete(id: string) {
    deleteInstance(id, {
      onSuccess: () => toast("记录已删除", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  return (
    <AppShell title="实盘策略" help={PAGE_HELP["live-strategy"]}>
      {/* 统计概览 */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        {[
          { label: "运行中", value: running, accent: running > 0 ? "text-[#3fb950]" : "text-[#8b949e]" },
          { label: "已停止", value: stopped, accent: "text-[#8b949e]" },
          { label: "总实例", value: (instances ?? []).length, accent: "text-[#e6edf3]" },
        ].map((s) => (
          <div key={s.label} className="card text-center py-4">
            <p className="text-xs text-[#6e7681] mb-1">{s.label}</p>
            <p className={`text-2xl font-bold font-mono ${s.accent}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* 启动按钮 */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">策略实例</h2>
          <p className="text-xs text-[#6e7681] mt-0.5">每 5 秒自动刷新状态</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="btn btn-primary text-xs px-4"
        >
          {showForm ? "取消" : "+ 启动策略"}
        </button>
      </div>

      {/* 启动表单 */}
      {showForm && strategies && (
        <div className="mb-6">
          <LaunchForm
            strategies={strategies}
            onClose={() => setShowForm(false)}
          />
        </div>
      )}

      {/* 风险提示 */}
      <div className="mb-4 px-3 py-2 bg-[#2a1a00] border border-[#e3b341]/30 rounded-lg text-xs text-[#e3b341]">
        ⚠️ 实盘/模拟盘策略将通过 OMS 实际下单。请确认风控配置正确，建议先在纸面交易（Paper Trading）模式下验证。
      </div>

      {/* 策略列表 */}
      {isLoading && (
        <div className="flex justify-center py-16">
          <Spinner size="lg" />
        </div>
      )}

      {!isLoading && (!instances || instances.length === 0) && (
        <EmptyState
          title="暂无运行中的策略"
          description='点击"+ 启动策略"选择预设策略，配置标的和参数后启动'
        />
      )}

      {instances && instances.length > 0 && (
        <div className="space-y-3">
          {/* 运行中的在前 */}
          {[...instances]
            .sort((a, b) => {
              if (a.state === "running" && b.state !== "running") return -1
              if (a.state !== "running" && b.state === "running") return 1
              return 0
            })
            .map((inst) => (
              <InstanceCard
                key={inst.instance_id}
                inst={inst}
                onStop={handleStop}
                onDelete={handleDelete}
                isStoping={isStopping && stoppingId === inst.instance_id}
              />
            ))}
        </div>
      )}

      {/* 说明 */}
      <div className="mt-8 pt-6 border-t border-[#21262d]">
        <h3 className="text-xs font-semibold text-[#8b949e] mb-3">使用说明</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[10px] text-[#6e7681]">
          <div className="card py-2.5">
            <p className="font-semibold text-[#e6edf3] mb-1">数据源</p>
            <p>策略自动订阅实时 K 线（美股：Alpaca，港股：富途，A股：akshare），历史数据用于指标预热。</p>
          </div>
          <div className="card py-2.5">
            <p className="font-semibold text-[#e6edf3] mb-1">风控保护</p>
            <p>每笔订单前自动执行风控前置检查（仓位限制/日亏损/频率），BLOCK 级违规自动拦截。</p>
          </div>
          <div className="card py-2.5">
            <p className="font-semibold text-[#e6edf3] mb-1">订单执行</p>
            <p>策略信号经 OMS 路由到对应市场网关（Alpaca/富途/纸面交易），可在「订单」页查看成交记录。</p>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
