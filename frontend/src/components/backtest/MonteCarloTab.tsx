import { useState } from "react"
import { useMonteCarlo } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import {
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area,
} from "recharts"
import type { Market, Frequency } from "@/types"
import { MARKET_CFGS, today, yearsAgo } from "./config"

// ── Tab: 蒙特卡洛 ─────────────────────────────────────────────
export function MonteCarloTab({ strategies }: { strategies: { name: string; description: string }[] }) {
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
