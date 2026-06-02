import { useState, useEffect } from "react"
import { useSearchParams, Link } from "react-router-dom"
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as ReTooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import {
  useLiveStrategies, useStartStrategy, useStopStrategy, useDeleteStrategyInstance,
} from "@/hooks/useLiveStrategy"
import { useStrategies } from "@/hooks/useBacktest"
import type { LiveStrategyInstance, PaperSimResult, LiveStrategyState, Market, Frequency } from "@/types"

// ── 策略中文名映射 ─────────────────────────────────────────────
const STRATEGY_LABELS: Record<string, string> = {
  double_ma:           "双均线交叉",
  triple_ma:           "三均线顺势",
  macd:                "MACD 信号",
  supertrend:          "Supertrend 趋势",
  adx_trend:           "ADX 趋势过滤",
  bollinger:           "布林带均值回归",
  rsi_mean_reversion:  "RSI 均值回归",
  stochastic:          "随机指标 KD",
  vwap_reversion:      "VWAP 均值回归",
  donchian_breakout:   "唐奇安突破",
  keltner_breakout:    "凯尔特纳突破",
  atr_breakout:        "ATR 波动率突破",
  momentum:            "价格动量",
  multi_factor:        "多因子模型",
  grid_trading:        "网格交易",
  pairs_trading:       "配对套利",
}

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
  idle:    { label: "待机",   dot: "bg-[#30363d]",  text: "text-[#8b949e]" },
  running: { label: "运行中", dot: "bg-[#3fb950]",  text: "text-[#3fb950]" },
  stopped: { label: "已停止", dot: "bg-[#8b949e]",  text: "text-[#8b949e]" },
  error:   { label: "错误",   dot: "bg-[#f85149]",  text: "text-[#f85149]" },
}

// ── 工具 ──────────────────────────────────────────────────────
function pct(v: number) {
  const color = v >= 0 ? "text-[#3fb950]" : "text-[#f85149]"
  return <span className={`font-mono font-bold ${color}`}>{v >= 0 ? "+" : ""}{v.toFixed(2)}%</span>
}

function elapsed(startedAt: string | null): string {
  if (!startedAt) return "—"
  const diff = Date.now() - new Date(startedAt).getTime()
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

// ── 状态徽章 ──────────────────────────────────────────────────
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
interface CardProps {
  inst: LiveStrategyInstance
  onStop: (id: string) => void
  onDelete: (id: string) => void
  onRerun: (values: {
    strategy_name: string; symbol: string; market: Market
    frequency: Frequency; params: Record<string, unknown>; sim_days: number
  }) => void
  isStopping: boolean
}

// ── 当前参数展示 ───────────────────────────────────────────────
function ParamBadges({ stratName, params }: { stratName: string; params: Record<string, unknown> }) {
  const defs = STRATEGY_PARAM_DEFS[stratName] ?? []
  if (defs.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {defs.map((d) => {
        const v = params[d.key] ?? d.default
        return (
          <span key={d.key}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[10px]">
            <span className="text-[#6e7681]">{d.label}</span>
            <span className="font-mono text-[#58a6ff] font-medium">{String(v)}</span>
          </span>
        )
      })}
    </div>
  )
}

// ── 决策建议计算 ───────────────────────────────────────────────
function computeAdvice(paper: PaperSimResult) {
  const { total_return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, profit_factor } = paper
  const dd = Math.abs(max_drawdown_pct)
  const issues: string[] = []
  const goods: string[] = []
  const paramHints: string[] = []

  if (total_return_pct > 0) goods.push(`模拟收益 +${total_return_pct.toFixed(1)}%`)
  if (sharpe_ratio > 1.0)   goods.push(`Sharpe ${sharpe_ratio.toFixed(2)} 优秀`)
  if (win_rate_pct > 50)    goods.push(`胜率 ${win_rate_pct.toFixed(0)}% 良好`)
  if (profit_factor > 1.5)  goods.push(`盈亏比 ${profit_factor.toFixed(2)} 优异`)

  if (dd > 25) {
    issues.push(`最大回撤 ${dd.toFixed(1)}% 偏高（建议 <20%）`)
    paramHints.push("尝试缩小仓位比例，或增大均线周期来减少频繁交易")
  }
  if (sharpe_ratio < 0.5) {
    issues.push(`Sharpe ${sharpe_ratio.toFixed(2)} 偏低（建议 >1.0）`)
    paramHints.push("收益波动过大，可适当放宽入场条件参数（如 RSI 超卖线降至 25）")
  }
  if (total_return_pct < 0) {
    issues.push(`模拟亏损 ${total_return_pct.toFixed(1)}%`)
    paramHints.push("策略方向可能与当前市场不符，可尝试调整快慢线比例或换用均值回归策略")
  }
  if (profit_factor < 1.0 && profit_factor > 0) {
    issues.push(`盈亏比 ${profit_factor.toFixed(2)} < 1（亏大于盈）`)
    paramHints.push("止盈条件过早触发，或止损太宽，可尝试收紧超买线至 65 或扩大超卖线至 35")
  }
  if (paper.total_trades < 3 && paper.sim_days >= 60) {
    issues.push(`模拟期间仅 ${paper.total_trades} 笔交易，信号过少`)
    paramHints.push("参数过于保守，可尝试缩短均线周期或放宽阈值，增加信号频率")
  }

  const action = issues.length === 0 && goods.length >= 2
    ? "proceed"
    : issues.length >= 2
      ? "adjust"
      : "watch"

  const label = action === "proceed"
    ? "建议推进实盘"
    : action === "adjust"
      ? "建议调整参数"
      : "继续观察一段时间"

  const color = action === "proceed" ? "#3fb950" : action === "adjust" ? "#f85149" : "#e3b341"
  return { action, label, color, goods, issues, paramHints }
}

function InstanceCard({ inst, onStop, onDelete, onRerun, isStopping }: CardProps) {
  const [tab, setTab] = useState<"overview" | "trades" | "guide">("overview")
  const [showParams, setShowParams] = useState(false)

  const paper = inst.paper
  const isRunning = inst.state === "running"
  const hasResult = !!paper && paper.total_trades > 0
  const simDaysActual = paper?.sim_days ?? 60

  const advice = paper ? computeAdvice(paper) : null

  return (
    <div className={`rounded-xl border transition-colors ${isRunning ? "border-[#3fb950]/25 bg-[#0d1117]" : "border-[#30363d] bg-[#0d1117]"}`}>
      {/* ── 头部 ── */}
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StateBadge state={inst.state} />
            {paper && (
              <span className="text-[10px] text-[#6e7681]">
                {paper.sim_start} → {paper.sim_end}
                <span className="ml-1 text-[#8b949e]">({simDaysActual}天)</span>
              </span>
            )}
          </div>
          <h3 className="text-base font-bold text-[#e6edf3]">
            {STRATEGY_LABELS[inst.strategy_name] ?? inst.strategy_name}
            <span className="ml-2 text-sm font-normal text-[#8b949e]">{inst.symbol}</span>
          </h3>
          <p className="text-xs text-[#6e7681] mt-0.5">
            {inst.market} · {inst.frequency} · 运行 {elapsed(inst.started_at)}
          </p>
          {/* 参数标签 */}
          <div className="mt-2">
            <button onClick={() => setShowParams((v) => !v)}
              className="text-[10px] text-[#6e7681] hover:text-[#8b949e] flex items-center gap-1">
              <span>{showParams ? "▾" : "▸"}</span>
              <span>参数配置</span>
            </button>
            {showParams && (
              <div className="mt-1.5">
                <ParamBadges stratName={inst.strategy_name} params={inst.params} />
              </div>
            )}
          </div>
        </div>
        <div className="flex gap-2 shrink-0 flex-wrap justify-end">
          {/* 调整重跑：任何状态都可用 */}
          <button
            onClick={() => onRerun({
              strategy_name: inst.strategy_name,
              symbol: inst.symbol,
              market: inst.market as Market,
              frequency: inst.frequency as Frequency,
              params: inst.params,
              sim_days: simDaysActual,
            })}
            className="px-3 py-1.5 rounded text-xs font-medium border border-[#e3b341]/40 text-[#e3b341] bg-[#272111]/50 hover:bg-[#e3b341]/10 transition-colors">
            ⚙ 调整重跑
          </button>
          {isRunning ? (
            <button onClick={() => onStop(inst.instance_id)} disabled={isStopping}
              className="px-3 py-1.5 rounded text-xs font-medium bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/30 hover:bg-[#f85149]/10 disabled:opacity-50 transition-colors">
              {isStopping ? <Spinner size="sm" className="inline-block" /> : "停止"}
            </button>
          ) : (
            <button onClick={() => onDelete(inst.instance_id)}
              className="px-3 py-1.5 rounded text-xs text-[#6e7681] border border-[#30363d] hover:text-[#f85149] transition-colors">
              删除
            </button>
          )}
        </div>
      </div>

      {/* ── 快速指标行 ── */}
      {paper && (
        <div className="grid grid-cols-4 gap-0 border-t border-[#21262d]">
          {[
            { label: "模拟收益", value: pct(paper.total_return_pct) },
            { label: "Sharpe", value: <span className="font-mono font-bold text-[#e6edf3]">{paper.sharpe_ratio.toFixed(2)}</span> },
            { label: "最大回撤", value: pct(paper.max_drawdown_pct) },
            { label: "胜率", value: <span className="font-mono font-bold text-[#e6edf3]">{paper.win_rate_pct.toFixed(0)}%</span> },
          ].map((m) => (
            <div key={m.label} className="flex flex-col items-center py-3 border-r border-[#21262d] last:border-r-0">
              <p className="text-[9px] text-[#6e7681] mb-1">{m.label}</p>
              <div className="text-sm">{m.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── Tab 栏 ── */}
      <div className="flex border-t border-[#21262d]">
        {(["overview", "trades", "guide"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`flex-1 py-2 text-xs font-medium transition-colors ${
              tab === t ? "text-[#58a6ff] border-b-2 border-[#58a6ff]" : "text-[#6e7681] hover:text-[#8b949e]"
            }`}>
            {{ overview: "📊 模拟概览", trades: "📋 成交记录", guide: "🧭 后续操作" }[t]}
          </button>
        ))}
      </div>

      {/* ── 内容区 ── */}
      <div className="p-4">
        {/* ── 模拟概览 ── */}
        {tab === "overview" && (
          <div className="space-y-4">
            {!paper ? (
              <div className="text-center py-8 text-[#6e7681] text-xs">
                <p className="text-2xl mb-2">📡</p>
                <p>正在加载历史数据进行模拟…</p>
                <p className="mt-1 text-[10px]">首次启动需要拉取历史 K 线，请稍候</p>
              </div>
            ) : !hasResult ? (
              <div className="py-6 space-y-3">
                <div className="text-center text-[#6e7681] text-xs">
                  <p className="text-2xl mb-2">💤</p>
                  <p className="font-medium text-[#e6edf3]">
                    模拟期间无交易信号（{paper.sim_start} → {paper.sim_end}，{simDaysActual} 天）
                  </p>
                  <p className="mt-1 text-[10px]">策略入场条件在此窗口内未被触发</p>
                </div>
                <div className="bg-[#161b22] rounded-lg p-3 space-y-2 text-xs">
                  <p className="text-[#8b949e] font-medium">💡 常见调整方向：</p>
                  <ul className="space-y-1 text-[#6e7681]">
                    <li>▸ <span className="text-[#e6edf3]">缩短均线周期</span>（如快线从 10 → 7），提高信号灵敏度</li>
                    <li>▸ <span className="text-[#e6edf3]">放宽阈值</span>（RSI 超卖线从 30 → 35），降低入场门槛</li>
                    <li>▸ <span className="text-[#e6edf3]">延长模拟天数</span>（从 {simDaysActual} → {Math.min(simDaysActual + 60, 180)} 天），覆盖更多市场周期</li>
                  </ul>
                  <button
                    onClick={() => onRerun({
                      strategy_name: inst.strategy_name,
                      symbol: inst.symbol,
                      market: inst.market as Market,
                      frequency: inst.frequency as Frequency,
                      params: inst.params,
                      sim_days: Math.min(simDaysActual + 60, 180),
                    })}
                    className="mt-1 px-3 py-1.5 rounded text-xs border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                    ↗ 延长至 {Math.min(simDaysActual + 60, 180)} 天重试
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* 净值曲线 */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs font-semibold text-[#e6edf3]">
                      净值曲线（{simDaysActual} 天模拟）
                    </p>
                  </div>
                  <div className="h-40">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={paper.equity_curve} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <defs>
                          <linearGradient id={`grad-${inst.instance_id}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor="#58a6ff" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="#58a6ff" stopOpacity={0}   />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="2 4" stroke="#21262d" />
                        <XAxis dataKey="time" tick={{ fontSize: 9, fill: "#6e7681" }}
                          tickFormatter={(v: string) => v.slice(5)} interval="preserveStartEnd" />
                        <YAxis tick={{ fontSize: 9, fill: "#6e7681" }} width={60}
                          tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`} />
                        <ReTooltip
                          contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
                          formatter={(v: number) => [`$${v.toLocaleString()}`, "净值"]}
                          labelStyle={{ color: "#8b949e" }}
                        />
                        <ReferenceLine y={paper.initial_cash} stroke="#30363d" strokeDasharray="3 3" />
                        <Area type="monotone" dataKey="value"
                          stroke="#58a6ff" strokeWidth={1.5}
                          fill={`url(#grad-${inst.instance_id})`} dot={false} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* 指标网格 */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[
                    { label: "模拟总收益", value: `${paper.total_return_pct >= 0 ? "+" : ""}${paper.total_return_pct.toFixed(2)}%`, color: paper.total_return_pct >= 0 ? "#3fb950" : "#f85149" },
                    { label: "买入持有", value: `${paper.buy_hold_return_pct >= 0 ? "+" : ""}${paper.buy_hold_return_pct.toFixed(2)}%`, color: paper.buy_hold_return_pct >= 0 ? "#3fb950" : "#f85149" },
                    { label: "Sharpe 比率", value: paper.sharpe_ratio.toFixed(3), color: paper.sharpe_ratio >= 1 ? "#3fb950" : paper.sharpe_ratio >= 0.5 ? "#e3b341" : "#f85149" },
                    { label: "最大回撤", value: `${paper.max_drawdown_pct.toFixed(2)}%`, color: Math.abs(paper.max_drawdown_pct) < 15 ? "#3fb950" : Math.abs(paper.max_drawdown_pct) < 25 ? "#e3b341" : "#f85149" },
                    { label: "胜率", value: `${paper.win_rate_pct.toFixed(1)}%`, color: paper.win_rate_pct >= 50 ? "#3fb950" : "#e3b341" },
                    { label: "盈亏比", value: paper.profit_factor.toFixed(2), color: paper.profit_factor >= 1.5 ? "#3fb950" : paper.profit_factor >= 1 ? "#e3b341" : "#f85149" },
                    { label: "成交笔数", value: `${paper.total_trades} 笔`, color: "#e6edf3" },
                    { label: "当前持仓", value: paper.position > 0 ? `${paper.position} 股 @ $${paper.avg_cost.toFixed(2)}` : "空仓", color: paper.position > 0 ? "#58a6ff" : "#8b949e" },
                  ].map((m) => (
                    <div key={m.label} className="bg-[#161b22] rounded-lg p-3">
                      <p className="text-[10px] text-[#6e7681] mb-1">{m.label}</p>
                      <p className="text-sm font-mono font-bold" style={{ color: m.color }}>{m.value}</p>
                    </div>
                  ))}
                </div>

                {/* 说明 */}
                <div className="bg-[#161b22] rounded-lg p-3 text-[10px] text-[#6e7681] space-y-1">
                  <p className="font-semibold text-[#8b949e]">📖 模拟说明</p>
                  <p>
                    以 ${paper.initial_cash.toLocaleString()} 初始资金，在
                    {paper.sim_start} → {paper.sim_end}（{simDaysActual} 天）历史数据上
                    运行策略，模拟真实买卖，不实际动用资金。
                  </p>
                  <p>⚠ Sharpe &gt; 1、最大回撤 &lt; 20%、收益超过买入持有为参考合格线。</p>
                </div>
              </>
            )}
          </div>
        )}

        {/* ── 成交记录 ── */}
        {tab === "trades" && (
          <div>
            {!paper || paper.trades.length === 0 ? (
              <div className="text-center py-8 text-[#6e7681] text-xs">
                <p>模拟期间无交易记录</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[#6e7681] border-b border-[#21262d]">
                      <th className="text-left pb-2 pr-3">时间</th>
                      <th className="text-left pb-2 pr-3">方向</th>
                      <th className="text-right pb-2 pr-3">价格</th>
                      <th className="text-right pb-2 pr-3">数量</th>
                      <th className="text-right pb-2 pr-3">金额</th>
                      <th className="text-right pb-2">实现盈亏</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#21262d]">
                    {paper.trades.map((t, i) => (
                      <tr key={i} className="hover:bg-[#161b22] transition-colors">
                        <td className="py-2 pr-3 text-[#8b949e] font-mono">{t.timestamp}</td>
                        <td className="py-2 pr-3">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.side === "BUY" ? "bg-[#3fb950]/15 text-[#3fb950]" : "bg-[#f85149]/15 text-[#f85149]"
                          }`}>
                            {t.side === "BUY" ? "买入" : "卖出"}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">${t.price.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{t.qty}</td>
                        <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">${t.value.toLocaleString()}</td>
                        <td className={`py-2 text-right font-mono font-bold ${
                          t.realized_pnl > 0 ? "text-[#3fb950]" : t.realized_pnl < 0 ? "text-[#f85149]" : "text-[#8b949e]"
                        }`}>
                          {t.side === "BUY" ? "—" : `${t.realized_pnl >= 0 ? "+" : ""}$${t.realized_pnl.toFixed(2)}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  {paper.trades.length > 0 && (
                    <tfoot>
                      <tr className="border-t border-[#30363d]">
                        <td colSpan={5} className="pt-2 text-[#6e7681]">合计实现盈亏</td>
                        <td className={`pt-2 text-right font-mono font-bold ${
                          paper.trades.reduce((s, t) => s + t.realized_pnl, 0) >= 0 ? "text-[#3fb950]" : "text-[#f85149]"
                        }`}>
                          {(() => {
                            const total = paper.trades.reduce((s, t) => s + t.realized_pnl, 0)
                            return `${total >= 0 ? "+" : ""}$${total.toFixed(2)}`
                          })()}
                        </td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── 后续操作建议 ── */}
        {tab === "guide" && (
          <div className="space-y-4">
            {/* 决策卡 */}
            {advice && (
              <div className={`rounded-lg p-4 border`}
                style={{ borderColor: `${advice.color}40`, background: `${advice.color}10` }}>
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-xl">
                    {advice.action === "proceed" ? "✅" : advice.action === "adjust" ? "❌" : "⏳"}
                  </span>
                  <p className="font-bold text-sm" style={{ color: advice.color }}>{advice.label}</p>
                </div>

                {advice.goods.length > 0 && (
                  <div className="mb-2">
                    <p className="text-[10px] text-[#6e7681] mb-1">✓ 积极指标</p>
                    {advice.goods.map((g, i) => (
                      <p key={i} className="text-xs text-[#3fb950]">▸ {g}</p>
                    ))}
                  </div>
                )}
                {advice.issues.length > 0 && (
                  <div>
                    <p className="text-[10px] text-[#6e7681] mb-1">✗ 待解决问题</p>
                    {advice.issues.map((s, i) => (
                      <p key={i} className="text-xs text-[#f85149]">▸ {s}</p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 操作步骤 */}
            <div className="space-y-2">
              <p className="text-xs font-semibold text-[#8b949e]">下一步操作路径</p>

              {/* Step 1: 参数调整 or 延长 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: `${advice?.color ?? "#58a6ff"}20`, color: advice?.color ?? "#58a6ff" }}>
                  1
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">
                    {advice?.action === "proceed" ? "✅ 延长模拟天数确认稳定性" : "⚙️ 调整参数或延长模拟天数"}
                  </p>
                  {advice?.action === "proceed" ? (
                    <p className="text-[10px] text-[#6e7681] leading-relaxed mb-2">
                      当前 {simDaysActual} 天结果良好，延长至更长周期验证稳定性
                    </p>
                  ) : (
                    <>
                      <p className="text-[10px] text-[#6e7681] leading-relaxed mb-2">
                        {advice?.paramHints[0] ?? "点击调整重跑，修改参数后重新模拟，直到指标达标"}
                      </p>
                      {advice && advice.paramHints.length > 1 && (
                        <ul className="space-y-0.5 mb-2">
                          {advice.paramHints.slice(1).map((h, i) => (
                            <li key={i} className="text-[9px] text-[#6e7681]">▸ {h}</li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                  {/* 快捷操作按钮 */}
                  <div className="flex flex-wrap gap-2">
                    {/* 延长天数快捷按钮 */}
                    {simDaysActual < 90 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 90 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 90 天
                      </button>
                    )}
                    {simDaysActual < 120 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 120 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 120 天
                      </button>
                    )}
                    {simDaysActual < 180 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 180 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 180 天
                      </button>
                    )}
                    <button
                      onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: simDaysActual })}
                      className="px-2 py-1 rounded text-[10px] border border-[#e3b341]/40 text-[#e3b341] bg-[#272111]/40 hover:bg-[#e3b341]/10 transition-colors">
                      ⚙ 调整参数
                    </button>
                  </div>
                </div>
              </div>

              {/* Step 2: 基准对比 */}
              {paper && (
                <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                    style={{ background: "#58a6ff20", color: "#58a6ff" }}>2</div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">📊 对比买入持有基准</p>
                    <div className="flex items-center gap-3 text-xs mt-1">
                      <span className="text-[#6e7681]">策略收益</span>
                      <span className={`font-mono font-bold ${paper.total_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {paper.total_return_pct >= 0 ? "+" : ""}{paper.total_return_pct.toFixed(2)}%
                      </span>
                      <span className="text-[#6e7681]">vs 买入持有</span>
                      <span className={`font-mono font-bold ${paper.buy_hold_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {paper.buy_hold_return_pct >= 0 ? "+" : ""}{paper.buy_hold_return_pct.toFixed(2)}%
                      </span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${paper.total_return_pct > paper.buy_hold_return_pct ? "bg-[#3fb950]/15 text-[#3fb950]" : "bg-[#f85149]/15 text-[#f85149]"}`}>
                        {paper.total_return_pct > paper.buy_hold_return_pct ? "✓ 跑赢基准" : "✗ 未跑赢"}
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {/* Step 3: 风控 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: "#e3b34120", color: "#e3b341" }}>3</div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">⚙️ 配置风控规则</p>
                  <p className="text-[10px] text-[#6e7681]">设置最大仓位比例、日亏损上限，保护本金安全</p>
                  <a href="/settings"
                    className="inline-block mt-1.5 text-[10px] px-2 py-0.5 rounded border border-[#30363d] text-[#58a6ff] hover:bg-[#21262d] transition-colors">
                    去设置 →
                  </a>
                </div>
              </div>

              {/* Step 4: 是否进入实盘 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: advice?.action === "proceed" ? "#3fb95020" : "#6e768120", color: advice?.action === "proceed" ? "#3fb950" : "#6e7681" }}>4</div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">
                    {advice?.action === "proceed" ? "🚀 可考虑开启实盘（谨慎）" : "🚫 暂缓实盘"}
                  </p>
                  <p className="text-[10px] text-[#6e7681]">
                    {advice?.action === "proceed"
                      ? "Sharpe > 1、最大回撤 < 20% 且跑赢基准，满足基础门槛。建议先小仓位（<5%）试水。"
                      : "指标尚未达标，继续在模拟盘中优化，或尝试不同策略组合后再评估。"}
                  </p>
                </div>
              </div>
            </div>

            {/* 实盘说明 */}
            <div className="bg-[#2a1a00] border border-[#e3b341]/30 rounded-lg p-3 text-[10px] text-[#e3b341] space-y-1">
              <p className="font-semibold">⚠ 实盘风险提示</p>
              <p>模拟结果基于历史数据，不保证未来表现。实盘交易需承担真实市场风险，请确保资金可承受全部亏损。
                建议实盘资金不超过模拟期间最大回撤对应的可承受额度。</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── 策略参数定义表 ────────────────────────────────────────────
interface ParamDef {
  key: string
  label: string
  type: "int" | "float" | "select"
  default: number | string
  min?: number
  max?: number
  step?: number
  options?: { value: string; label: string }[]
  hint?: string
}

const STRATEGY_PARAM_DEFS: Record<string, ParamDef[]> = {
  double_ma: [
    { key: "fast_period", label: "快线周期", type: "int",    default: 10,   min: 3,  max: 60,  hint: "短期均线，越小越灵敏" },
    { key: "slow_period", label: "慢线周期", type: "int",    default: 30,   min: 10, max: 200, hint: "长期均线，需 > 快线" },
    { key: "ma_type",     label: "均线类型", type: "select", default: "sma", options: [{ value: "sma", label: "SMA 简单均线" }, { value: "ema", label: "EMA 指数均线" }] },
  ],
  triple_ma: [
    { key: "fast_period", label: "快线周期", type: "int", default: 5,  min: 2,  max: 30  },
    { key: "mid_period",  label: "中线周期", type: "int", default: 15, min: 5,  max: 60  },
    { key: "slow_period", label: "慢线周期", type: "int", default: 30, min: 10, max: 200 },
    { key: "ma_type",     label: "均线类型", type: "select", default: "sma", options: [{ value: "sma", label: "SMA" }, { value: "ema", label: "EMA" }] },
  ],
  macd: [
    { key: "fast",   label: "快线 EMA", type: "int", default: 12, min: 3,  max: 50  },
    { key: "slow",   label: "慢线 EMA", type: "int", default: 26, min: 10, max: 100 },
    { key: "signal", label: "信号线",   type: "int", default: 9,  min: 3,  max: 30  },
  ],
  supertrend: [
    { key: "period",     label: "ATR 周期",   type: "int",   default: 10,  min: 5,  max: 50               },
    { key: "multiplier", label: "ATR 倍数",   type: "float", default: 3.0, min: 1.0, max: 6.0, step: 0.5 },
  ],
  adx_trend: [
    { key: "adx_period",    label: "ADX 周期",   type: "int", default: 14, min: 5, max: 50              },
    { key: "adx_threshold", label: "ADX 阈值",   type: "int", default: 25, min: 15, max: 40, hint: "高于此值才交易" },
    { key: "fast_period",   label: "快线周期",   type: "int", default: 10, min: 3,  max: 50              },
    { key: "slow_period",   label: "慢线周期",   type: "int", default: 30, min: 10, max: 200             },
  ],
  bollinger: [
    { key: "period",  label: "布林周期", type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "std_dev", label: "标准差倍数", type: "float", default: 2.0, min: 1.0, max: 4.0, step: 0.5 },
  ],
  rsi_mean_reversion: [
    { key: "period",     label: "RSI 周期", type: "int", default: 14, min: 5,  max: 50             },
    { key: "oversold",   label: "超卖线",   type: "int", default: 30, min: 10, max: 45, hint: "低于此值买入" },
    { key: "overbought", label: "超买线",   type: "int", default: 70, min: 55, max: 90, hint: "高于此值卖出" },
  ],
  stochastic: [
    { key: "k_period",   label: "K 周期",  type: "int", default: 14, min: 3,  max: 50 },
    { key: "d_period",   label: "D 周期",  type: "int", default: 3,  min: 1,  max: 10 },
    { key: "oversold",   label: "超卖线",  type: "int", default: 20, min: 5,  max: 35 },
    { key: "overbought", label: "超买线",  type: "int", default: 80, min: 65, max: 95 },
  ],
  vwap_reversion: [
    { key: "period",    label: "VWAP 周期", type: "int",   default: 20,   min: 5,  max: 60  },
    { key: "threshold", label: "偏离阈值",  type: "float", default: 0.02, min: 0.005, max: 0.1, step: 0.005, hint: "偏离 VWAP 多少触发" },
  ],
  donchian_breakout: [
    { key: "period", label: "唐奇安通道周期", type: "int", default: 20, min: 5, max: 100 },
  ],
  keltner_breakout: [
    { key: "ema_period",  label: "EMA 周期",  type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "atr_period",  label: "ATR 周期",  type: "int",   default: 10,  min: 3,  max: 50              },
    { key: "multiplier",  label: "ATR 倍数",  type: "float", default: 2.0, min: 0.5, max: 5.0, step: 0.5 },
  ],
  atr_breakout: [
    { key: "channel_period", label: "通道周期", type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "atr_period",     label: "ATR 周期", type: "int",   default: 14,  min: 3,  max: 50              },
    { key: "multiplier",     label: "ATR 倍数", type: "float", default: 0.5, min: 0.1, max: 3.0, step: 0.1 },
  ],
  momentum: [
    { key: "lookback",  label: "动量周期", type: "int",   default: 20,   min: 5,  max: 100             },
    { key: "threshold", label: "动量阈值", type: "float", default: 0.03, min: 0.005, max: 0.2, step: 0.005 },
  ],
}

const DEFAULT_PARAMS: Record<string, Record<string, number | string>> = Object.fromEntries(
  Object.entries(STRATEGY_PARAM_DEFS).map(([k, defs]) => [
    k,
    Object.fromEntries(defs.map((d) => [d.key, d.default])),
  ])
)

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

function LaunchForm({ strategies, onClose, initialValues }: LaunchFormProps) {
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

// ── 主页面 ────────────────────────────────────────────────────
type RerunValues = {
  strategy_name: string; symbol: string; market: Market
  frequency: Frequency; params: Record<string, unknown>; sim_days: number
}

export function LiveStrategy() {
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: instances, isLoading } = useLiveStrategies()
  const { data: strategies } = useStrategies()
  const { mutate: stopStrategy, isPending: isStopping, variables: stoppingId } = useStopStrategy()
  const { mutate: deleteInstance } = useDeleteStrategyInstance()
  const [showForm, setShowForm] = useState(false)
  const [rerunValues, setRerunValues] = useState<RerunValues | undefined>()

  const running = (instances ?? []).filter((i) => i.state === "running").length

  // 从回测页面携带参数进入时自动打开表单
  useEffect(() => {
    const strategy = searchParams.get("strategy")
    if (!strategy) return
    let params: Record<string, unknown> = {}
    try { params = JSON.parse(searchParams.get("params") ?? "{}") } catch {}
    setRerunValues({
      strategy_name: strategy,
      symbol:   searchParams.get("symbol")   ?? "AAPL",
      market:   (searchParams.get("market")  ?? "US") as Market,
      frequency:(searchParams.get("freq")    ?? "1d") as Frequency,
      params,
      sim_days: 60,
    })
    setShowForm(true)
    // 清空 URL 参数，避免刷新重复弹出
    setSearchParams({})
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  function handleRerun(values: RerunValues) {
    setRerunValues(values)
    setShowForm(true)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  function handleCloseForm() {
    setShowForm(false)
    setRerunValues(undefined)
  }

  return (
    <AppShell title="策略自动交易" help={PAGE_HELP["live-strategy"]}>

      {/* 功能说明横幅 */}
      <div className="flex items-start gap-3 mb-5 px-4 py-3 rounded-xl border border-[#58a6ff]/20 bg-[#0d1421]">
        <span className="text-xl mt-0.5">🤖</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[#e6edf3] mb-1">量化策略自动执行</p>
          <p className="text-xs text-[#8b949e] leading-relaxed">
            在此选择策略、标的和参数，系统将在历史数据上运行<strong className="text-[#e6edf3]">模拟盘</strong>（回放真实行情，本地纸面撮合），
            看到净值曲线和成交记录。策略满意后，可在
            <Link to="/orders" className="text-[#58a6ff] underline mx-1">订单中心</Link>
            手动下单，或等待 Alpaca 实盘接入功能上线。
          </p>
        </div>
        <div className="shrink-0 flex gap-2">
          <Link to="/orders"
            className="px-3 py-1.5 rounded text-xs border border-[#3fb950]/30 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors whitespace-nowrap">
            📋 手动下单
          </Link>
        </div>
      </div>

      {/* 头部操作行 */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">策略实例</h2>
          <p className="text-xs text-[#6e7681] mt-0.5">
            {running > 0 ? `${running} 个运行中 · ` : ""}
            可自由调整参数和模拟天数进行多次对比
          </p>
        </div>
        <button
          onClick={() => { setRerunValues(undefined); setShowForm(!showForm) }}
          className="btn btn-primary text-xs px-4">
          {showForm && !rerunValues ? "取消" : "+ 新建模拟"}
        </button>
      </div>

      {showForm && strategies && (
        <div className="mb-6">
          <LaunchForm
            strategies={strategies}
            onClose={handleCloseForm}
            initialValues={rerunValues}
          />
        </div>
      )}

      {isLoading && <div className="flex justify-center py-20"><Spinner size="lg" /></div>}

      {!isLoading && (!instances || instances.length === 0) && (
        <EmptyState
          title="尚无模拟盘"
          description='点击「+ 新建模拟」，选择策略和标的，系统会立即生成模拟结果，包含净值曲线、成交记录和操作建议。模拟结束后可点击「⚙ 调整重跑」修改参数或延长天数进行对比。'
        />
      )}

      {instances && instances.length > 0 && (
        <div className="space-y-4">
          {[...instances]
            .sort((a, b) => (a.state === "running" ? -1 : 1) - (b.state === "running" ? -1 : 1))
            .map((inst) => (
              <InstanceCard
                key={inst.instance_id}
                inst={inst}
                onRerun={handleRerun}
                onStop={(id) => stopStrategy(id, {
                  onSuccess: () => toast("已停止", "success"),
                  onError: (e) => toast(e.message, "error"),
                })}
                onDelete={(id) => deleteInstance(id, {
                  onSuccess: () => toast("已删除", "success"),
                  onError: (e) => toast(e.message, "error"),
                })}
                isStopping={isStopping && stoppingId === inst.instance_id}
              />
            ))}
        </div>
      )}
    </AppShell>
  )
}
