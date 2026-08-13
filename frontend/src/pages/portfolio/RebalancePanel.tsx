import { useEffect, useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import {
  useRebalancePreview,
  useRebalanceExecute,
  defaultLotSize,
  type RebalanceLeg,
  type RebalancePreviewResult,
  type RebalanceExecuteResult,
} from "@/hooks/useRebalance"

const REASON_LABEL: Record<RebalanceLeg["reason"], string> = {
  open: "建仓",
  close: "清仓",
  increase: "加仓",
  decrease: "减仓",
}

function money(v: number): string {
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

/** 权重和是否已归一（后端会拒绝不为 1 的输入，这里先拦一道给出人话提示） */
function weightSum(weights: Record<string, number>): number {
  return Object.values(weights).reduce((a, b) => a + b, 0)
}

// ── 子块 ──────────────────────────────────────────────────────

function LegsTable({ legs }: { legs: RebalanceLeg[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#6e7681] border-b border-[#30363d]">
            <th className="text-left font-medium py-1.5 pr-3">标的</th>
            <th className="text-left font-medium py-1.5 pr-3">方向</th>
            <th className="text-right font-medium py-1.5 pr-3">现有</th>
            <th className="text-right font-medium py-1.5 pr-3">目标</th>
            <th className="text-right font-medium py-1.5 pr-3">委托股数</th>
            <th className="text-right font-medium py-1.5 pr-3">现价</th>
            <th className="text-right font-medium py-1.5 pr-3">金额</th>
            <th className="text-right font-medium py-1.5">类型</th>
          </tr>
        </thead>
        <tbody>
          {legs.map((leg) => (
            <tr key={leg.symbol} className="border-b border-[#21262d]/50 last:border-0">
              <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{leg.symbol}</td>
              <td className="py-2 pr-3">
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                    leg.side === "BUY"
                      ? "bg-[#3fb950]/15 text-[#3fb950]"
                      : "bg-[#f85149]/15 text-[#f85149]"
                  }`}
                >
                  {leg.side === "BUY" ? "买入" : "卖出"}
                </span>
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">{leg.current_qty}</td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">{leg.target_qty}</td>
              <td className="py-2 pr-3 text-right font-mono text-[#e6edf3] font-semibold">
                {leg.delta_qty > 0 ? `+${leg.delta_qty}` : leg.delta_qty}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">{money(leg.price)}</td>
              <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                {money(Math.abs(leg.delta_value))}
              </td>
              <td className="py-2 text-right text-[10px] text-[#6e7681]">
                {REASON_LABEL[leg.reason] ?? leg.reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PreviewSummary({ preview }: { preview: RebalancePreviewResult }) {
  const items = [
    { label: "账户净值", value: money(preview.portfolio_value) },
    { label: "买入合计", value: money(preview.total_buy_value), tone: "text-[#3fb950]" },
    { label: "卖出合计", value: money(preview.total_sell_value), tone: "text-[#f85149]" },
    { label: "预估佣金", value: money(preview.estimated_commission) },
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {items.map((it) => (
        <div key={it.label} className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-[10px] text-[#6e7681]">{it.label}</p>
          <p className={`font-mono text-sm ${it.tone ?? "text-[#e6edf3]"}`}>{it.value}</p>
        </div>
      ))}
    </div>
  )
}

function ExecutionReport({ result }: { result: RebalanceExecuteResult }) {
  return (
    <div className="space-y-2">
      {result.submitted.length > 0 && (
        <div className="rounded-lg border border-[#3fb950]/25 bg-[#0d2018] p-3">
          <p className="text-xs font-semibold text-[#3fb950] mb-1.5">
            已提交 {result.submitted.length} 笔
          </p>
          <ul className="space-y-1 text-[11px] text-[#8b949e]">
            {result.submitted.map((o) => (
              <li key={o.order_id} className="font-mono">
                {o.symbol} · {o.side} {o.qty} · {o.status} · {o.order_id}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.rejected.length > 0 && (
        <div className="rounded-lg border border-[#f85149]/25 bg-[#1a0f0f] p-3">
          <p className="text-xs font-semibold text-[#f85149] mb-1.5">
            被拒 {result.rejected.length} 笔（已提交的部分不会撤回）
          </p>
          <ul className="space-y-1 text-[11px] text-[#8b949e]">
            {result.rejected.map((r) => (
              <li key={r.symbol}>
                <span className="font-mono text-[#e6edf3]">
                  {r.symbol} · {r.side} {r.qty}
                </span>
                <span className="ml-2 text-[#f85149]/80">{r.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── 主面板 ────────────────────────────────────────────────────

export function RebalancePanel({
  weights,
  market,
}: {
  weights: Record<string, number>
  market: string
}) {
  const preview = useRebalancePreview()
  const execute = useRebalanceExecute()
  const [minTradeValue, setMinTradeValue] = useState(0)
  const [lotSize, setLotSize] = useState(() => defaultLotSize(market))
  const [localError, setLocalError] = useState<string | null>(null)

  // 权重或市场一变，旧的预览（连同其 confirm_token）立即作废
  useEffect(() => {
    preview.reset()
    execute.reset()
    setLotSize(defaultLotSize(market))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weights, market])

  const data = preview.data
  const sum = weightSum(weights)
  const busy = preview.isPending || execute.isPending

  function handlePreview() {
    setLocalError(null)
    execute.reset()
    if (Math.abs(sum - 1) > 1e-4) {
      setLocalError(`目标权重之和为 ${sum.toFixed(4)}，需要等于 1 才能执行调仓`)
      return
    }
    preview.mutate({
      target_weights: weights,
      market,
      min_trade_value: minTradeValue,
      lot_size: lotSize,
    })
  }

  function handleExecute() {
    if (!data) return
    setLocalError(null)
    execute.mutate({
      market: data.market,
      legs: data.legs,
      confirm_token: data.confirm_token,
    })
  }

  return (
    <div className="card space-y-4 border-[#d29922]/30">
      <div>
        <h3 className="text-sm font-semibold text-[#e6edf3]">执行再平衡</h3>
        <p className="text-[11px] text-[#6e7681] mt-0.5">
          按优化权重与实时持仓算出增量委托，确认后逐笔提交到 OMS（真实资金，请核对明细）
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <div>
          <label className="label">最小交易金额</label>
          <input
            type="number"
            min={0}
            step={100}
            className="input w-full mt-1 font-mono"
            value={minTradeValue}
            onChange={(e) => setMinTradeValue(Number(e.target.value))}
          />
          <p className="text-[10px] text-[#6e7681] mt-1">低于该金额的碎单直接剔除</p>
        </div>
        <div>
          <label className="label">整手股数</label>
          <input
            type="number"
            min={1}
            step={1}
            className="input w-full mt-1 font-mono"
            value={lotSize}
            onChange={(e) => setLotSize(Math.max(1, Number(e.target.value)))}
          />
          <p className="text-[10px] text-[#6e7681] mt-1">港股/A 股通常为 100</p>
        </div>
        <button
          type="button"
          onClick={handlePreview}
          disabled={busy}
          className="btn-primary h-9 flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {preview.isPending && <Spinner size="sm" />}
          预览调仓
        </button>
      </div>

      {localError && (
        <p className="text-xs text-[#f85149] bg-[#1a0f0f] rounded-lg px-3 py-2">{localError}</p>
      )}
      {preview.error && (
        <p className="text-xs text-[#f85149] bg-[#1a0f0f] rounded-lg px-3 py-2">
          预览失败：{preview.error.message}
        </p>
      )}

      {data && (
        <div className="space-y-3">
          <PreviewSummary preview={data} />

          {data.warnings.map((w) => (
            <p key={w} className="text-[11px] text-[#d29922] bg-[#1c1810] rounded-lg px-3 py-2">
              ⚠ {w}
            </p>
          ))}

          {data.legs.length === 0 ? (
            <p className="text-xs text-[#8b949e] bg-[#0d1117] rounded-lg px-3 py-3">
              当前持仓已符合目标权重，无需调仓。
            </p>
          ) : (
            <>
              <LegsTable legs={data.legs} />
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <p className="text-[10px] text-[#6e7681]">
                  确认凭证 {data.expires_in_seconds} 秒内有效；超时或持仓变化后需重新预览
                </p>
                <button
                  type="button"
                  onClick={handleExecute}
                  disabled={busy}
                  className="btn-primary h-9 px-4 flex items-center gap-2 disabled:opacity-50"
                >
                  {execute.isPending && <Spinner size="sm" />}
                  确认执行 {data.legs.length} 笔委托
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {execute.error && (
        <p className="text-xs text-[#f85149] bg-[#1a0f0f] rounded-lg px-3 py-2">
          执行失败：{execute.error.message}（请重新预览后再试）
        </p>
      )}
      {execute.data && <ExecutionReport result={execute.data} />}
    </div>
  )
}
