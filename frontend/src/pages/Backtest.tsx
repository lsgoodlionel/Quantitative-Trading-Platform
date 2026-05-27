import { useState, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { EquityCurve } from "@/components/charts/EquityCurve"
import { DrawdownChart } from "@/components/charts/DrawdownChart"
import { MonthlyHeatmap } from "@/components/charts/MonthlyHeatmap"
import { useStrategies, useRunBacktest, useOptimize, useMonteCarlo } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, AreaChart, Area,
} from "recharts"
import type { BacktestResult, BacktestRequest, Market, Frequency } from "@/types"
import { format, subYears } from "date-fns"

// ── 市场/频率配置 ─────────────────────────────────────────────
interface MarketCfg { value: Market; label: string; allowedFreqs: Frequency[]; defaultFreq: Frequency }
const MARKET_CFGS: MarketCfg[] = [
  { value: "US", label: "美股", allowedFreqs: ["1d", "1h", "15m", "5m", "1m"], defaultFreq: "1d" },
  { value: "HK", label: "港股", allowedFreqs: ["1d", "1w"], defaultFreq: "1d" },
  { value: "A",  label: "A股", allowedFreqs: ["1d", "1w"],  defaultFreq: "1d" },
]
const FREQ_LABELS: Record<string, string> = {
  "1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "1h": "1小时", "1d": "日线", "1w": "周线",
}

function today() { return format(new Date(), "yyyy-MM-dd") }
function yearsAgo(n: number) { return format(subYears(new Date(), n), "yyyy-MM-dd") }

// ── 子组件：指标卡 ─────────────────────────────────────────────
interface MetricCardProps {
  label: string; value: string; sub?: string
  accent?: "up" | "down"; help?: string
}
function MetricCard({ label, value, sub, accent, help }: MetricCardProps) {
  return (
    <div className="card py-3 group relative">
      <p className="text-xs text-[#6e7681] mb-1 flex items-center gap-1">
        {label}
        {help && (
          <span className="text-[10px] text-[#3d444d] cursor-help" title={help}>ⓘ</span>
        )}
      </p>
      <p className={`font-mono text-base font-semibold ${
        accent === "up" ? "text-[#3fb950]" : accent === "down" ? "text-[#f85149]" : "text-[#e6edf3]"
      }`}>{value}</p>
      {sub && <p className="text-[10px] text-[#6e7681] mt-0.5">{sub}</p>}
    </div>
  )
}

// ── Tab 类型 ──────────────────────────────────────────────────
type MainTab = "backtest" | "optimize" | "montecarlo"

// ── 回测配置面板 ──────────────────────────────────────────────
interface ConfigPanelProps {
  form: BacktestRequest
  strategies: { name: string; description: string }[]
  isLoading: boolean
  error: Error | null
  onChange: (key: keyof BacktestRequest, val: BacktestRequest[keyof BacktestRequest]) => void
  onSubmit: (e: React.FormEvent) => void
  submitLabel?: string
}

function ConfigPanel({ form, strategies, isLoading, error, onChange, onSubmit, submitLabel = "▶ 开始回测" }: ConfigPanelProps) {
  const marketCfg = MARKET_CFGS.find((c) => c.value === form.market) ?? MARKET_CFGS[0]

  function handleMarketChange(m: string) {
    const cfg = MARKET_CFGS.find((c) => c.value === m) ?? MARKET_CFGS[0]
    onChange("market", m as Market)
    onChange("frequency", cfg.defaultFreq)
  }

  return (
    <form onSubmit={onSubmit} className="card space-y-4">
      <h2 className="text-sm font-semibold text-[#e6edf3]">策略配置</h2>

      {/* 策略选择 */}
      <div>
        <label className="label">策略</label>
        <select className="select w-full mt-1" value={form.strategy_name}
          onChange={(e) => onChange("strategy_name", e.target.value)}>
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>{s.description || s.name}</option>
          ))}
        </select>
      </div>

      {/* 市场 + 标的 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">市场</label>
          <select className="select w-full mt-1" value={form.market}
            onChange={(e) => handleMarketChange(e.target.value)}>
            {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <label className="label">
            标的
            <span className="text-[10px] text-[#6e7681] ml-1">
              {form.market === "A" ? "如 000001" : form.market === "HK" ? "如 00700" : "如 AAPL"}
            </span>
          </label>
          <input className="input w-full mt-1 font-mono uppercase"
            value={form.symbol}
            onChange={(e) => onChange("symbol", e.target.value.toUpperCase())}
          />
        </div>
      </div>

      {/* 周期 */}
      <div>
        <label className="label">K线周期</label>
        <select className="select w-full mt-1" value={form.frequency}
          onChange={(e) => onChange("frequency", e.target.value as Frequency)}>
          {marketCfg.allowedFreqs.map((f) => (
            <option key={f} value={f}>{FREQ_LABELS[f]}</option>
          ))}
        </select>
      </div>

      {/* 快捷日期 */}
      <div>
        <label className="label mb-1.5">日期范围</label>
        <div className="flex gap-1 mb-2">
          {[
            { label: "1年", fn: () => yearsAgo(1) },
            { label: "2年", fn: () => yearsAgo(2) },
            { label: "3年", fn: () => yearsAgo(3) },
            { label: "5年", fn: () => yearsAgo(5) },
          ].map(({ label, fn }) => (
            <button key={label} type="button"
              className="btn btn-ghost text-xs px-2 py-0.5"
              onClick={() => onChange("start_date", fn())}>
              {label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input className="input" type="date" value={form.start_date}
            onChange={(e) => onChange("start_date", e.target.value)} />
          <input className="input" type="date" value={form.end_date}
            onChange={(e) => onChange("end_date", e.target.value)} />
        </div>
      </div>

      {/* 初始资金 */}
      <div>
        <label className="label">初始资金</label>
        <input className="input w-full mt-1 font-mono" type="number"
          value={form.initial_cash}
          onChange={(e) => onChange("initial_cash", Number(e.target.value))}
          min={1000} step={10000}
        />
      </div>

      {error && (
        <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          {error.message}
        </p>
      )}

      <button type="submit" className="btn btn-primary w-full" disabled={isLoading}>
        {isLoading ? <Spinner size="sm" className="mx-auto" /> : submitLabel}
      </button>
    </form>
  )
}

// ── Tab: 回测结果 ─────────────────────────────────────────────
function BacktestResultPanel({ result }: { result: BacktestResult }) {
  const m = result.metrics
  const [resultTab, setResultTab] = useState<"overview" | "drawdown" | "monthly" | "trades">("overview")

  const RESULT_TABS = [
    { key: "overview" as const, label: "总览" },
    { key: "drawdown" as const, label: "回撤" },
    { key: "monthly" as const, label: "月度收益" },
    { key: "trades" as const, label: "交易记录" },
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
            <MetricCard label="总收益率" value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`}
              accent={m.total_return_pct >= 0 ? "up" : "down"} sub={`买持: ${m.buy_hold_return_pct >= 0 ? "+" : ""}${m.buy_hold_return_pct.toFixed(2)}%`} />
            <MetricCard label="年化收益" value={`${m.annual_return_pct >= 0 ? "+" : ""}${m.annual_return_pct.toFixed(2)}%`}
              accent={m.annual_return_pct >= 0 ? "up" : "down"} sub={`波动率: ${m.volatility_pct.toFixed(2)}%`} />
            <MetricCard label="最终净值" value={`$${result.final_value.toLocaleString()}`}
              sub={`初始 $${result.initial_cash.toLocaleString()}`} />
            <MetricCard label="超额收益" value={`${excess >= 0 ? "+" : ""}${excess.toFixed(2)}%`}
              accent={excess >= 0 ? "up" : "down"} sub="vs 买入持有" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <MetricCard label="夏普比率" value={m.sharpe_ratio.toFixed(3)}
              accent={m.sharpe_ratio >= 1 ? "up" : m.sharpe_ratio < 0 ? "down" : undefined}
              help="年化收益 / 年化波动率，越高越好" />
            <MetricCard label="索提诺比率" value={m.sortino_ratio.toFixed(3)}
              help="年化收益 / 下行波动率" />
            <MetricCard label="卡玛比率" value={m.calmar_ratio.toFixed(3)}
              help="年化收益 / |最大回撤|" />
            <MetricCard label="Omega 比率" value={m.omega_ratio.toFixed(3)}
              accent={m.omega_ratio > 1 ? "up" : "down"}
              help="盈利面积 / 亏损面积，>1为正期望" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <MetricCard label="最大回撤" value={`${m.max_drawdown_pct.toFixed(2)}%`}
              accent="down" sub={`持续 ${m.max_drawdown_duration} 天`} />
            <MetricCard label="胜率" value={`${m.win_rate_pct.toFixed(1)}%`}
              accent={m.win_rate_pct >= 50 ? "up" : "down"} sub={`共 ${m.total_trades} 笔`} />
            <MetricCard label="盈亏比" value={m.profit_factor.toFixed(3)}
              accent={m.profit_factor >= 1.5 ? "up" : "down"} help="总盈利 / 总亏损" />
            <MetricCard label="SQN" value={m.sqn.toFixed(2)}
              accent={m.sqn >= 2 ? "up" : m.sqn < 0 ? "down" : undefined}
              help="系统品质数: >2好, >3极好" />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <MetricCard label="期望值/笔" value={`$${m.expectancy.toFixed(2)}`}
              accent={m.expectancy >= 0 ? "up" : "down"} help="平均每笔交易期望盈亏" />
            <MetricCard label="平均盈利" value={`$${m.avg_win.toFixed(2)}`} accent="up" />
            <MetricCard label="平均亏损" value={`$${m.avg_loss.toFixed(2)}`} accent="down" />
            <MetricCard label="连胜/连败"
              value={`${m.max_consecutive_wins}/${m.max_consecutive_losses}`}
              sub="最大连胜/连败" />
          </div>

          {/* 净值曲线 */}
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">净值曲线</h3>
            <EquityCurve data={result.equity_curve} initialCash={result.initial_cash} height={240} />
          </div>
        </>
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

// ── Tab: 参数优化 ─────────────────────────────────────────────
function OptimizeTab({ strategies }: { strategies: { name: string; description: string }[] }) {
  const { mutate: runOptimize, isPending, data: result, error } = useOptimize()

  const [form, setForm] = useState({
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(2),
    end_date: today(),
    initial_cash: 100000,
    optimize_target: "sharpe_ratio",
    max_combinations: 30,
    // 参数网格（文本输入）
    param_grid_text: '{"short_window": [5, 10, 20], "long_window": [50, 100, 200]}',
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    let param_grid: Record<string, number[]>
    try {
      param_grid = JSON.parse(form.param_grid_text)
    } catch {
      alert("参数网格 JSON 格式错误")
      return
    }
    runOptimize({ ...form, param_grid })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      {/* 配置 */}
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">参数优化配置</h2>

        <div>
          <label className="label">策略</label>
          <select className="select w-full mt-1" value={form.strategy_name}
            onChange={(e) => setForm((f) => ({ ...f, strategy_name: e.target.value }))}>
            {strategies.map((s) => <option key={s.name} value={s.name}>{s.description || s.name}</option>)}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">市场</label>
            <select className="select w-full mt-1" value={form.market}
              onChange={(e) => setForm((f) => ({ ...f, market: e.target.value }))}>
              {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label">标的</label>
            <input className="input w-full mt-1 font-mono uppercase" value={form.symbol}
              onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">开始日期</label>
            <input className="input w-full mt-1" type="date" value={form.start_date}
              onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div>
            <label className="label">结束日期</label>
            <input className="input w-full mt-1" type="date" value={form.end_date}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
        </div>

        <div>
          <label className="label">
            参数网格 <span className="text-[10px] text-[#6e7681]">JSON 格式</span>
          </label>
          <textarea
            className="input w-full mt-1 font-mono text-xs resize-none"
            rows={4}
            value={form.param_grid_text}
            onChange={(e) => setForm((f) => ({ ...f, param_grid_text: e.target.value }))}
            placeholder={'{"short_window": [5,10,20], "long_window": [50,100,200]}'}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">优化目标</label>
            <select className="select w-full mt-1" value={form.optimize_target}
              onChange={(e) => setForm((f) => ({ ...f, optimize_target: e.target.value }))}>
              <option value="sharpe_ratio">夏普比率</option>
              <option value="total_return_pct">总收益率</option>
              <option value="calmar_ratio">卡玛比率</option>
              <option value="sqn">SQN</option>
              <option value="omega_ratio">Omega 比率</option>
            </select>
          </div>
          <div>
            <label className="label">最大组合数</label>
            <input className="input w-full mt-1 font-mono" type="number"
              value={form.max_combinations} min={5} max={200}
              onChange={(e) => setForm((f) => ({ ...f, max_combinations: parseInt(e.target.value) || 30 }))} />
          </div>
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🔍 开始优化"}
        </button>
      </form>

      {/* 结果 */}
      <div className="xl:col-span-2">
        {isPending && (
          <div className="card flex items-center justify-center h-48">
            <div className="text-center">
              <Spinner size="lg" className="mx-auto mb-3" />
              <p className="text-[#8b949e] text-sm">网格搜索中，请稍候…</p>
            </div>
          </div>
        )}

        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置参数网格后点击开始优化"
              description="系统将自动搜索最优参数组合" />
          </div>
        )}

        {result && (
          <div className="space-y-4">
            {/* 最优参数 */}
            <div className="card">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
                最优参数 <span className="text-xs text-[#6e7681] font-normal">({result.optimize_target})</span>
              </h3>
              <div className="flex flex-wrap gap-3 mb-3">
                {Object.entries(result.best_params).map(([k, v]) => (
                  <div key={k} className="bg-[#1f6feb]/10 border border-[#1f6feb]/30 rounded px-3 py-1.5">
                    <span className="text-xs text-[#58a6ff]">{k}</span>
                    <span className="ml-2 font-mono font-bold text-[#e6edf3]">{v}</span>
                  </div>
                ))}
                <div className="bg-[#3fb950]/10 border border-[#3fb950]/30 rounded px-3 py-1.5">
                  <span className="text-xs text-[#3fb950]">得分</span>
                  <span className="ml-2 font-mono font-bold text-[#3fb950]">{result.best_score.toFixed(4)}</span>
                </div>
              </div>
              <p className="text-xs text-[#6e7681]">
                共评估 {result.evaluated_combinations} / {result.total_combinations} 个参数组合
              </p>
            </div>

            {/* 排行表 */}
            <div className="card">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">优化结果排行</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[#8b949e] border-b border-[#21262d]">
                      <th className="text-center py-2 pr-3 w-8">#</th>
                      <th className="text-left py-2 pr-3">参数</th>
                      <th className="text-right py-2 pr-3">得分</th>
                      <th className="text-right py-2 pr-3">总收益</th>
                      <th className="text-right py-2 pr-3">年化</th>
                      <th className="text-right py-2 pr-3">夏普</th>
                      <th className="text-right py-2 pr-3">最大回撤</th>
                      <th className="text-right py-2">交易数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.results.slice(0, 20).map((r, idx) => (
                      <tr key={idx} className={`border-b border-[#21262d]/50 last:border-0 ${idx === 0 ? "bg-[#3fb950]/5" : "hover:bg-[#21262d]/30"}`}>
                        <td className="py-1.5 pr-3 text-center text-[#6e7681]">{idx + 1}</td>
                        <td className="py-1.5 pr-3 font-mono text-[10px] text-[#e6edf3]">
                          {Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(", ")}
                        </td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[#58a6ff]">{r.score.toFixed(4)}</td>
                        <td className={`py-1.5 pr-3 text-right font-mono ${r.total_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                          {r.total_return_pct >= 0 ? "+" : ""}{r.total_return_pct.toFixed(2)}%
                        </td>
                        <td className={`py-1.5 pr-3 text-right font-mono ${r.annual_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                          {r.annual_return_pct >= 0 ? "+" : ""}{r.annual_return_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{r.sharpe_ratio.toFixed(3)}</td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[#f85149]">{r.max_drawdown_pct.toFixed(2)}%</td>
                        <td className="py-1.5 text-right font-mono text-[#8b949e]">{r.total_trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Tab: 蒙特卡洛 ─────────────────────────────────────────────
function MonteCarloTab({ strategies }: { strategies: { name: string; description: string }[] }) {
  const { mutate: runMC, isPending, data: result, error } = useMonteCarlo()
  const [form, setForm] = useState({
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US" as Market,
    frequency: "1d" as Frequency,
    start_date: yearsAgo(2),
    end_date: today(),
    initial_cash: 100000,
    n_simulations: 300,
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    runMC({ ...form, params: {} })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      {/* 配置 */}
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">蒙特卡洛配置</h2>
        <p className="text-xs text-[#6e7681]">
          随机打乱成交顺序 N 次，评估策略统计显著性。
          若大部分模拟结果均为正收益，则策略具有统计稳健性。
        </p>

        <div>
          <label className="label">策略</label>
          <select className="select w-full mt-1" value={form.strategy_name}
            onChange={(e) => setForm((f) => ({ ...f, strategy_name: e.target.value }))}>
            {strategies.map((s) => <option key={s.name} value={s.name}>{s.description || s.name}</option>)}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">市场</label>
            <select className="select w-full mt-1" value={form.market}
              onChange={(e) => setForm((f) => ({ ...f, market: e.target.value as Market }))}>
              {MARKET_CFGS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label">标的</label>
            <input className="input w-full mt-1 font-mono uppercase" value={form.symbol}
              onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">开始日期</label>
            <input className="input w-full mt-1" type="date" value={form.start_date}
              onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div>
            <label className="label">结束日期</label>
            <input className="input w-full mt-1" type="date" value={form.end_date}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
        </div>

        <div>
          <label className="label">模拟次数</label>
          <input className="input w-full mt-1 font-mono" type="number"
            value={form.n_simulations} min={50} max={1000} step={50}
            onChange={(e) => setForm((f) => ({ ...f, n_simulations: parseInt(e.target.value) || 300 }))} />
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🎲 运行蒙特卡洛"}
        </button>
      </form>

      {/* 结果 */}
      <div className="xl:col-span-2">
        {isPending && (
          <div className="card flex items-center justify-center h-48">
            <div className="text-center">
              <Spinner size="lg" className="mx-auto mb-3" />
              <p className="text-[#8b949e] text-sm">模拟运行中…</p>
            </div>
          </div>
        )}

        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置参数后点击运行蒙特卡洛"
              description="验证策略的统计显著性和稳健性" />
          </div>
        )}

        {result && (
          <div className="space-y-4">
            {/* 统计概要 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="card py-3">
                <p className="text-xs text-[#6e7681] mb-1">正收益概率</p>
                <p className={`font-mono text-xl font-bold ${result.prob_positive >= 0.6 ? "text-[#3fb950]" : result.prob_positive >= 0.4 ? "text-[#e3b341]" : "text-[#f85149]"}`}>
                  {(result.prob_positive * 100).toFixed(1)}%
                </p>
              </div>
              <div className="card py-3">
                <p className="text-xs text-[#6e7681] mb-1">原始夏普</p>
                <p className="font-mono text-xl font-bold text-[#e6edf3]">{result.original_sharpe.toFixed(3)}</p>
              </div>
              <div className="card py-3">
                <p className="text-xs text-[#6e7681] mb-1">原始收益</p>
                <p className={`font-mono text-xl font-bold ${result.original_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                  {result.original_return_pct >= 0 ? "+" : ""}{result.original_return_pct.toFixed(2)}%
                </p>
              </div>
              <div className="card py-3">
                <p className="text-xs text-[#6e7681] mb-1">模拟次数</p>
                <p className="font-mono text-xl font-bold text-[#e6edf3]">{result.n_simulations}</p>
              </div>
            </div>

            {/* 分布表 */}
            <div className="card">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">收益率分布（{result.n_simulations} 次模拟）</h3>
              <div className="grid grid-cols-5 gap-2 text-center text-xs">
                {[
                  { label: "P5（悲观）", val: result.p5_return_pct },
                  { label: "P25", val: result.p25_return_pct },
                  { label: "P50（中位）", val: result.p50_return_pct },
                  { label: "P75", val: result.p75_return_pct },
                  { label: "P95（乐观）", val: result.p95_return_pct },
                ].map(({ label, val }) => (
                  <div key={label} className="bg-[#1c2128] rounded-lg py-2 px-1">
                    <p className="text-[#6e7681] mb-1">{label}</p>
                    <p className={`font-mono font-bold text-sm ${val >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                      {val >= 0 ? "+" : ""}{val.toFixed(2)}%
                    </p>
                  </div>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-4 text-xs">
                <div>
                  <p className="text-[#6e7681] mb-1">夏普比率区间（P5~P95）</p>
                  <p className="font-mono text-[#e6edf3]">{result.p5_sharpe.toFixed(3)} ~ {result.p95_sharpe.toFixed(3)}</p>
                </div>
                <div>
                  <p className="text-[#6e7681] mb-1">最大回撤区间（P5~P95）</p>
                  <p className="font-mono text-[#f85149]">{result.p5_max_drawdown_pct.toFixed(2)}% ~ {result.p95_max_drawdown_pct.toFixed(2)}%</p>
                </div>
              </div>
            </div>

            {/* 净值包络图 */}
            {result.envelope.length > 0 && (
              <div className="card">
                <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">净值曲线置信区间</h3>
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={result.envelope} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="mcGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.15} />
                        <stop offset="95%" stopColor="#58a6ff" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                    <XAxis dataKey="time" tickFormatter={(v: string) => v.slice(0, 7)}
                      tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false}
                      interval="preserveStartEnd" />
                    <YAxis tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                      tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={52} />
                    <Tooltip
                      contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
                      formatter={(v: number, name: string) => [`$${v.toLocaleString()}`, name]}
                    />
                    <Area type="monotone" dataKey="p95" stroke="none" fill="url(#mcGrad)" fillOpacity={0.4} name="P95" />
                    <Area type="monotone" dataKey="p75" stroke="none" fill="#58a6ff" fillOpacity={0.1} name="P75" />
                    <Area type="monotone" dataKey="p50" stroke="#58a6ff" strokeWidth={2} fill="none" dot={false} name="中位数" />
                    <Area type="monotone" dataKey="p25" stroke="none" fill="#f85149" fillOpacity={0.05} name="P25" />
                    <Area type="monotone" dataKey="p5" stroke="#f85149" strokeWidth={1} strokeDasharray="4 2" fill="none" dot={false} name="P5" />
                  </AreaChart>
                </ResponsiveContainer>
                <p className="text-[10px] text-[#6e7681] mt-2 text-center">
                  蓝线=中位数，红虚线=P5（悲观），浅蓝区=P25~P75置信区间
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────
export function Backtest() {
  const [searchParams] = useSearchParams()
  const { data: strategies } = useStrategies()
  const { mutate: runBacktest, isPending, error } = useRunBacktest()
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [activeTab, setActiveTab] = useState<MainTab>("backtest")

  const [form, setForm] = useState<BacktestRequest>({
    strategy_name: searchParams.get("strategy") ?? "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(2),
    end_date: today(),
    initial_cash: 100_000,
    params: {},
  })

  useEffect(() => {
    const s = searchParams.get("strategy")
    if (s) setForm((prev) => ({ ...prev, strategy_name: s }))
  }, [searchParams])

  function updateForm<K extends keyof BacktestRequest>(key: K, val: BacktestRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: val }))
  }

  function handleRun(e: React.FormEvent) {
    e.preventDefault()
    runBacktest(form, { onSuccess: (data) => setResult(data) })
  }

  const stratList = strategies ?? []

  const TABS: { key: MainTab; label: string }[] = [
    { key: "backtest", label: "📊 策略回测" },
    { key: "optimize", label: "🔍 参数优化" },
    { key: "montecarlo", label: "🎲 蒙特卡洛" },
  ]

  return (
    <AppShell title="回测">
      {/* 主 Tab */}
      <div className="flex gap-1 mb-5 border-b border-[#21262d]">
        {TABS.map(({ key, label }) => (
          <button key={key}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              activeTab === key
                ? "border-[#58a6ff] text-[#58a6ff]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
            onClick={() => setActiveTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {/* 策略回测 */}
      {activeTab === "backtest" && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          {/* 配置 */}
          <div className="xl:col-span-1">
            <ConfigPanel
              form={form} strategies={stratList} isLoading={isPending} error={error}
              onChange={updateForm} onSubmit={handleRun}
            />
          </div>

          {/* 结果 */}
          <div className="xl:col-span-2">
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
                <EmptyState
                  title="配置策略参数后点击开始回测"
                  description="支持美股/港股/A股，含回撤分析、月度收益热力图、蒙特卡洛验证"
                />
              </div>
            )}
            {result && !isPending && <BacktestResultPanel result={result} />}
          </div>
        </div>
      )}

      {/* 参数优化 */}
      {activeTab === "optimize" && <OptimizeTab strategies={stratList} />}

      {/* 蒙特卡洛 */}
      {activeTab === "montecarlo" && <MonteCarloTab strategies={stratList} />}
    </AppShell>
  )
}
