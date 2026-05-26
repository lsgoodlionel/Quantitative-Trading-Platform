import { useState, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { EquityCurve } from "@/components/charts/EquityCurve"
import { useStrategies, useRunBacktest } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import type { BacktestResult, BacktestRequest, Market, Frequency } from "@/types"
import { format, subYears } from "date-fns"

const MARKETS: Market[] = ["US", "HK"]
const FREQUENCIES: Frequency[] = ["1d", "1h", "15m", "5m", "1m"]

function today() { return format(new Date(), "yyyy-MM-dd") }
function oneYearAgo() { return format(subYears(new Date(), 1), "yyyy-MM-dd") }

interface MetricCardProps { label: string; value: string; sub?: string; accent?: "up" | "down" }
function MetricCard({ label, value, sub, accent }: MetricCardProps) {
  return (
    <div className="card py-3">
      <p className="text-xs text-[#6e7681] mb-1">{label}</p>
      <p className={`font-mono text-lg font-semibold ${accent === "up" ? "text-[#3fb950]" : accent === "down" ? "text-[#f85149]" : "text-[#e6edf3]"}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-[#6e7681] mt-0.5">{sub}</p>}
    </div>
  )
}

export function Backtest() {
  const [searchParams] = useSearchParams()
  const { data: strategies } = useStrategies()
  const { mutate: runBacktest, isPending, error } = useRunBacktest()
  const [result, setResult] = useState<BacktestResult | null>(null)

  const [form, setForm] = useState<BacktestRequest>({
    strategy_name: searchParams.get("strategy") ?? "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: oneYearAgo(),
    end_date: today(),
    initial_cash: 100000,
    params: {},
  })

  useEffect(() => {
    const s = searchParams.get("strategy")
    if (s) setForm((prev) => ({ ...prev, strategy_name: s }))
  }, [searchParams])

  function update<K extends keyof BacktestRequest>(key: K, value: BacktestRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleRun(e: React.FormEvent) {
    e.preventDefault()
    runBacktest(form, { onSuccess: (data) => setResult(data) })
  }

  const m = result?.metrics

  return (
    <AppShell title="回测">
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Config Panel */}
        <form onSubmit={handleRun} className="xl:col-span-1 space-y-4">
          <div className="card space-y-4">
            <h2 className="text-sm font-semibold text-[#e6edf3] mb-2">策略配置</h2>

            <div>
              <label className="label">策略</label>
              <select
                className="select w-full mt-1"
                value={form.strategy_name}
                onChange={(e) => update("strategy_name", e.target.value)}
              >
                {(strategies ?? []).map((s) => (
                  <option key={s.name} value={s.name}>{s.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="label">标的代码</label>
              <input
                className="input w-full mt-1 font-mono uppercase"
                value={form.symbol}
                onChange={(e) => update("symbol", e.target.value.toUpperCase())}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">市场</label>
                <select className="select w-full mt-1" value={form.market} onChange={(e) => update("market", e.target.value as Market)}>
                  {MARKETS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <label className="label">周期</label>
                <select className="select w-full mt-1" value={form.frequency} onChange={(e) => update("frequency", e.target.value as Frequency)}>
                  {FREQUENCIES.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">开始日期</label>
                <input className="input w-full mt-1" type="date" value={form.start_date} onChange={(e) => update("start_date", e.target.value)} />
              </div>
              <div>
                <label className="label">结束日期</label>
                <input className="input w-full mt-1" type="date" value={form.end_date} onChange={(e) => update("end_date", e.target.value)} />
              </div>
            </div>

            <div>
              <label className="label">初始资金 ($)</label>
              <input
                className="input w-full mt-1 font-mono"
                type="number"
                value={form.initial_cash}
                onChange={(e) => update("initial_cash", Number(e.target.value))}
                min={1000}
                step={1000}
              />
            </div>

            {error && (
              <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
                {error.message}
              </p>
            )}

            <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
              {isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 开始回测"}
            </button>
          </div>
        </form>

        {/* Results Panel */}
        <div className="xl:col-span-2 space-y-4">
          {isPending && (
            <div className="card flex items-center justify-center h-48">
              <div className="text-center">
                <Spinner size="lg" className="mx-auto mb-3" />
                <p className="text-[#8b949e] text-sm">回测运行中…</p>
              </div>
            </div>
          )}

          {!isPending && !result && (
            <div className="card">
              <EmptyState title="配置参数后点击开始回测" description="支持双均线、布林带、MACD 等 8 种策略" />
            </div>
          )}

          {result && m && (
            <>
              {/* Metrics */}
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                <MetricCard label="总收益率" value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`} accent={m.total_return_pct >= 0 ? "up" : "down"} />
                <MetricCard label="年化收益" value={`${m.annual_return_pct >= 0 ? "+" : ""}${m.annual_return_pct.toFixed(2)}%`} accent={m.annual_return_pct >= 0 ? "up" : "down"} />
                <MetricCard label="夏普比率" value={m.sharpe_ratio.toFixed(3)} accent={m.sharpe_ratio >= 1 ? "up" : m.sharpe_ratio < 0 ? "down" : undefined} />
                <MetricCard label="索提诺比率" value={m.sortino_ratio.toFixed(3)} />
                <MetricCard label="最大回撤" value={`-${m.max_drawdown_pct.toFixed(2)}%`} accent="down" />
                <MetricCard label="卡玛比率" value={m.calmar_ratio.toFixed(3)} />
                <MetricCard label="胜率" value={`${m.win_rate_pct.toFixed(1)}%`} accent={m.win_rate_pct >= 50 ? "up" : "down"} />
                <MetricCard label="盈亏比" value={m.profit_factor.toFixed(3)} accent={m.profit_factor >= 1.5 ? "up" : "down"} />
                <MetricCard label="总交易次数" value={String(m.total_trades)} />
                <MetricCard label="年化波动率" value={`${m.volatility_pct.toFixed(2)}%`} />
                <MetricCard label="交易天数" value={String(m.trading_days)} />
                <MetricCard label="最终净值" value={`$${result.final_value.toLocaleString()}`} sub={`初始 $${result.initial_cash.toLocaleString()}`} />
              </div>

              {/* Equity Curve */}
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">净值曲线</h3>
                <EquityCurve data={result.equity_curve} initialCash={result.initial_cash} height={260} />
              </div>

              {/* Fills Table */}
              {result.fills.length > 0 && (
                <div className="card">
                  <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
                    成交记录 <span className="text-[#6e7681] font-normal text-xs">（最近 {result.fills.length} 笔）</span>
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
                        {result.fills.slice(0, 20).map((f, i) => (
                          <tr key={i} className="border-b border-[#21262d]/50 last:border-0">
                            <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{f.filled_at.slice(0, 10)}</td>
                            <td className={`py-1.5 pr-3 font-medium ${f.side === "BUY" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                              {f.side === "BUY" ? "买" : "卖"}
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
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}
