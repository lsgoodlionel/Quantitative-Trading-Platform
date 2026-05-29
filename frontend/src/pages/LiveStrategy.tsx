import { useState } from "react"
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
import type { LiveStrategyInstance, LiveStrategyState, Market, Frequency } from "@/types"

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
  isStopping: boolean
}

function InstanceCard({ inst, onStop, onDelete, isStopping }: CardProps) {
  const [tab, setTab] = useState<"overview" | "trades" | "guide">("overview")
  const paper = (inst as any).paper as null | {
    total_return_pct: number
    sharpe_ratio: number
    max_drawdown_pct: number
    win_rate_pct: number
    profit_factor: number
    total_trades: number
    buy_hold_return_pct: number
    initial_cash: number
    cash: number
    position: number
    avg_cost: number
    sim_start: string
    sim_end: string
    equity_curve: Array<{ time: string; value: number; pnl_pct: number }>
    trades: Array<{
      timestamp: string; side: string; price: number; qty: number;
      value: number; realized_pnl: number; signal_reason: string
    }>
  }

  const isRunning = inst.state === "running"
  const hasResult = !!paper && paper.total_trades > 0

  // 决策建议
  const advice = (() => {
    if (!paper) return null
    const { total_return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, profit_factor } = paper
    const dd = Math.abs(max_drawdown_pct)
    const issues: string[] = []
    const goods: string[] = []
    if (total_return_pct > 0) goods.push(`模拟盘盈利 +${total_return_pct.toFixed(1)}%`)
    if (sharpe_ratio > 1.0)   goods.push(`Sharpe ${sharpe_ratio.toFixed(2)} 优秀`)
    if (win_rate_pct > 50)    goods.push(`胜率 ${win_rate_pct.toFixed(0)}% 良好`)
    if (dd > 25)              issues.push(`最大回撤 ${dd.toFixed(1)}% 偏高`)
    if (sharpe_ratio < 0.5)   issues.push(`Sharpe ${sharpe_ratio.toFixed(2)} 偏低`)
    if (total_return_pct < 0) issues.push(`模拟亏损 ${total_return_pct.toFixed(1)}%`)
    if (profit_factor < 1.0)  issues.push(`盈亏比 ${profit_factor.toFixed(2)} < 1`)

    if (issues.length === 0 && goods.length >= 2) {
      return { action: "proceed", label: "建议推进实盘", color: "#3fb950", goods, issues }
    } else if (issues.length >= 2) {
      return { action: "adjust", label: "建议重新调参", color: "#f85149", goods, issues }
    } else {
      return { action: "watch", label: "继续观察一段时间", color: "#e3b341", goods, issues }
    }
  })()

  return (
    <div className={`rounded-xl border transition-colors ${isRunning ? "border-[#3fb950]/25 bg-[#0d1117]" : "border-[#30363d] bg-[#0d1117]"}`}>
      {/* ── 头部 ── */}
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StateBadge state={inst.state} />
            <span className="text-[10px] text-[#6e7681] font-mono truncate hidden sm:block">
              {inst.instance_id}
            </span>
          </div>
          <h3 className="text-base font-bold text-[#e6edf3]">
            {STRATEGY_LABELS[inst.strategy_name] ?? inst.strategy_name}
          </h3>
          <p className="text-xs text-[#8b949e] mt-0.5">
            {inst.symbol} &nbsp;·&nbsp; {inst.market} &nbsp;·&nbsp; {inst.frequency}
            &nbsp;·&nbsp; 运行 {elapsed(inst.started_at)}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
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
                <p className="mt-1 text-[10px]">首次启动需要拉取最近 60 天 K 线，请稍候</p>
              </div>
            ) : !hasResult ? (
              <div className="text-center py-8 text-[#6e7681] text-xs">
                <p className="text-2xl mb-2">💤</p>
                <p>策略在模拟期间未发出任何交易信号</p>
                <p className="mt-1 text-[10px]">
                  可能原因：市场条件不符合策略入场条件，建议调整参数或更换策略
                </p>
              </div>
            ) : (
              <>
                {/* 净值曲线 */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs font-semibold text-[#e6edf3]">净值曲线（最近 60 天模拟）</p>
                    <p className="text-[10px] text-[#6e7681]">
                      {paper.sim_start} → {paper.sim_end}
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
                  <p className="font-semibold text-[#8b949e]">📖 什么是模拟盘？</p>
                  <p>模拟盘使用真实历史 K 线数据，以 ${paper.initial_cash.toLocaleString()} 初始资金运行策略，
                    模拟真实买卖而不实际动用资金。结果反映策略在过去 60 天的表现。</p>
                  <p>⚠ 历史表现不代表未来收益。Sharpe &gt; 1、最大回撤 &lt; 20% 且收益超过买入持有为参考合格线。</p>
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
              {[
                {
                  step: "1",
                  title: advice?.action === "proceed" ? "✅ 延长模拟时间确认稳定性" : "🔄 回到回测调整参数",
                  desc: advice?.action === "proceed"
                    ? "继续让模拟盘运行 1-2 周，观察不同市场条件下表现是否稳定"
                    : "返回「智能交易引导」，使用调参建议重新回测，直到指标达标",
                  link: advice?.action === "proceed" ? null : "/",
                  linkText: "去智能引导",
                  color: advice?.action === "proceed" ? "#3fb950" : "#f85149",
                },
                {
                  step: "2",
                  title: "📊 对比买入持有基准",
                  desc: `策略收益 ${paper ? (paper.total_return_pct >= 0 ? "+" : "") + paper.total_return_pct.toFixed(2) + "%" : "—"} vs 买入持有 ${paper ? (paper.buy_hold_return_pct >= 0 ? "+" : "") + paper.buy_hold_return_pct.toFixed(2) + "%" : "—"}。策略应跑赢基准才有部署价值`,
                  color: "#58a6ff",
                },
                {
                  step: "3",
                  title: "⚙️ 在设置中配置风控",
                  desc: "进入「设置 → 风控」设置最大仓位比例、日亏损上限，保护本金安全",
                  link: "/settings",
                  linkText: "去设置",
                  color: "#e3b341",
                },
                {
                  step: advice?.action === "proceed" ? "4 ✓" : "4",
                  title: advice?.action === "proceed" ? "🚀 准备开启实盘（谨慎）" : "🚫 暂缓实盘",
                  desc: advice?.action === "proceed"
                    ? "模拟盘表现达标后，可在「订单」页查看真实下单情况，或联系券商配置实盘接入"
                    : "当前指标未达合格线，不建议直接转实盘。继续优化策略参数或更换策略",
                  color: advice?.action === "proceed" ? "#3fb950" : "#8b949e",
                },
              ].map((item) => (
                <div key={item.step} className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                    style={{ background: `${item.color}20`, color: item.color }}>
                    {item.step}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">{item.title}</p>
                    <p className="text-[10px] text-[#6e7681] leading-relaxed">{item.desc}</p>
                    {item.link && (
                      <a href={item.link}
                        className="inline-block mt-1.5 text-[10px] px-2 py-0.5 rounded border border-[#30363d] text-[#58a6ff] hover:bg-[#21262d] transition-colors">
                        {item.linkText} →
                      </a>
                    )}
                  </div>
                </div>
              ))}
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

// ── 启动表单 ──────────────────────────────────────────────────
function LaunchForm({ strategies, onClose }: { strategies: { name: string; description: string }[]; onClose: () => void }) {
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
    try { const p = JSON.parse(raw); setParamsError(""); return p }
    catch { setParamsError("JSON 格式错误"); return null }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const params = validateParams(paramsJson)
    if (!params || !symbol.trim()) { toast("请填写标的代码", "warning"); return }
    startStrategy(
      { strategy_name: stratName, symbol: symbol.trim().toUpperCase(), market, frequency, params, warmup_days: parseInt(warmupDays) || 120 },
      {
        onSuccess: (inst) => { toast(`策略启动成功：${inst.instance_id.slice(0, 24)}…`, "success"); onClose() },
        onError: (e) => toast(e.message, "error"),
      }
    )
  }

  return (
    <form onSubmit={handleSubmit} className="bg-[#161b22] border border-[#58a6ff]/20 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#e6edf3]">启动模拟盘</h3>
        <button type="button" onClick={onClose} className="text-[#6e7681] hover:text-[#e6edf3] text-xl leading-none">×</button>
      </div>
      <div>
        <label className="block text-[10px] text-[#6e7681] mb-1.5">策略</label>
        <select className="select w-full" value={stratName} onChange={(e) => setStratName(e.target.value)}>
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>{STRATEGY_LABELS[s.name] ?? s.name}</option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">标的代码</label>
          <input className="input w-full font-mono uppercase" value={symbol}
            onChange={(e) => setSymbol(e.target.value)} placeholder="AAPL" />
        </div>
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">市场</label>
          <div className="flex gap-1">
            {MARKETS.map((m) => (
              <button key={m.value} type="button" onClick={() => setMarket(m.value)}
                className={`flex-1 py-1.5 rounded text-xs border transition-colors ${
                  market === m.value ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30" : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>{m.value}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">K 线周期</label>
          <select className="select w-full" value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
            {FREQS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] text-[#6e7681] mb-1.5">预热天数</label>
          <select className="select w-full" value={warmupDays} onChange={(e) => setWarmupDays(e.target.value)}>
            {["60", "120", "252", "365"].map((d) => <option key={d} value={d}>{d} 天</option>)}
          </select>
        </div>
      </div>
      <div>
        <label className="block text-[10px] text-[#6e7681] mb-1.5">策略参数 (JSON，留空使用默认)</label>
        <textarea className={`input w-full h-16 font-mono text-xs resize-none ${paramsError ? "border-[#f85149]" : ""}`}
          value={paramsJson} onChange={(e) => { setParamsJson(e.target.value); validateParams(e.target.value) }}
          placeholder='{"short_window": 10, "long_window": 30}' />
        {paramsError && <p className="text-[#f85149] text-[10px] mt-1">{paramsError}</p>}
      </div>
      <div className="bg-[#0d1117] rounded-lg p-3 text-[10px] text-[#6e7681]">
        💡 启动后系统将自动在最近 60 天历史数据上运行模拟，几秒内可看到净值曲线和交易记录
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={onClose} className="flex-1 btn border border-[#30363d] text-[#8b949e]">取消</button>
        <button type="submit" disabled={isPending} className="flex-1 btn btn-primary">
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 启动模拟盘"}
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

  return (
    <AppShell title="实盘 / 模拟盘" help={PAGE_HELP["live-strategy"]}>
      {/* 头部操作行 */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">策略实例</h2>
          <p className="text-xs text-[#6e7681] mt-0.5">
            {running > 0 ? `${running} 个运行中 · ` : ""}
            启动后自动生成最近 60 天模拟报告
          </p>
        </div>
        <button onClick={() => setShowForm(!showForm)} className="btn btn-primary text-xs px-4">
          {showForm ? "取消" : "+ 启动模拟盘"}
        </button>
      </div>

      {showForm && strategies && (
        <div className="mb-6">
          <LaunchForm strategies={strategies} onClose={() => setShowForm(false)} />
        </div>
      )}

      {isLoading && <div className="flex justify-center py-20"><Spinner size="lg" /></div>}

      {!isLoading && (!instances || instances.length === 0) && (
        <EmptyState
          title="尚无模拟盘"
          description='点击「+ 启动模拟盘」，选择策略和标的，系统会立即生成最近 60 天的模拟结果，包含净值曲线、成交记录和操作建议'
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
