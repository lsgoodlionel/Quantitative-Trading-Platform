import type { CopilotCard } from "@/hooks/useCopilot"

/**
 * 只读工具的结构化结果卡片。
 *
 * 后端把完整数据放在 `card.data`（回灌给模型的是裁剪版），这里按 `kind` 挑渲染器。
 * 遇到不认识的 kind 不隐藏、不报错 —— 退化成键值列表，宁可丑一点也别让数据消失。
 */

const KIND_TITLES: Record<string, string> = {
  quote: "最新行情",
  screener: "筛选结果",
  backtest: "回测结果",
  positions: "当前持仓",
  account: "账户资金",
  backtest_history: "回测记录",
}

const QUOTE_ROWS: [string, string][] = [
  ["open", "开盘"],
  ["high", "最高"],
  ["low", "最低"],
  ["close", "收盘"],
  ["volume", "成交量"],
]

export function ResultCard({ card }: { card: CopilotCard }) {
  return (
    <section
      className="rounded-md border border-[#30363d] bg-[#0d1117] p-3 space-y-2"
      aria-label={KIND_TITLES[card.kind] ?? card.title}
    >
      <h3 className="text-[11px] uppercase tracking-wide text-[#8b949e]">
        {KIND_TITLES[card.kind] ?? card.title}
      </h3>
      <CardBody card={card} />
    </section>
  )
}

function CardBody({ card }: { card: CopilotCard }) {
  if (card.kind === "quote") return <KeyValues data={card.data} rows={QUOTE_ROWS} />
  if (card.kind === "account") {
    return (
      <KeyValues
        data={card.data}
        rows={[
          ["cash", "现金"],
          ["buying_power", "购买力"],
          ["portfolio_value", "净值"],
        ]}
      />
    )
  }
  if (card.kind === "positions") return <PositionRows data={card.data} />
  if (card.kind === "screener") return <ScreenerRows data={card.data} />
  if (card.kind === "backtest" || card.kind === "backtest_history") {
    return <MetricRows data={card.data} />
  }
  return <KeyValues data={card.data} rows={Object.keys(card.data).map((k) => [k, k])} />
}

function KeyValues({
  data,
  rows,
}: {
  data: Record<string, unknown>
  rows: [string, string][]
}) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
      {rows
        .filter(([key]) => data[key] !== undefined && data[key] !== null)
        .map(([key, label]) => (
          <div key={key} className="contents">
            <dt className="text-[11px] text-[#8b949e]">{label}</dt>
            <dd className="text-[11px] text-right font-mono text-[#c9d1d9]">
              {formatValue(data[key])}
            </dd>
          </div>
        ))}
    </dl>
  )
}

function PositionRows({ data }: { data: Record<string, unknown> }) {
  const rows = asRows(data.positions)
  if (rows.length === 0) return <Empty text="没有持仓" />
  return (
    <SimpleTable
      head={["标的", "股数", "成本", "浮动盈亏"]}
      rows={rows.map((p) => [
        String(p.symbol ?? "-"),
        formatValue(p.qty),
        formatValue(p.avg_cost),
        formatValue(p.unrealized_pnl),
      ])}
    />
  )
}

function ScreenerRows({ data }: { data: Record<string, unknown> }) {
  const rows = asRows(data.candidates)
  if (rows.length === 0) return <Empty text="没有匹配的标的" />
  return (
    <>
      <SimpleTable
        head={["标的", "名称", "涨跌幅", "市盈率"]}
        rows={rows.slice(0, 20).map((c) => [
          String(c.symbol ?? "-"),
          String(c.name ?? "-"),
          formatValue(c.change_pct),
          formatValue(c.pe),
        ])}
      />
      {rows.length > 20 && (
        <p className="text-[10px] text-[#6e7681]">
          共 {rows.length} 条，这里只列前 20 条。
        </p>
      )}
    </>
  )
}

function MetricRows({ data }: { data: Record<string, unknown> }) {
  const metrics = (data.metrics ?? {}) as Record<string, unknown>
  return (
    <KeyValues
      data={{ ...metrics, final_value: data.final_value }}
      rows={[
        ["total_return_pct", "总收益 %"],
        ["annual_return_pct", "年化 %"],
        ["sharpe_ratio", "夏普"],
        ["max_drawdown_pct", "最大回撤 %"],
        ["win_rate_pct", "胜率 %"],
        ["total_trades", "交易次数"],
        ["final_value", "期末净值"],
      ]}
    />
  )
}

function SimpleTable({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="max-h-52 overflow-y-auto rounded border border-[#21262d]">
      <table className="w-full text-[11px]">
        <thead className="text-[#8b949e]">
          <tr>
            {head.map((h, i) => (
              <th
                key={h}
                className={`px-2 py-1 font-normal ${i === 0 ? "text-left" : "text-right"}`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono text-[#c9d1d9]">
          {rows.map((cells, index) => (
            <tr key={`${cells[0]}-${index}`} className="border-t border-[#21262d]">
              {cells.map((cell, i) => (
                <td
                  key={head[i]}
                  className={`px-2 py-1 ${i === 0 ? "text-left" : "text-right"}`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <p className="text-[11px] text-[#6e7681]">{text}</p>
}

function asRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : []
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "-"
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2)
  }
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
