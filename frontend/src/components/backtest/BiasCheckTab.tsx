import { useState } from "react"
import { useBiasCheck, type BiasCheckResult } from "@/hooks/useBacktestValidation"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { MARKET_CFGS, today, yearsAgo } from "./config"

interface StrategyOpt {
  name: string
  description: string
}

// ── Tab: 前视 / 递归偏差检测 ────────────────────────────────────
export function BiasCheckTab({ strategies }: { strategies: StrategyOpt[] }) {
  const { mutate: runCheck, isPending, data: result, error } = useBiasCheck()

  const [form, setForm] = useState({
    strategy_name: "double_ma",
    symbol: "AAPL",
    market: "US",
    frequency: "1d",
    start_date: yearsAgo(2),
    end_date: today(),
    initial_cash: 100000,
    params_text: '{"fast_period": 10, "slow_period": 30}',
    startup_text: "50, 100, 200",
    lookahead_cut_ratio: 0.7,
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    let params: Record<string, unknown>
    try {
      params = JSON.parse(form.params_text)
    } catch {
      alert("策略参数 JSON 格式错误")
      return
    }
    const startup_candles = form.startup_text
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0)
    runCheck({
      strategy_name: form.strategy_name,
      symbol: form.symbol,
      market: form.market,
      frequency: form.frequency,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_cash: form.initial_cash,
      params,
      startup_candles: startup_candles.length ? startup_candles : [50, 100, 200],
      lookahead_cut_ratio: form.lookahead_cut_ratio,
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      {/* 配置 */}
      <form onSubmit={handleSubmit} className="xl:col-span-1 card space-y-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">前视 / 递归偏差检测</h2>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">
          通过截断重跑对比成交序列，识别策略是否偷看未来数据、指标是否随历史长度漂移。
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
          <label className="label">策略参数 <span className="text-[10px] text-[#6e7681]">JSON（固定）</span></label>
          <textarea
            className="input w-full mt-1 font-mono text-xs resize-none"
            rows={2}
            value={form.params_text}
            onChange={(e) => setForm((f) => ({ ...f, params_text: e.target.value }))}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">递归裁剪 (bar)</label>
            <input className="input w-full mt-1 font-mono text-xs" value={form.startup_text}
              onChange={(e) => setForm((f) => ({ ...f, startup_text: e.target.value }))}
              placeholder="50, 100, 200" />
          </div>
          <div>
            <label className="label">前视保留比例</label>
            <input className="input w-full mt-1 font-mono" type="number" step={0.05} min={0.3} max={0.95}
              value={form.lookahead_cut_ratio}
              onChange={(e) => setForm((f) => ({ ...f, lookahead_cut_ratio: parseFloat(e.target.value) || 0.7 }))} />
          </div>
        </div>

        {error && (
          <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
            {error.message}
          </p>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "🔬 开始检测"}
        </button>
      </form>

      {/* 结果 */}
      <div className="xl:col-span-2">
        {isPending && (
          <div className="card flex items-center justify-center h-48">
            <div className="text-center">
              <Spinner size="lg" className="mx-auto mb-3" />
              <p className="text-[#8b949e] text-sm">截断重跑对比中…</p>
            </div>
          </div>
        )}

        {!isPending && !result && (
          <div className="card">
            <EmptyState title="配置后点击开始检测"
              description="健康的策略：截断未来数据不改变历史成交" />
          </div>
        )}

        {result && !isPending && <BiasCheckView result={result} />}
      </div>
    </div>
  )
}

function BiasCheckView({ result }: { result: BiasCheckResult }) {
  return (
    <div className="space-y-4">
      {/* 结论卡 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <VerdictCard title="前视偏差" ok={!result.has_lookahead_bias}
          okText="未偷看未来数据" badText="检测到前视偏差" />
        <VerdictCard title="递归偏差 / 起点敏感" ok={!result.has_recursive_bias}
          okText="信号对起点稳定" badText="检测到起点敏感" />
      </div>

      {/* 结论说明 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">检测结论</h3>
        <ul className="space-y-2">
          {result.notes.map((note, idx) => (
            <li key={idx} className="text-xs text-[#c9d1d9] leading-relaxed">{note}</li>
          ))}
        </ul>
        <p className="text-[11px] text-[#6e7681] mt-3">基准回测共产生 {result.total_signals} 笔成交。</p>
      </div>

      {/* 前视明细 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-2">前视偏差明细</h3>
        <div className="flex items-center gap-4 mb-2">
          <DiffBadge changed={result.lookahead.changed_signals} checked={result.lookahead.checked_signals} />
        </div>
        <p className="text-[11px] text-[#6e7681] leading-relaxed">{result.lookahead.detail}</p>
      </div>

      {/* 递归明细 */}
      {result.recursive.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">递归偏差明细（按裁剪 bar 数）</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#21262d]">
                  <th className="text-left py-2 pr-3">前端裁剪 bar</th>
                  <th className="text-right py-2 pr-3">比对成交数</th>
                  <th className="text-right py-2 pr-3">不一致数</th>
                  <th className="text-right py-2">判定</th>
                </tr>
              </thead>
              <tbody>
                {result.recursive.map((r) => (
                  <tr key={r.startup_candle} className="border-b border-[#21262d]/50 last:border-0">
                    <td className="py-1.5 pr-3 font-mono text-[#e6edf3]">{r.startup_candle}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{r.checked_signals}</td>
                    <td className={`py-1.5 pr-3 text-right font-mono ${r.changed_signals > 0 ? "text-[#f85149]" : "text-[#3fb950]"}`}>
                      {r.changed_signals}
                    </td>
                    <td className={`py-1.5 text-right font-mono ${r.changed_signals > 0 ? "text-[#f85149]" : "text-[#3fb950]"}`}>
                      {r.changed_signals > 0 ? "漂移" : "稳定"}
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

function VerdictCard({ title, ok, okText, badText }: { title: string; ok: boolean; okText: string; badText: string }) {
  return (
    <div className={`card border ${ok ? "border-[#3fb950]/40 bg-[#3fb950]/5" : "border-[#f85149]/40 bg-[#f85149]/5"}`}>
      <div className="flex items-center gap-3">
        <div className={`text-2xl ${ok ? "text-[#3fb950]" : "text-[#f85149]"}`}>{ok ? "✅" : "⚠️"}</div>
        <div>
          <div className="text-xs text-[#8b949e]">{title}</div>
          <div className={`text-sm font-semibold ${ok ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {ok ? okText : badText}
          </div>
        </div>
      </div>
    </div>
  )
}

function DiffBadge({ changed, checked }: { changed: number; checked: number }) {
  const bad = changed > 0
  return (
    <span className={`text-xs font-mono px-3 py-1 rounded border ${
      bad ? "text-[#f85149] border-[#f85149]/30 bg-[#f85149]/10" : "text-[#3fb950] border-[#3fb950]/30 bg-[#3fb950]/10"
    }`}>
      {changed} / {checked} 处不一致
    </span>
  )
}
