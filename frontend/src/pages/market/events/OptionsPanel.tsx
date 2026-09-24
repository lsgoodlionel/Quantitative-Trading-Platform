// 期权链面板：到期日选择 + Calls/Puts 两张表（含本地 BSM Greeks；仅美股）。
import { useState } from "react"
import { EmptyState } from "@/components/ui/EmptyState"
import {
  useOptionChain,
  useOptionExpirations,
  type OptionContract,
} from "@/hooks/useMarketEvents"
import type { Market } from "@/types"
import { LoadingBlock, Warnings, fmtInt, fmtNum, fmtPct, pctColor } from "./shared"

const OPTION_COLS = [
  "行权价", "最新", "买价", "卖价", "涨跌%", "成交", "OI", "IV", "Δ", "Γ", "Θ", "ν",
]

function OptionRow({ c }: { c: OptionContract }) {
  return (
    <tr
      className={`border-b border-[#21262d] hover:bg-[#161b22] ${
        c.in_the_money ? "bg-[#1f6feb]/5" : ""
      }`}
    >
      <td className="px-2 py-1.5 font-medium text-[#e6edf3]">{fmtNum(c.strike)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.last_price)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtNum(c.bid)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtNum(c.ask)}</td>
      <td className={`px-2 py-1.5 text-right ${pctColor(c.percent_change)}`}>
        {fmtPct(c.percent_change)}
      </td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtInt(c.volume)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtInt(c.open_interest)}</td>
      <td className="px-2 py-1.5 text-right text-[#8b949e]">{fmtPct(c.implied_volatility, true)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.delta, 3)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.gamma, 4)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.theta, 4)}</td>
      <td className="px-2 py-1.5 text-right text-[#e6edf3]">{fmtNum(c.vega, 4)}</td>
    </tr>
  )
}

function OptionTable({ title, rows }: { title: string; rows: OptionContract[] }) {
  return (
    <div>
      <div className="mb-1.5 text-sm font-medium text-[#e6edf3]">{title}</div>
      <div className="overflow-x-auto rounded-lg border border-[#21262d]">
        <table className="w-full text-xs">
          <thead className="bg-[#0d1117] text-[#8b949e]">
            <tr>
              {OPTION_COLS.map((col) => (
                <th key={col} className="px-2 py-1.5 text-right font-medium first:text-left">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((c, i) => (
              <OptionRow key={c.contract_symbol ?? i} c={c} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function OptionsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const isUS = market === "US"
  const expQuery = useOptionExpirations(symbol, isUS)
  const [expiration, setExpiration] = useState<string | null>(null)
  const activeExp = expiration ?? expQuery.data?.expirations[0] ?? null
  const chainQuery = useOptionChain(symbol, activeExp, isUS)

  if (!isUS) {
    return <EmptyState title="仅支持美股期权" description="期权链数据来自 yfinance，目前仅覆盖美股标的" />
  }
  if (expQuery.isLoading) return <LoadingBlock />
  if (expQuery.isError)
    return <EmptyState title="期权数据加载失败" description="数据源暂不可用" />

  const expirations = expQuery.data?.expirations ?? []
  if (!expirations.length) {
    return (
      <div>
        <Warnings items={expQuery.data?.warnings ?? []} />
        <EmptyState title="无期权链" description="该标的无期权或非期权标的" />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#8b949e]">到期日</span>
          <select
            value={activeExp ?? ""}
            onChange={(e) => setExpiration(e.target.value)}
            className="rounded-md border border-[#30363d] bg-[#0d1117] px-2 py-1 text-sm text-[#e6edf3] focus:border-[#58a6ff] focus:outline-none"
          >
            {expirations.map((exp) => (
              <option key={exp} value={exp}>
                {exp}
              </option>
            ))}
          </select>
        </div>
        {chainQuery.data?.underlying_price != null && (
          <span className="text-xs text-[#8b949e]">
            标的现价 <span className="text-[#e6edf3]">{fmtNum(chainQuery.data.underlying_price)}</span>
          </span>
        )}
        <span className="text-xs text-[#484f58]">
          Greeks 无风险利率 {fmtPct((chainQuery.data?.risk_free_rate ?? 0) * 100)}
        </span>
      </div>

      <Warnings items={chainQuery.data?.warnings ?? []} />

      {chainQuery.isLoading ? (
        <LoadingBlock />
      ) : chainQuery.data ? (
        <div className="space-y-4">
          <OptionTable title="🟢 看涨 Calls" rows={chainQuery.data.calls} />
          <OptionTable title="🔴 看跌 Puts" rows={chainQuery.data.puts} />
        </div>
      ) : (
        <EmptyState title="无期权链数据" description="请选择其它到期日" />
      )}
    </div>
  )
}
