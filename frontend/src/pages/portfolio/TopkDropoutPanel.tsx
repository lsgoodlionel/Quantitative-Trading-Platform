import { useState } from "react"
import { format, subYears } from "date-fns"
import { Spinner } from "@/components/ui/Spinner"
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts"
import {
  useTopkPortfolio,
  type TopkScoreMethod, type TopkResult,
} from "@/hooks/useTopkPortfolio"

// ── 常量 ──────────────────────────────────────────────────────────

const MARKET_DEFAULTS: Record<string, string[]> = {
  US: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V", "UNH"],
  HK: ["00700", "09988", "03690", "01810", "00981", "02318", "00388", "01299"],
  A: ["600519", "601318", "600036", "000858", "600276", "300750", "002594", "601899"],
}

const SCORE_OPTIONS: { value: TopkScoreMethod; label: string; desc: string }[] = [
  { value: "momentum",            label: "动量",         desc: "回看窗口累计收益，高者优先" },
  { value: "reversal",            label: "反转",         desc: "近期跌得多者优先（均值回归）" },
  { value: "vol_scaled_momentum", label: "波动缩放动量", desc: "动量 / 波动，风险调整后择优" },
]

const CHART_GRID = "#21262d"
const AXIS_TICK = { fill: "#8b949e", fontSize: 10 }
const TOOLTIP_STYLE = { background: "#161b22", border: "1px solid #30363d", fontSize: 12 }

function today() { return format(new Date(), "yyyy-MM-dd") }
function yearsAgo(n: number) { return format(subYears(new Date(), n), "yyyy-MM-dd") }
function pct(v: number) { return `${(v * 100).toFixed(2)}%` }

function parseSymbols(text: string): string[] {
  return text.split(/[\s,，、]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)
}

// ── 小组件 ────────────────────────────────────────────────────────

interface MetricTileProps { label: string; value: string; accent?: "up" | "down" | "none" }
function MetricTile({ label, value, accent = "none" }: MetricTileProps) {
  const color = accent === "up" ? "#3fb950" : accent === "down" ? "#f85149" : "#e6edf3"
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-3">
      <p className="text-[10px] text-[#8b949e] mb-1">{label}</p>
      <p className="font-mono text-sm font-semibold" style={{ color }}>{value}</p>
    </div>
  )
}

// ── 主面板 ────────────────────────────────────────────────────────

export function TopkDropoutPanel() {
  const { mutate: run, isPending, data: result, error } = useTopkPortfolio()

  const [market, setMarket] = useState("US")
  const [symbolsText, setSymbolsText] = useState(MARKET_DEFAULTS.US.join(", "))
  const [startDate, setStartDate] = useState(yearsAgo(2))
  const [endDate, setEndDate] = useState(today())
  const [scoreMethod, setScoreMethod] = useState<TopkScoreMethod>("momentum")
  const [lookback, setLookback] = useState("20")
  const [rebalanceDays, setRebalanceDays] = useState("5")
  const [topk, setTopk] = useState("5")
  const [nDrop, setNDrop] = useState("1")
  const [holdThresh, setHoldThresh] = useState("1")
  const [riskDegree, setRiskDegree] = useState("0.95")

  function handleMarketChange(m: string) {
    setMarket(m)
    setSymbolsText((MARKET_DEFAULTS[m] ?? MARKET_DEFAULTS.US).join(", "))
  }

  function handleRun() {
    const symbols = parseSymbols(symbolsText)
    if (symbols.length < 3) { alert("请输入至少 3 个标的代码"); return }
    run({
      symbols,
      market,
      frequency: "1d",
      start: startDate,
      end: endDate,
      score_method: scoreMethod,
      lookback: parseInt(lookback) || 20,
      rebalance_days: parseInt(rebalanceDays) || 5,
      topk: parseInt(topk) || 5,
      n_drop: parseInt(nDrop) || 0,
      hold_thresh: parseInt(holdThresh) || 1,
      risk_degree: parseFloat(riskDegree) || 0.95,
      method_sell: "bottom",
      method_buy: "top",
    })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <ConfigForm
        market={market} onMarket={handleMarketChange}
        symbolsText={symbolsText} onSymbols={setSymbolsText}
        startDate={startDate} onStart={setStartDate}
        endDate={endDate} onEnd={setEndDate}
        scoreMethod={scoreMethod} onScore={setScoreMethod}
        lookback={lookback} onLookback={setLookback}
        rebalanceDays={rebalanceDays} onRebalance={setRebalanceDays}
        topk={topk} onTopk={setTopk}
        nDrop={nDrop} onNDrop={setNDrop}
        holdThresh={holdThresh} onHold={setHoldThresh}
        riskDegree={riskDegree} onRisk={setRiskDegree}
        isPending={isPending} onRun={handleRun}
      />

      <div className="xl:col-span-3">
        {isPending && (
          <div className="card flex flex-col items-center justify-center py-20 gap-3">
            <Spinner size="lg" />
            <p className="text-[#8b949e] text-sm">正在打分并回测轮动组合…</p>
          </div>
        )}
        {error && !isPending && (
          <div className="card border-[#f85149]/30">
            <p className="text-[#f85149] text-sm font-medium mb-1">构建失败</p>
            <p className="text-[#8b949e] text-xs">{error.message}</p>
          </div>
        )}
        {!isPending && !result && !error && (
          <div className="card flex flex-col items-center justify-center py-20 gap-3 border-dashed">
            <p className="text-4xl">🔁</p>
            <p className="text-[#e6edf3] font-medium">配置标的池并构建 Topk 轮动组合</p>
            <p className="text-[#8b949e] text-sm text-center max-w-sm">
              横截面打分 → 持有 topK、每期剔除最差 n_drop 只，控制换手率
            </p>
          </div>
        )}
        {result && !isPending && <TopkResultView result={result} />}
      </div>
    </div>
  )
}

// ── 配置表单 ──────────────────────────────────────────────────────

interface ConfigFormProps {
  market: string; onMarket: (m: string) => void
  symbolsText: string; onSymbols: (v: string) => void
  startDate: string; onStart: (v: string) => void
  endDate: string; onEnd: (v: string) => void
  scoreMethod: TopkScoreMethod; onScore: (v: TopkScoreMethod) => void
  lookback: string; onLookback: (v: string) => void
  rebalanceDays: string; onRebalance: (v: string) => void
  topk: string; onTopk: (v: string) => void
  nDrop: string; onNDrop: (v: string) => void
  holdThresh: string; onHold: (v: string) => void
  riskDegree: string; onRisk: (v: string) => void
  isPending: boolean; onRun: () => void
}

function ConfigForm(p: ConfigFormProps) {
  return (
    <div className="xl:col-span-1 card h-fit space-y-4">
      <h2 className="text-sm font-semibold text-[#e6edf3]">轮动配置</h2>

      <div>
        <label className="label">市场</label>
        <div className="flex gap-1 mt-1">
          {["US", "HK", "A"].map((m) => (
            <button key={m} type="button" onClick={() => p.onMarket(m)}
              className={`flex-1 py-1.5 rounded text-xs font-medium border transition-colors ${
                p.market === m
                  ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                  : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
              }`}>{m === "A" ? "A股" : m}</button>
          ))}
        </div>
      </div>

      <div>
        <label className="label">标的池 <span className="ml-1 text-[#6e7681] text-[10px]">逗号或换行分隔</span></label>
        <textarea className="input w-full mt-1 font-mono text-xs resize-none" rows={5}
          value={p.symbolsText} onChange={(e) => p.onSymbols(e.target.value)}
          placeholder="AAPL, MSFT, GOOGL" />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="label">开始</label>
          <input className="input w-full mt-1" type="date" value={p.startDate}
            onChange={(e) => p.onStart(e.target.value)} />
        </div>
        <div>
          <label className="label">结束</label>
          <input className="input w-full mt-1" type="date" value={p.endDate}
            onChange={(e) => p.onEnd(e.target.value)} />
        </div>
      </div>

      <div>
        <label className="label">打分因子</label>
        <div className="space-y-1.5 mt-1">
          {SCORE_OPTIONS.map((o) => (
            <label key={o.value}
              className={`flex items-start gap-2 p-2 rounded cursor-pointer border transition-colors ${
                p.scoreMethod === o.value
                  ? "border-[#58a6ff]/40 bg-[#1f6feb]/10"
                  : "border-[#30363d] hover:border-[#58a6ff]/20"
              }`}>
              <input type="radio" name="topk-score" value={o.value}
                checked={p.scoreMethod === o.value}
                onChange={() => p.onScore(o.value)} className="mt-0.5 accent-[#58a6ff]" />
              <div>
                <p className="text-xs font-medium text-[#e6edf3]">{o.label}</p>
                <p className="text-[10px] text-[#6e7681]">{o.desc}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <NumField label="回看窗口" value={p.lookback} onChange={p.onLookback} />
        <NumField label="再平衡间隔" value={p.rebalanceDays} onChange={p.onRebalance} />
        <NumField label="持仓数 topK" value={p.topk} onChange={p.onTopk} />
        <NumField label="每期剔除 n_drop" value={p.nDrop} onChange={p.onNDrop} />
        <NumField label="最短持仓期" value={p.holdThresh} onChange={p.onHold} />
        <NumField label="资金度" value={p.riskDegree} onChange={p.onRisk} step="0.05" />
      </div>

      <button type="button" onClick={p.onRun} disabled={p.isPending} className="btn btn-primary w-full">
        {p.isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 构建组合"}
      </button>
    </div>
  )
}

interface NumFieldProps { label: string; value: string; onChange: (v: string) => void; step?: string }
function NumField({ label, value, onChange, step = "1" }: NumFieldProps) {
  return (
    <div>
      <label className="label text-[10px]">{label}</label>
      <input className="input w-full mt-1 font-mono text-xs" type="number" step={step}
        value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

// ── 结果视图 ──────────────────────────────────────────────────────

function TopkResultView({ result }: { result: TopkResult }) {
  const m = result.metrics
  const equityData = result.equity_curve.map((pt, i) => ({ i, equity: +pt.equity.toFixed(4) }))
  const turnoverData = result.periods.map((pd, i) => ({ i, turnover: +(pd.turnover * 100).toFixed(2) }))
  const latest = result.periods[result.periods.length - 1]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <MetricTile label="累计收益" value={pct(m.total_return)} accent={m.total_return >= 0 ? "up" : "down"} />
        <MetricTile label="年化收益" value={pct(m.annual_return)} accent={m.annual_return >= 0 ? "up" : "down"} />
        <MetricTile label="年化波动" value={pct(m.annual_vol)} />
        <MetricTile label="夏普比率" value={m.sharpe.toFixed(2)} accent={m.sharpe >= 1 ? "up" : m.sharpe < 0 ? "down" : "none"} />
        <MetricTile label="最大回撤" value={pct(m.max_drawdown)} accent="down" />
        <MetricTile label="平均换手" value={pct(m.avg_turnover)} />
        <MetricTile label="平均持仓数" value={m.avg_holdings.toFixed(1)} />
        <MetricTile label="胜率" value={pct(m.win_rate)} accent={m.win_rate >= 0.5 ? "up" : "down"} />
      </div>

      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-1">组合净值曲线</h3>
        <p className="text-[11px] text-[#6e7681] mb-3">
          {result.n_periods} 个再平衡期 · 每 {result.rebalance_days} 根 bar 调仓 · 资金度 {pct(result.risk_degree)}
        </p>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={equityData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="topk-equity" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3fb950" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#3fb950" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
            <XAxis dataKey="i" tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} width={44} tickFormatter={(v) => v.toFixed(2)} domain={["auto", "auto"]} />
            <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [v.toFixed(4), "净值"]} />
            <Area dataKey="equity" stroke="#3fb950" strokeWidth={2} fill="url(#topk-equity)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">逐期换手率</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={turnoverData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
              <XAxis dataKey="i" tick={AXIS_TICK} />
              <YAxis tick={AXIS_TICK} width={36} tickFormatter={(v) => `${v}%`} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`, "换手率"]} />
              <Bar dataKey="turnover" fill="#58a6ff" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {latest && (
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-1">最新持仓</h3>
            <p className="text-[11px] text-[#6e7681] mb-3">{latest.date} · {latest.n_holdings} 只</p>
            <div className="flex flex-wrap gap-1.5">
              {latest.holdings.map((sym) => (
                <span key={sym}
                  className="px-2.5 py-1 rounded bg-[#162a1e] border border-[#3fb950]/30 text-xs font-mono text-[#3fb950]">
                  {sym} · {((latest.weights[sym] ?? 0) * 100).toFixed(1)}%
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <RecentRebalances periods={result.periods} />
    </div>
  )
}

function RecentRebalances({ periods }: { periods: TopkResult["periods"] }) {
  const recent = periods.slice(-12).reverse()
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">近期调仓记录</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[#8b949e] border-b border-[#30363d]">
              <th className="text-left py-1.5 pr-3 font-medium">日期</th>
              <th className="text-left py-1.5 pr-3 font-medium">买入</th>
              <th className="text-left py-1.5 pr-3 font-medium">卖出</th>
              <th className="text-right py-1.5 pr-3 font-medium">换手</th>
              <th className="text-right py-1.5 font-medium">期间收益</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((p) => (
              <tr key={p.date} className="border-b border-[#21262d]">
                <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{p.date.slice(0, 10)}</td>
                <td className="py-1.5 pr-3 font-mono text-[#3fb950]">{p.buys.join(", ") || "—"}</td>
                <td className="py-1.5 pr-3 font-mono text-[#f85149]">{p.sells.join(", ") || "—"}</td>
                <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{(p.turnover * 100).toFixed(1)}%</td>
                <td className={`py-1.5 text-right font-mono ${p.period_return >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                  {p.period_return >= 0 ? "+" : ""}{(p.period_return * 100).toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
