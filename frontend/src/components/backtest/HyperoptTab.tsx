import { useState } from "react"
import { useHyperopt, useLossFunctions, type ParamSpaceDef } from "@/hooks/useBacktestValidation"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { MARKET_CFGS, today, yearsAgo } from "./config"

interface StrategyOpt {
  name: string
  description: string
}

const ALGORITHMS: { value: "grid" | "random" | "bayesian"; label: string }[] = [
  { value: "bayesian", label: "贝叶斯优化" },
  { value: "random", label: "随机搜索" },
  { value: "grid", label: "网格穷举" },
]

const DEFAULT_SPACE = '{\n  "fast_period": {"low": 3, "high": 30, "step": 1, "type": "int"},\n  "slow_period": {"low": 20, "high": 80, "step": 5, "type": "int"}\n}'

// ── Tab: Hyperopt 参数优化（贝叶斯 + 多目标损失）─────────────────
export function HyperoptTab({ strategies }: { strategies: StrategyOpt[] }) {
  const { mutate: runHyperopt, isPending, data: result, error } = useHyperopt()
  const { data: lossFns } = useLossFunctions()

  const [form, setForm] = useState({
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(2),
    end_date: today(),
    initial_cash: 100000,
    algorithm: "bayesian" as "grid" | "random" | "bayesian",
    loss_function: "sharpe",
    n_trials: 40,
    min_trades: 3,
    param_space_text: DEFAULT_SPACE,
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    let param_space: ParamSpaceDef
    try {
      param_space = JSON.parse(form.param_space_text)
    } catch {
      alert("参数空间 JSON 格式错误")
      return
    }
    runHyperopt({
      strategy_name: form.strategy_name,
      symbol: form.symbol,
      market: form.market,
      frequency: form.frequency,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_cash: form.initial_cash,
      param_space,
      algorithm: form.algorithm,
      loss_function: form.loss_function,
      n_trials: form.n_trials,
      min_trades: form.min_trades,
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      {/* 配置 */}
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">Hyperopt 参数优化</h2>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">
          贝叶斯/随机/网格搜索最优参数，多目标损失函数防过拟合。
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
            参数空间 <span className="text-[10px] text-[#6e7681]">JSON：列表或 {"{low,high,step,type}"}</span>
          </label>
          <textarea
            className="input w-full mt-1 font-mono text-xs resize-none"
            rows={5}
            value={form.param_space_text}
            onChange={(e) => setForm((f) => ({ ...f, param_space_text: e.target.value }))}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">搜索算法</label>
            <select className="select w-full mt-1" value={form.algorithm}
              onChange={(e) => setForm((f) => ({ ...f, algorithm: e.target.value as typeof f.algorithm }))}>
              {ALGORITHMS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label">损失函数</label>
            <select className="select w-full mt-1" value={form.loss_function}
              onChange={(e) => setForm((f) => ({ ...f, loss_function: e.target.value }))}>
              {(lossFns ?? [{ name: "sharpe", label: "夏普比率" }]).map((l) => (
                <option key={l.name} value={l.name}>{l.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">评估次数</label>
            <input className="input w-full mt-1 font-mono" type="number" min={5} max={300}
              value={form.n_trials}
              onChange={(e) => setForm((f) => ({ ...f, n_trials: parseInt(e.target.value) || 40 }))} />
          </div>
          <div>
            <label className="label">最少成交数</label>
            <input className="input w-full mt-1 font-mono" type="number" min={0} max={1000}
              value={form.min_trades}
              onChange={(e) => setForm((f) => ({ ...f, min_trades: parseInt(e.target.value) || 0 }))} />
          </div>
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🎯 开始优化"}
        </button>
      </form>

      {/* 结果 */}
      <div className="xl:col-span-2">
        {isPending && (
          <div className="card flex items-center justify-center h-48">
            <div className="text-center">
              <Spinner size="lg" className="mx-auto mb-3" />
              <p className="text-[#8b949e] text-sm">参数寻优中，请稍候…</p>
            </div>
          </div>
        )}

        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置参数空间后点击开始优化"
              description="贝叶斯优化以高斯过程代理引导搜索，比网格更高效" />
          </div>
        )}

        {result && !isPending && <HyperoptResult result={result} />}
      </div>
    </div>
  )
}

function HyperoptResult({ result }: { result: ReturnType<typeof useHyperopt>["data"] & object }) {
  if (!result) return null
  return (
    <div className="space-y-4">
      {/* 最优参数 */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[#e6edf3]">
            最优参数 <span className="text-xs text-[#6e7681] font-normal">({result.loss_function} · {result.algorithm})</span>
          </h3>
          {result.used_fallback && (
            <span className="text-[10px] text-[#d29922] bg-[#d29922]/10 border border-[#d29922]/30 rounded px-2 py-0.5">
              已退化为随机搜索
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-3 mb-3">
          {Object.entries(result.best_params).map(([k, v]) => (
            <div key={k} className="bg-[#1f6feb]/10 border border-[#1f6feb]/30 rounded px-3 py-1.5">
              <span className="text-xs text-[#58a6ff]">{k}</span>
              <span className="ml-2 font-mono font-bold text-[#e6edf3]">{String(v)}</span>
            </div>
          ))}
          <div className="bg-[#3fb950]/10 border border-[#3fb950]/30 rounded px-3 py-1.5">
            <span className="text-xs text-[#3fb950]">得分</span>
            <span className="ml-2 font-mono font-bold text-[#3fb950]">{result.best_score.toFixed(4)}</span>
          </div>
        </div>
        <p className="text-xs text-[#6e7681]">
          共评估 {result.evaluated} 次 / 参数空间约 {result.total_space.toLocaleString()} 种组合
        </p>
      </div>

      {/* 排行表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">寻优结果排行</h3>
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
              {result.trials.slice(0, 25).map((t, idx) => (
                <tr key={idx} className={`border-b border-[#21262d]/50 last:border-0 ${idx === 0 ? "bg-[#3fb950]/5" : "hover:bg-[#21262d]/30"}`}>
                  <td className="py-1.5 pr-3 text-center text-[#6e7681]">{idx + 1}</td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-[#e6edf3]">
                    {Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(", ")}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#58a6ff]">{t.score.toFixed(4)}</td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${t.total_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {t.total_return_pct >= 0 ? "+" : ""}{t.total_return_pct.toFixed(2)}%
                  </td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${t.annual_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {t.annual_return_pct >= 0 ? "+" : ""}{t.annual_return_pct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{t.sharpe_ratio.toFixed(3)}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#f85149]">{t.max_drawdown_pct.toFixed(2)}%</td>
                  <td className="py-1.5 text-right font-mono text-[#8b949e]">{t.total_trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
