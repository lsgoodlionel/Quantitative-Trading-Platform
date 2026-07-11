import { useState } from "react"
import { useOptimize } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { MARKET_CFGS, today, yearsAgo } from "./config"

// ── Tab: 参数优化 ─────────────────────────────────────────────
export function OptimizeTab({ strategies }: { strategies: { name: string; description: string }[] }) {
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
