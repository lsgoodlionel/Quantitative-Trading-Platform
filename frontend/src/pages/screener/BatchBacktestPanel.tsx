import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { today, yearsAgo } from "@/components/backtest/config"
import { usePresets } from "@/hooks/useStrategy"
import { useBatchBacktest, type BatchBacktestItem } from "@/hooks/useBatchBacktest"
import type { Market } from "@/types"

const DEFAULT_STRATEGY = "double_ma"
const DEFAULT_CASH = 100_000

interface BatchBacktestPanelProps {
  symbols: string[]
  market: Market
}

/**
 * 批量回测面板：同一策略跑一批标的，结果按夏普排序。
 *
 * 后端保证「单个标的失败不拖垮整批」，失败项带 error 字段返回 —— 这里
 * 必须把失败项也渲染出来，否则用户会以为标的被静默丢了。
 */
export function BatchBacktestPanel({ symbols, market }: BatchBacktestPanelProps) {
  const presetsQ = usePresets()
  const runM = useBatchBacktest()
  const [strategy, setStrategy] = useState(DEFAULT_STRATEGY)
  const [startDate, setStartDate] = useState(yearsAgo(2))
  const [endDate, setEndDate] = useState(today())

  const run = () => {
    runM.mutate({
      symbols,
      strategy_name: strategy,
      market,
      frequency: "1d",
      start_date: startDate,
      end_date: endDate,
      initial_cash: DEFAULT_CASH,
      params: {},
    })
  }

  const result = runM.data
  const sorted = result
    ? [...result.results].sort((a, b) => rankScore(b) - rankScore(a))
    : []

  const inputCls =
    "bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-[#e6edf3] " +
    "focus:border-[#58a6ff] focus:outline-none"

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[#8b949e]">策略</span>
          <select
            aria-label="策略"
            className={inputCls}
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            {(presetsQ.data ?? [{ name: DEFAULT_STRATEGY, description: "" }]).map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[#8b949e]">开始</span>
          <input
            aria-label="开始日期"
            type="date"
            className={inputCls}
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[#8b949e]">结束</span>
          <input
            aria-label="结束日期"
            type="date"
            className={inputCls}
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
        <button
          onClick={run}
          disabled={runM.isPending}
          className="rounded-lg bg-[#238636] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2ea043] disabled:opacity-50"
        >
          {runM.isPending ? "回测中…" : `开始回测（${symbols.length} 只）`}
        </button>
      </div>

      {runM.isPending && <div className="flex justify-center py-10"><Spinner /></div>}

      {runM.isError && (
        <EmptyState title="批量回测失败" description={runM.error.message} />
      )}

      {result && !runM.isPending && (
        <>
          <p className="text-xs text-[#8b949e]">
            成功 <span className="text-[#3fb950]">{result.succeeded}</span> ·
            失败 <span className="text-[#f85149]"> {result.failed}</span> ·
            共 {result.total} 只
          </p>
          <BatchResultTable rows={sorted} />
        </>
      )}
    </div>
  )
}

/** 排序键：成功项按夏普，失败项永远沉底 */
function rankScore(item: BatchBacktestItem): number {
  return item.metrics ? item.metrics.sharpe_ratio : Number.NEGATIVE_INFINITY
}

function pctClass(v: number): string {
  return v > 0 ? "text-[#3fb950]" : v < 0 ? "text-[#f85149]" : "text-[#e6edf3]"
}

function BatchResultTable({ rows }: { rows: BatchBacktestItem[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[600px] text-sm">
        <thead>
          <tr className="border-b border-[#21262d] text-xs text-[#8b949e]">
            <th className="px-2 py-2 text-left">标的</th>
            <th className="px-2 py-2 text-right">总收益</th>
            <th className="px-2 py-2 text-right">夏普</th>
            <th className="px-2 py-2 text-right">最大回撤</th>
            <th className="px-2 py-2 text-right">胜率</th>
            <th className="px-2 py-2 text-right">交易数</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => <BatchResultRow key={r.symbol} item={r} />)}
        </tbody>
      </table>
    </div>
  )
}

function BatchResultRow({ item }: { item: BatchBacktestItem }) {
  const m = item.metrics
  return (
    <tr data-testid="batch-result-row" className="border-b border-[#21262d]/40">
      <td className="px-2 py-2 font-mono text-[#58a6ff]">{item.symbol}</td>
      {m == null ? (
        <td colSpan={5} className="px-2 py-2 text-xs text-[#f85149]">
          ⚠ {item.error ?? "未知错误"}
        </td>
      ) : (
        <>
          <td className={`px-2 py-2 text-right font-mono ${pctClass(m.total_return_pct)}`}>
            {m.total_return_pct.toFixed(2)}%
          </td>
          <td className="px-2 py-2 text-right font-mono text-[#e6edf3]">
            {m.sharpe_ratio.toFixed(2)}
          </td>
          <td className="px-2 py-2 text-right font-mono text-[#f85149]">
            {m.max_drawdown_pct.toFixed(2)}%
          </td>
          <td className="px-2 py-2 text-right font-mono text-[#e6edf3]">
            {m.win_rate_pct.toFixed(1)}%
          </td>
          <td className="px-2 py-2 text-right font-mono text-[#8b949e]">
            {m.total_trades}
          </td>
        </>
      )}
    </tr>
  )
}
