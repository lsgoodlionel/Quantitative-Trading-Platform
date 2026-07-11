import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import {
  useScreenerRun,
  useScreenerPresets,
  useScreenerSectors,
  useScreenerMovers,
  type ScreenerFilter,
  type ScreenerCandidate,
  type ScreenerSortKey,
} from "@/hooks/useScreener"
import { DynamicPairlist } from "@/pages/screener/DynamicPairlist"
import type { Market } from "@/types"

// ── 常量 ────────────────────────────────────────────────────────
const MARKETS: { value: Market; label: string; ccy: string }[] = [
  { value: "US", label: "美股", ccy: "$" },
  { value: "HK", label: "港股", ccy: "HK$" },
  { value: "A", label: "A股", ccy: "¥" },
]

const SORT_OPTIONS: { value: ScreenerSortKey; label: string }[] = [
  { value: "change_pct", label: "涨跌幅" },
  { value: "market_cap", label: "市值" },
  { value: "pe", label: "市盈率" },
  { value: "pb", label: "市净率" },
  { value: "dividend_yield", label: "股息率" },
  { value: "turnover", label: "成交额" },
  { value: "price", label: "价格" },
]

const DEFAULT_FILTER: ScreenerFilter = {
  market: "US",
  sectors: [],
  sort_by: "change_pct",
  sort_dir: "desc",
  limit: 50,
}

// ── 格式化工具 ──────────────────────────────────────────────────
function fmtPct(v: number | null): string {
  if (v == null) return "—"
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
}
function fmtNum(v: number | null, d = 2): string {
  return v == null ? "—" : v.toFixed(d)
}
function fmtCap(v: number | null): string {
  if (v == null) return "—"
  if (v >= 10000) return `${(v / 10000).toFixed(2)}万亿`
  return `${v.toFixed(0)}亿`
}
function fmtTurnover(v: number | null): string {
  if (v == null) return "—"
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return v.toFixed(0)
}
function pctColor(v: number | null): string {
  if (v == null) return "text-[#8b949e]"
  return v > 0 ? "text-[#3fb950]" : v < 0 ? "text-[#f85149]" : "text-[#e6edf3]"
}

// ── 数字区间输入 ─────────────────────────────────────────────────
interface RangeProps {
  label: string
  minKey: keyof ScreenerFilter
  maxKey?: keyof ScreenerFilter
  filter: ScreenerFilter
  onChange: (patch: Partial<ScreenerFilter>) => void
  step?: number
}

function RangeInput({ label, minKey, maxKey, filter, onChange, step = 1 }: RangeProps) {
  const inputCls =
    "w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-[#e6edf3] " +
    "focus:border-[#58a6ff] focus:outline-none placeholder:text-[#484f58]"
  const parse = (s: string): number | null => (s === "" ? null : Number(s))
  return (
    <div>
      <label className="block text-xs text-[#8b949e] mb-1">{label}</label>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          step={step}
          placeholder="最小"
          className={inputCls}
          value={(filter[minKey] as number | null | undefined) ?? ""}
          onChange={(e) => onChange({ [minKey]: parse(e.target.value) } as Partial<ScreenerFilter>)}
        />
        {maxKey && (
          <>
            <span className="text-[#484f58] text-xs">–</span>
            <input
              type="number"
              step={step}
              placeholder="最大"
              className={inputCls}
              value={(filter[maxKey] as number | null | undefined) ?? ""}
              onChange={(e) => onChange({ [maxKey]: parse(e.target.value) } as Partial<ScreenerFilter>)}
            />
          </>
        )}
      </div>
    </div>
  )
}

// ── 结果表格 ────────────────────────────────────────────────────
function ResultTable({
  rows,
  ccy,
  onGoMarket,
  onGoBacktest,
}: {
  rows: ScreenerCandidate[]
  ccy: string
  onGoMarket: (c: ScreenerCandidate) => void
  onGoBacktest: (c: ScreenerCandidate) => void
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm min-w-[880px]">
        <thead>
          <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
            <th className="text-left py-2 px-3">代码 / 名称</th>
            <th className="text-left py-2 px-2">行业</th>
            <th className="text-right py-2 px-2">现价</th>
            <th className="text-right py-2 px-2">涨跌幅</th>
            <th className="text-right py-2 px-2">市盈率</th>
            <th className="text-right py-2 px-2">市净率</th>
            <th className="text-right py-2 px-2">市值</th>
            <th className="text-right py-2 px-2">股息率</th>
            <th className="text-right py-2 px-2">成交额</th>
            <th className="text-right py-2 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={`${c.market}-${c.symbol}`} className="border-b border-[#21262d]/40 hover:bg-[#161b22]">
              <td className="py-2 px-3">
                <span className="font-mono text-[#58a6ff]">{c.symbol}</span>
                <span className="text-[#e6edf3] ml-2">{c.name}</span>
              </td>
              <td className="py-2 px-2">
                <span className="text-xs text-[#8b949e] bg-[#21262d] rounded px-1.5 py-0.5">{c.sector}</span>
              </td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">
                {c.price == null ? "—" : `${ccy}${fmtNum(c.price)}`}
              </td>
              <td className={`py-2 px-2 text-right font-mono ${pctColor(c.change_pct)}`}>{fmtPct(c.change_pct)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">{fmtNum(c.pe)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">{fmtNum(c.pb)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">{fmtCap(c.market_cap_yi)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e3b341]">
                {c.dividend_yield == null ? "—" : `${c.dividend_yield.toFixed(2)}%`}
              </td>
              <td className="py-2 px-2 text-right font-mono text-[#8b949e]">{fmtTurnover(c.turnover)}</td>
              <td className="py-2 px-3 text-right whitespace-nowrap">
                <button
                  onClick={() => onGoMarket(c)}
                  className="text-xs text-[#58a6ff] hover:underline mr-3"
                >
                  行情
                </button>
                <button
                  onClick={() => onGoBacktest(c)}
                  className="text-xs text-[#3fb950] hover:underline"
                >
                  回测
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 涨跌榜 ──────────────────────────────────────────────────────
function MoversPanel({ market, ccy }: { market: Market; ccy: string }) {
  const { data, isLoading, isError } = useScreenerMovers(market, 10)

  if (isLoading) return <div className="py-16 flex justify-center"><Spinner /></div>
  if (isError || !data) return <EmptyState title="涨跌榜加载失败" description="稍后重试" />

  const col = (title: string, list: ScreenerCandidate[], up: boolean) => (
    <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-4">
      <h3 className={`text-sm font-semibold mb-3 ${up ? "text-[#3fb950]" : "text-[#f85149]"}`}>{title}</h3>
      <div className="flex flex-col gap-0.5">
        {list.map((c, i) => (
          <div key={c.symbol} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-[#21262d]">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-xs text-[#484f58] w-4 text-right">{i + 1}</span>
              <span className="font-mono text-[#58a6ff] text-sm">{c.symbol}</span>
              <span className="text-[#e6edf3] text-sm truncate">{c.name}</span>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <span className="font-mono text-xs text-[#8b949e]">{c.price == null ? "—" : `${ccy}${fmtNum(c.price)}`}</span>
              <span className={`font-mono text-sm w-16 text-right ${pctColor(c.change_pct)}`}>{fmtPct(c.change_pct)}</span>
            </div>
          </div>
        ))}
        {list.length === 0 && <p className="text-[#8b949e] text-sm py-4 text-center">暂无数据</p>}
      </div>
    </div>
  )

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {col("涨幅榜 Top 10", data.gainers, true)}
      {col("跌幅榜 Top 10", data.losers, false)}
    </div>
  )
}

// ── 主页面 ──────────────────────────────────────────────────────
export function Screener() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [filter, setFilter] = useState<ScreenerFilter>(DEFAULT_FILTER)
  const [tab, setTab] = useState<"screen" | "movers" | "pairlist">("screen")

  const presetsQ = useScreenerPresets()
  const sectorsQ = useScreenerSectors()
  const runM = useScreenerRun()

  const ccy = useMemo(
    () => MARKETS.find((m) => m.value === filter.market)?.ccy ?? "$",
    [filter.market],
  )

  const patch = (p: Partial<ScreenerFilter>) => setFilter((prev) => ({ ...prev, ...p }))

  const setMarket = (market: Market) => setFilter((prev) => ({ ...prev, market }))

  const toggleSector = (s: string) =>
    setFilter((prev) => ({
      ...prev,
      sectors: prev.sectors.includes(s)
        ? prev.sectors.filter((x) => x !== s)
        : [...prev.sectors, s],
    }))

  const runScreen = (f: ScreenerFilter) => {
    runM.mutate(f, {
      onError: (e) => toast(`筛选失败: ${e.message}`, "error"),
    })
  }

  const applyPreset = (criteria: Partial<ScreenerFilter>) => {
    const next: ScreenerFilter = { ...DEFAULT_FILTER, market: filter.market, ...criteria, sectors: [] }
    setFilter(next)
    runScreen(next)
  }

  const reset = () => setFilter({ ...DEFAULT_FILTER, market: filter.market })

  const goMarket = (c: ScreenerCandidate) =>
    navigate(`/market?symbol=${encodeURIComponent(c.symbol)}&market=${c.market}`)
  const goBacktest = (c: ScreenerCandidate) =>
    navigate(`/backtest?symbol=${encodeURIComponent(c.symbol)}&market=${c.market}`)

  const result = runM.data

  return (
    <AppShell title="股票筛选器">
      {/* 市场 + 视图切换 */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="flex gap-1 bg-[#161b22] rounded-lg p-1 border border-[#21262d]">
          {MARKETS.map((m) => (
            <button
              key={m.value}
              onClick={() => setMarket(m.value)}
              className={`px-4 py-1.5 rounded-md text-sm transition-colors ${
                filter.market === m.value
                  ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                  : "text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-[#161b22] rounded-lg p-1 border border-[#21262d]">
          {(["screen", "movers", "pairlist"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 rounded-md text-sm transition-colors ${
                tab === t ? "bg-[#1f6feb]/20 text-[#58a6ff]" : "text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {t === "screen" ? "条件筛选" : t === "movers" ? "涨跌榜" : "动态标的池"}
            </button>
          ))}
        </div>
      </div>

      {tab === "movers" ? (
        <MoversPanel market={filter.market} ccy={ccy} />
      ) : tab === "pairlist" ? (
        <DynamicPairlist market={filter.market} />
      ) : (
        <>
          {/* 预设方案 */}
          <div className="mb-5">
            <p className="text-xs text-[#8b949e] mb-2">预设方案（一键套用并筛选）</p>
            <div className="flex flex-wrap gap-2">
              {presetsQ.data?.map((p) => (
                <button
                  key={p.id}
                  onClick={() => applyPreset(p.criteria)}
                  title={p.desc}
                  className="px-3 py-1.5 rounded-lg border border-[#30363d] bg-[#161b22] text-sm text-[#e6edf3] hover:border-[#58a6ff] hover:text-[#58a6ff] transition-colors"
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>

          {/* 条件表单 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-4 mb-5">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <RangeInput label="市值（亿）" minKey="min_market_cap_yi" maxKey="max_market_cap_yi" filter={filter} onChange={patch} step={10} />
              <RangeInput label="市盈率 PE" minKey="min_pe" maxKey="max_pe" filter={filter} onChange={patch} />
              <RangeInput label="市净率 PB" minKey="min_pb" maxKey="max_pb" filter={filter} onChange={patch} step={0.1} />
              <RangeInput label="涨跌幅 %" minKey="min_change_pct" maxKey="max_change_pct" filter={filter} onChange={patch} step={0.5} />
              <RangeInput label={`价格 ${ccy}`} minKey="min_price" maxKey="max_price" filter={filter} onChange={patch} />
              <RangeInput label="最低股息率 %" minKey="min_dividend_yield" filter={filter} onChange={patch} step={0.5} />
              <RangeInput label="最低成交量" minKey="min_volume" filter={filter} onChange={patch} step={1000} />
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">排序</label>
                <div className="flex gap-1.5">
                  <select
                    className="flex-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-[#e6edf3] focus:border-[#58a6ff] focus:outline-none"
                    value={filter.sort_by}
                    onChange={(e) => patch({ sort_by: e.target.value as ScreenerSortKey })}
                  >
                    {SORT_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => patch({ sort_dir: filter.sort_dir === "desc" ? "asc" : "desc" })}
                    className="px-2.5 rounded border border-[#30363d] bg-[#0d1117] text-sm text-[#8b949e] hover:text-[#e6edf3]"
                    title={filter.sort_dir === "desc" ? "降序" : "升序"}
                  >
                    {filter.sort_dir === "desc" ? "↓" : "↑"}
                  </button>
                </div>
              </div>
            </div>

            {/* 行业 */}
            <div className="mt-4">
              <label className="block text-xs text-[#8b949e] mb-2">行业（不选=全部）</label>
              <div className="flex flex-wrap gap-1.5">
                {sectorsQ.data?.map((s) => (
                  <button
                    key={s}
                    onClick={() => toggleSector(s)}
                    className={`px-2.5 py-1 rounded text-xs border transition-colors ${
                      filter.sectors.includes(s)
                        ? "border-[#58a6ff] bg-[#1f6feb]/20 text-[#58a6ff]"
                        : "border-[#30363d] bg-[#0d1117] text-[#8b949e] hover:text-[#e6edf3]"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            {/* 操作 */}
            <div className="flex items-center gap-3 mt-4">
              <button
                onClick={() => runScreen(filter)}
                disabled={runM.isPending}
                className="px-5 py-2 rounded-lg bg-[#238636] text-white text-sm font-medium hover:bg-[#2ea043] disabled:opacity-50 transition-colors"
              >
                {runM.isPending ? "筛选中…" : "开始筛选"}
              </button>
              <button
                onClick={reset}
                className="px-4 py-2 rounded-lg border border-[#30363d] text-sm text-[#8b949e] hover:text-[#e6edf3] transition-colors"
              >
                重置
              </button>
              {result && (
                <span className="text-xs text-[#8b949e]">
                  匹配 {result.count} / {result.universe_size} 只
                </span>
              )}
            </div>
          </div>

          {/* 结果 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-4">
            {runM.isPending ? (
              <div className="py-16 flex justify-center"><Spinner /></div>
            ) : !result ? (
              <EmptyState title="设置条件后开始筛选" description="或点击上方预设方案快速开始" />
            ) : result.candidates.length === 0 ? (
              <EmptyState title="无匹配标的" description="放宽条件后重试（部分标的基本面数据可能缺失）" />
            ) : (
              <ResultTable rows={result.candidates} ccy={ccy} onGoMarket={goMarket} onGoBacktest={goBacktest} />
            )}
          </div>
        </>
      )}
    </AppShell>
  )
}
