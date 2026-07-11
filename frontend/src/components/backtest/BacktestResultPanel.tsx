import { useState, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { EquityCurve } from "@/components/charts/EquityCurve"
import { DrawdownChart } from "@/components/charts/DrawdownChart"
import { MonthlyHeatmap } from "@/components/charts/MonthlyHeatmap"
import { useBacktestReport } from "@/hooks/useBacktestReport"
import { TearsheetTab } from "./TearsheetTab"
import { TradeAnalyticsTab } from "./TradeAnalyticsTab"
import { StatCard } from "./StatCard"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts"
import type { BacktestResult, BacktestRequest } from "@/types"

// ── 扩展报告 Tab 加载/错误/空态包装 ──────────────────────────────
interface ReportSectionProps {
  loading: boolean
  error: Error | null
  hasForm: boolean
  loadingLabel: string
  children: React.ReactNode
}
function ReportSection({ loading, error, hasForm, loadingLabel, children }: ReportSectionProps) {
  if (!hasForm) {
    return (
      <div className="card">
        <EmptyState title="无法加载扩展分析" description="缺少回测配置，请重新运行回测" />
      </div>
    )
  }
  if (loading) {
    return (
      <div className="card flex items-center justify-center h-48">
        <div className="text-center">
          <Spinner size="lg" className="mx-auto mb-3" />
          <p className="text-[#8b949e] text-sm">{loadingLabel}</p>
        </div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="card">
        <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          {error.message}
        </p>
      </div>
    )
  }
  return <>{children}</>
}

// ── Tab: 回测结果 ─────────────────────────────────────────────
interface BacktestResultPanelProps {
  result: BacktestResult
  /** 原始回测配置，用于"启动模拟盘"参数透传 */
  form?: BacktestRequest
}

type ResultTab = "overview" | "tearsheet" | "trade_analytics" | "drawdown" | "monthly" | "trades"

export function BacktestResultPanel({ result, form }: BacktestResultPanelProps) {
  const navigate = useNavigate()
  const m = result.metrics
  const [resultTab, setResultTab] = useState<ResultTab>("overview")

  // ── C6/C7 扩展报告：按需在切换到 Tearsheet / 交易分析 Tab 时拉取 ──
  const {
    mutate: fetchReport, data: report, isPending: reportLoading,
    error: reportError, reset: resetReport,
  } = useBacktestReport()
  const needsReport = resultTab === "tearsheet" || resultTab === "trade_analytics"

  // 新回测结果到达时清空旧扩展报告，下次进入扩展 Tab 重新拉取
  useEffect(() => {
    resetReport()
  }, [result.backtest_id, resetReport])

  // 首次进入扩展 Tab 且尚未加载/出错时拉取
  useEffect(() => {
    if (needsReport && form && !report && !reportLoading && !reportError) {
      fetchReport(form)
    }
  }, [needsReport, form, report, reportLoading, reportError, fetchReport])

  /** 是否达到模拟盘门槛（Sharpe>0.5, 回撤<30%, 正收益） */
  const isQualified = m.sharpe_ratio >= 0.5 && Math.abs(m.max_drawdown_pct) < 30 && m.total_return_pct > 0

  /** 携带参数跳转到模拟盘 */
  function handleLaunchPaper() {
    if (!form) { navigate("/live-strategy"); return }
    const params = new URLSearchParams({
      strategy: form.strategy_name,
      symbol:   form.symbol,
      market:   form.market,
      freq:     form.frequency,
      params:   JSON.stringify(form.params ?? {}),
    })
    navigate(`/live-strategy?${params.toString()}`)
  }

  const RESULT_TABS: { key: ResultTab; label: string }[] = [
    { key: "overview", label: "总览" },
    { key: "tearsheet", label: "Tearsheet" },
    { key: "trade_analytics", label: "交易分析" },
    { key: "drawdown", label: "回撤" },
    { key: "monthly", label: "月度收益" },
    { key: "trades", label: "交易记录" },
  ]

  const excess = m.total_return_pct - m.buy_hold_return_pct

  return (
    <div className="space-y-4">
      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-[#21262d]">
        {RESULT_TABS.map(({ key, label }) => (
          <button key={key}
            className={`px-3 py-1.5 text-xs border-b-2 -mb-px transition-colors ${
              resultTab === key
                ? "border-[#58a6ff] text-[#58a6ff]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
            onClick={() => setResultTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {/* 总览 */}
      {resultTab === "overview" && (
        <>
          {/* 核心指标 — 两行 */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCard label="总收益率" value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`}
              accent={m.total_return_pct >= 0 ? "up" : "down"} sub={`买持: ${m.buy_hold_return_pct >= 0 ? "+" : ""}${m.buy_hold_return_pct.toFixed(2)}%`} />
            <StatCard label="年化收益" value={`${m.annual_return_pct >= 0 ? "+" : ""}${m.annual_return_pct.toFixed(2)}%`}
              accent={m.annual_return_pct >= 0 ? "up" : "down"} sub={`波动率: ${m.volatility_pct.toFixed(2)}%`} />
            <StatCard label="最终净值" value={`$${result.final_value.toLocaleString()}`}
              sub={`初始 $${result.initial_cash.toLocaleString()}`} />
            <StatCard label="超额收益" value={`${excess >= 0 ? "+" : ""}${excess.toFixed(2)}%`}
              accent={excess >= 0 ? "up" : "down"} sub="vs 买入持有" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCard label="夏普比率" value={m.sharpe_ratio.toFixed(3)}
              accent={m.sharpe_ratio >= 1 ? "up" : m.sharpe_ratio < 0 ? "down" : undefined}
              help="年化收益 / 年化波动率，越高越好" />
            <StatCard label="索提诺比率" value={m.sortino_ratio.toFixed(3)}
              help="年化收益 / 下行波动率" />
            <StatCard label="卡玛比率" value={m.calmar_ratio.toFixed(3)}
              help="年化收益 / |最大回撤|" />
            <StatCard label="Omega 比率" value={m.omega_ratio.toFixed(3)}
              accent={m.omega_ratio > 1 ? "up" : "down"}
              help="盈利面积 / 亏损面积，>1为正期望" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCard label="最大回撤" value={`${m.max_drawdown_pct.toFixed(2)}%`}
              accent="down" sub={`持续 ${m.max_drawdown_duration} 天`} />
            <StatCard label="胜率" value={`${m.win_rate_pct.toFixed(1)}%`}
              accent={m.win_rate_pct >= 50 ? "up" : "down"} sub={`共 ${m.total_trades} 笔`} />
            <StatCard label="盈亏比" value={m.profit_factor.toFixed(3)}
              accent={m.profit_factor >= 1.5 ? "up" : "down"} help="总盈利 / 总亏损" />
            <StatCard label="SQN" value={m.sqn.toFixed(2)}
              accent={m.sqn >= 2 ? "up" : m.sqn < 0 ? "down" : undefined}
              help="系统品质数: >2好, >3极好" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCard label="期望值/笔" value={`$${m.expectancy.toFixed(2)}`}
              accent={m.expectancy >= 0 ? "up" : "down"} help="平均每笔交易期望盈亏" />
            <StatCard label="平均盈利" value={`$${m.avg_win.toFixed(2)}`} accent="up" />
            <StatCard label="平均亏损" value={`$${m.avg_loss.toFixed(2)}`} accent="down" />
            <StatCard label="连胜/连败"
              value={`${m.max_consecutive_wins}/${m.max_consecutive_losses}`}
              sub="最大连胜/连败" />
          </div>

          {/* 净值曲线 */}
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">净值曲线</h3>
            <EquityCurve data={result.equity_curve} initialCash={result.initial_cash} height={240} />
          </div>

          {/* ── 下一步 CTA ── */}
          <div className={`rounded-xl border p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 ${
            isQualified
              ? "border-[#3fb950]/30 bg-[#0d2018]"
              : "border-[#e3b341]/25 bg-[#1a1500]"
          }`}>
            <div>
              <p className={`text-sm font-semibold mb-1 ${isQualified ? "text-[#3fb950]" : "text-[#e3b341]"}`}>
                {isQualified ? "✅ 回测指标达标，可进入模拟验证" : "⚠ 回测结果仅供参考，建议先在模拟盘验证"}
              </p>
              <p className="text-xs text-[#6e7681]">
                {isQualified
                  ? `Sharpe ${m.sharpe_ratio.toFixed(2)} · 回撤 ${m.max_drawdown_pct.toFixed(1)}% · 收益 +${m.total_return_pct.toFixed(1)}%，以相同参数启动 60 天模拟盘`
                  : "历史回测不代表未来表现，在真实市场数据上模拟验证后再考虑实盘"}
              </p>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={handleLaunchPaper}
                className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
                  isQualified
                    ? "bg-[#3fb950]/15 border border-[#3fb950]/40 text-[#3fb950] hover:bg-[#3fb950]/25"
                    : "bg-[#e3b341]/10 border border-[#e3b341]/30 text-[#e3b341] hover:bg-[#e3b341]/20"
                }`}>
                ▶ 启动模拟盘
              </button>
            </div>
          </div>
        </>
      )}

      {/* Tearsheet (C7) */}
      {resultTab === "tearsheet" && (
        <ReportSection loading={reportLoading} error={reportError} hasForm={!!form} loadingLabel="生成 Tearsheet 中…">
          <TearsheetTab
            rolling={report?.rolling_stats}
            drawdownPeriods={report?.drawdown_periods ?? []}
          />
        </ReportSection>
      )}

      {/* 交易分析 (C6/C7) */}
      {resultTab === "trade_analytics" && (
        <ReportSection loading={reportLoading} error={reportError} hasForm={!!form} loadingLabel="计算逐笔分析中…">
          <TradeAnalyticsTab
            analytics={report?.trade_analytics}
            tagMetrics={report?.tag_metrics}
            periodic={report?.periodic_stats}
          />
        </ReportSection>
      )}

      {/* 回撤 */}
      {resultTab === "drawdown" && (
        <>
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-1">回撤曲线</h3>
            <p className="text-xs text-[#6e7681] mb-3">最大回撤: {m.max_drawdown_pct.toFixed(2)}%，持续 {m.max_drawdown_duration} 天</p>
            <DrawdownChart data={result.drawdown_series} height={220} />
          </div>

          {/* 盈亏分布 */}
          {result.pnl_distribution.length > 0 && (
            <div className="card">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
                交易盈亏分布 <span className="text-xs text-[#6e7681] font-normal">（每笔成交 P&L）</span>
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={result.pnl_distribution} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                  <XAxis dataKey="range" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={36} />
                  <Tooltip
                    contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
                    formatter={(v: number) => [v, "次数"]}
                  />
                  <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                    {result.pnl_distribution.map((entry, idx) => (
                      <Cell key={idx} fill={entry.positive ? "#3fb950" : "#f85149"} fillOpacity={0.8} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      {/* 月度收益 */}
      {resultTab === "monthly" && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">月度收益热力图</h3>
          {Object.keys(result.monthly_returns).length > 0 ? (
            <MonthlyHeatmap data={result.monthly_returns} />
          ) : (
            <p className="text-[#6e7681] text-sm text-center py-6">数据不足，无法生成月度收益（至少需要 2 个月）</p>
          )}
        </div>
      )}

      {/* 交易记录 */}
      {resultTab === "trades" && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
            成交记录 <span className="text-[#6e7681] font-normal text-xs">（共 {result.fills.length} 笔）</span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#21262d]">
                  <th className="text-left py-2 pr-3">时间</th>
                  <th className="text-left py-2 pr-3">方向</th>
                  <th className="text-right py-2 pr-3">数量</th>
                  <th className="text-right py-2 pr-3">价格</th>
                  <th className="text-right py-2 pr-3">手续费</th>
                  <th className="text-right py-2">实现盈亏</th>
                </tr>
              </thead>
              <tbody>
                {result.fills.slice(0, 100).map((f, i) => (
                  <tr key={i} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                    <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{f.filled_at?.slice(0, 10)}</td>
                    <td className={`py-1.5 pr-3 font-medium ${f.side === "BUY" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                      {f.side === "BUY" ? "买入" : "卖出"}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{f.qty}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">${f.price.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#6e7681]">${f.commission.toFixed(4)}</td>
                    <td className={`py-1.5 text-right font-mono ${f.realized_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                      {f.realized_pnl >= 0 ? "+" : ""}${f.realized_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
