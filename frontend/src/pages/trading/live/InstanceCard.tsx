// 策略实例卡：头部（状态/参数/操作）+ 快速指标行 + 三个内容页（概览/成交/后续操作）。
import { useState } from "react"
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as ReTooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { Spinner } from "@/components/ui/Spinner"
import type { LiveStrategyInstance, Market, Frequency } from "@/types"
import { computeAdvice } from "./advice"
import { InstanceGuide } from "./InstanceGuide"
import { InstanceTrades } from "./InstanceTrades"
import { STRATEGY_LABELS, StateBadge, elapsed, pct, type RerunValues } from "./shared"
import { STRATEGY_PARAM_DEFS } from "./strategyParams"

interface CardProps {
  inst: LiveStrategyInstance
  onStop: (id: string) => void
  onDelete: (id: string) => void
  onRerun: (values: RerunValues) => void
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


        {tab === "trades" && <InstanceTrades paper={paper} />}

        {tab === "guide" && (
          <InstanceGuide
            inst={inst}
            paper={paper}
            advice={advice}
            simDaysActual={simDaysActual}
            onRerun={onRerun}
          />
        )}
      </div>
    </div>
  )
}

export { InstanceCard }
