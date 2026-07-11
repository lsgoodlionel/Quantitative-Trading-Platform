import { useState } from "react"
import { useWalkForward, useLossFunctions, type ParamSpaceDef, type WalkForwardResult } from "@/hooks/useBacktestValidation"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { MARKET_CFGS, today, yearsAgo } from "./config"

interface StrategyOpt {
  name: string
  description: string
}

const DEFAULT_SPACE = '{\n  "fast_period": [5, 10, 15, 20],\n  "slow_period": [30, 50, 80]\n}'

// ── Tab: Walk-Forward 滚动样本内外验证 ──────────────────────────
export function WalkForwardTab({ strategies }: { strategies: StrategyOpt[] }) {
  const { mutate: runWf, isPending, data: result, error } = useWalkForward()
  const { data: lossFns } = useLossFunctions()

  const [form, setForm] = useState({
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(4),
    end_date: today(),
    initial_cash: 100000,
    train_size: 250,
    test_size: 60,
    mode: "rolling" as "rolling" | "anchored",
    algorithm: "grid" as "grid" | "random" | "bayesian",
    loss_function: "sharpe",
    inner_trials: 24,
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
    const { param_space_text, ...rest } = form
    void param_space_text
    runWf({ ...rest, param_space })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      {/* 配置 */}
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">Walk-Forward 分析</h2>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">
          滚动训练窗口寻优 + 紧邻测试窗口样本外验证，衡量抗曲线拟合能力。
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

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">训练窗口 (bar)</label>
            <input className="input w-full mt-1 font-mono" type="number" min={20} max={2000}
              value={form.train_size}
              onChange={(e) => setForm((f) => ({ ...f, train_size: parseInt(e.target.value) || 250 }))} />
          </div>
          <div>
            <label className="label">测试窗口 (bar)</label>
            <input className="input w-full mt-1 font-mono" type="number" min={5} max={1000}
              value={form.test_size}
              onChange={(e) => setForm((f) => ({ ...f, test_size: parseInt(e.target.value) || 60 }))} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">窗口模式</label>
            <select className="select w-full mt-1" value={form.mode}
              onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value as typeof f.mode }))}>
              <option value="rolling">滚动 (rolling)</option>
              <option value="anchored">锚定扩张 (anchored)</option>
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

        <div>
          <label className="label">
            参数空间 <span className="text-[10px] text-[#6e7681]">JSON，训练窗口内寻优</span>
          </label>
          <textarea
            className="input w-full mt-1 font-mono text-xs resize-none"
            rows={4}
            value={form.param_space_text}
            onChange={(e) => setForm((f) => ({ ...f, param_space_text: e.target.value }))}
          />
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🔁 开始 Walk-Forward"}
        </button>
      </form>

      {/* 结果 */}
      <div className="xl:col-span-2">
        {isPending && (
          <div className="card flex items-center justify-center h-48">
            <div className="text-center">
              <Spinner size="lg" className="mx-auto mb-3" />
              <p className="text-[#8b949e] text-sm">滚动窗口寻优与验证中，可能耗时较长…</p>
            </div>
          </div>
        )}

        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置窗口大小后点击开始"
              description="将数据切成多段，每段训练寻优再样本外测试，暴露过拟合" />
          </div>
        )}

        {result && !isPending && <WalkForwardView result={result} />}
      </div>
    </div>
  )
}

function effColor(eff: number): string {
  if (eff >= 0.7) return "text-[#3fb950]"
  if (eff >= 0.4) return "text-[#d29922]"
  return "text-[#f85149]"
}

function WalkForwardView({ result }: { result: WalkForwardResult }) {
  return (
    <div className="space-y-4">
      {/* 汇总 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          样本内外汇总 <span className="text-xs text-[#6e7681] font-normal">({result.total_windows} 个窗口 · {result.mode})</span>
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SummaryStat label="样本内夏普 (IS)" value={result.avg_is_sharpe.toFixed(3)} tone="neutral" />
          <SummaryStat label="样本外夏普 (OOS)" value={result.avg_oos_sharpe.toFixed(3)}
            tone={result.avg_oos_sharpe >= 0 ? "good" : "bad"} />
          <SummaryStat label="OOS/IS 效率" value={result.oos_is_efficiency.toFixed(2)} className={effColor(result.oos_is_efficiency)} />
          <SummaryStat label="OOS 一致性" value={`${(result.oos_consistency * 100).toFixed(0)}%`}
            tone={result.oos_consistency >= 0.5 ? "good" : "bad"} />
        </div>
        <p className="text-[11px] text-[#6e7681] mt-3 leading-relaxed">
          OOS/IS 效率越接近 1 说明样本外衰减越小；一致性为样本外正收益窗口占比。
          {result.oos_is_efficiency < 0.4 && (
            <span className="text-[#f85149]"> ⚠️ 样本外效率偏低，策略可能存在过拟合。</span>
          )}
        </p>
      </div>

      {/* 逐窗口表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">逐窗口明细</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[#8b949e] border-b border-[#21262d]">
                <th className="text-center py-2 pr-3 w-8">#</th>
                <th className="text-left py-2 pr-3">测试区间</th>
                <th className="text-left py-2 pr-3">最优参数</th>
                <th className="text-right py-2 pr-3">IS 夏普</th>
                <th className="text-right py-2 pr-3">OOS 夏普</th>
                <th className="text-right py-2 pr-3">OOS 收益</th>
                <th className="text-right py-2 pr-3">OOS 回撤</th>
                <th className="text-right py-2">交易数</th>
              </tr>
            </thead>
            <tbody>
              {result.windows.map((w) => (
                <tr key={w.index} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                  <td className="py-1.5 pr-3 text-center text-[#6e7681]">{w.index + 1}</td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-[#8b949e]">
                    {w.test_start.slice(0, 10)} ~ {w.test_end.slice(0, 10)}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-[#e6edf3]">
                    {Object.entries(w.best_params).map(([k, v]) => `${k}=${v}`).join(", ")}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{w.is_sharpe.toFixed(3)}</td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${w.oos_sharpe >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {w.oos_sharpe.toFixed(3)}
                  </td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${w.oos_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {w.oos_return_pct >= 0 ? "+" : ""}{w.oos_return_pct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#f85149]">{w.oos_max_drawdown_pct.toFixed(2)}%</td>
                  <td className="py-1.5 text-right font-mono text-[#8b949e]">{w.oos_total_trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

interface SummaryStatProps {
  label: string
  value: string
  tone?: "good" | "bad" | "neutral"
  className?: string
}

function SummaryStat({ label, value, tone = "neutral", className }: SummaryStatProps) {
  const toneColor =
    className ?? (tone === "good" ? "text-[#3fb950]" : tone === "bad" ? "text-[#f85149]" : "text-[#e6edf3]")
  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2.5">
      <div className="text-[10px] text-[#6e7681] mb-1">{label}</div>
      <div className={`font-mono font-bold text-lg ${toneColor}`}>{value}</div>
    </div>
  )
}
