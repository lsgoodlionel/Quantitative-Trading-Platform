// 财报日历 / 分红日历面板：两张结构相同的事件表，放在一起便于对照维护。
import { EmptyState } from "@/components/ui/EmptyState"
import {
  useDividendCalendar,
  useEarningsCalendar,
  type DividendEvent,
  type EarningsEvent,
} from "@/hooks/useMarketEvents"
import type { Market } from "@/types"
import { LoadingBlock, UpcomingBadge, Warnings, fmtDate, fmtNum, fmtPct, pctColor } from "./shared"

function EarningsRow({ e }: { e: EarningsEvent }) {
  return (
    <tr className="border-b border-[#21262d] hover:bg-[#161b22]">
      <td className="px-3 py-2 text-[#e6edf3]">
        {fmtDate(e.report_date)}
        {e.is_upcoming && <UpcomingBadge />}
      </td>
      <td className="px-3 py-2 text-[#8b949e]">{e.period ?? "—"}</td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.eps_estimate)}</td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.eps_actual)}</td>
      <td className={`px-3 py-2 text-right ${pctColor(e.surprise_percent)}`}>
        {fmtPct(e.surprise_percent)}
      </td>
    </tr>
  )
}

export function EarningsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useEarningsCalendar(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="财报日历加载失败" description="数据源暂不可用" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.events.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-[#21262d]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d1117] text-xs text-[#8b949e]">
              <tr>
                <th className="px-3 py-2 text-left font-medium">披露日</th>
                <th className="px-3 py-2 text-left font-medium">报告期</th>
                <th className="px-3 py-2 text-right font-medium">EPS 预期</th>
                <th className="px-3 py-2 text-right font-medium">EPS 实际</th>
                <th className="px-3 py-2 text-right font-medium">超预期</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e, i) => (
                <EarningsRow key={`${e.report_date}-${i}`} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="暂无财报日历" description="该标的无财报数据或数据源不覆盖" />
      )}
    </div>
  )
}

// ── 分红日历面板 ─────────────────────────────────────────────────

function DividendRow({ e }: { e: DividendEvent }) {
  return (
    <tr className="border-b border-[#21262d] hover:bg-[#161b22]">
      <td className="px-3 py-2 text-[#e6edf3]">
        {fmtDate(e.ex_dividend_date)}
        {e.is_upcoming && <UpcomingBadge />}
      </td>
      <td className="px-3 py-2 text-right text-[#e6edf3]">{fmtNum(e.amount, 4)}</td>
      <td className="px-3 py-2 text-right text-[#3fb950]">{fmtPct(e.dividend_yield, true)}</td>
      <td className="px-3 py-2 text-[#8b949e]">{fmtDate(e.record_date)}</td>
      <td className="px-3 py-2 text-[#8b949e]">{fmtDate(e.payment_date)}</td>
    </tr>
  )
}

export function DividendPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useDividendCalendar(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="分红日历加载失败" description="数据源暂不可用" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.events.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-[#21262d]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d1117] text-xs text-[#8b949e]">
              <tr>
                <th className="px-3 py-2 text-left font-medium">除息日</th>
                <th className="px-3 py-2 text-right font-medium">每股分红</th>
                <th className="px-3 py-2 text-right font-medium">股息率</th>
                <th className="px-3 py-2 text-left font-medium">登记日</th>
                <th className="px-3 py-2 text-left font-medium">派息日</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e, i) => (
                <DividendRow key={`${e.ex_dividend_date}-${i}`} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="暂无分红日历" description="该标的无分红记录或数据源不覆盖" />
      )}
    </div>
  )
}
