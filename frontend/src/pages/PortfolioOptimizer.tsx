import { useState } from "react"
import { Link } from "react-router-dom"
import { format, subYears } from "date-fns"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { usePortfolioOptimize } from "@/hooks/usePortfolio"
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceDot, Cell, PieChart, Pie,
} from "recharts"
import type { Market, PortfolioOptMethod, PortfolioOptResult } from "@/types"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"

// ── 常量 ──────────────────────────────────────────────────────

const METHOD_OPTIONS: { value: PortfolioOptMethod; label: string; desc: string }[] = [
  { value: "max_sharpe",    label: "最大夏普",     desc: "最大化风险调整后收益" },
  { value: "min_volatility",label: "最小波动",     desc: "最小化组合波动率" },
  { value: "risk_parity",   label: "风险平价",     desc: "均等化各资产风险贡献" },
  { value: "min_cvar",      label: "最小 CVaR",    desc: "最小化极端损失风险" },
  { value: "equal_weight",  label: "等权重基准",   desc: "等权对照组" },
]

const MARKET_DEFAULTS: Record<string, string[]> = {
  US: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "V"],
  HK: ["00700", "02318", "09988", "01299", "02020"],
  A:  ["000001", "600519", "300750", "000858", "601318"],
}

const PALETTE = [
  "#58a6ff", "#3fb950", "#f85149", "#e3b341", "#bc8cff",
  "#ff9f43", "#54a0ff", "#00d2d3", "#ff6b81", "#5f27cd",
]

function today() { return format(new Date(), "yyyy-MM-dd") }
function yearsAgo(n: number) { return format(subYears(new Date(), n), "yyyy-MM-dd") }

// ── 子组件 ────────────────────────────────────────────────────

function WeightPieChart({ weights }: { weights: Record<string, number> }) {
  const data = Object.entries(weights)
    .filter(([, w]) => w > 0.005)
    .map(([sym, w]) => ({ name: sym, value: Math.round(w * 10000) / 100 }))

  return (
    <div>
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie data={data} cx="50%" cy="50%" innerRadius={50} outerRadius={85} paddingAngle={2} dataKey="value">
            {data.map((_, idx) => <Cell key={idx} fill={PALETTE[idx % PALETTE.length]} />)}
          </Pie>
          <Tooltip
            contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
            formatter={(v: number) => [`${v.toFixed(1)}%`, "权重"]}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="space-y-1.5 mt-1">
        {data.map((d, idx) => (
          <div key={d.name} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: PALETTE[idx % PALETTE.length] }} />
              <span className="font-mono text-[#e6edf3]">{d.name}</span>
            </div>
            <span className="text-[#8b949e] font-mono">{d.value.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function EfficientFrontierChart({
  frontier,
  result,
}: {
  frontier: { vol: number; ret: number; sharpe: number }[]
  result: PortfolioOptResult
}) {
  if (!frontier.length) return null

  const coloredFrontier = frontier.map((pt) => ({
    ...pt,
    color: pt.sharpe >= result.sharpe_ratio * 0.95 ? "#3fb950" : "#58a6ff",
  }))

  return (
    <ResponsiveContainer width="100%" height={280}>
      <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
        <XAxis
          type="number" dataKey="vol"
          name="波动率"
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false} tickLine={false}
          label={{ value: "年化波动率 (%)", position: "insideBottom", offset: -4, fill: "#6e7681", fontSize: 10 }}
        />
        <YAxis
          type="number" dataKey="ret"
          name="收益率"
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          tick={{ fill: "#8b949e", fontSize: 10 }}
          axisLine={false} tickLine={false} width={48}
          label={{ value: "年化收益率 (%)", angle: -90, position: "insideLeft", offset: 8, fill: "#6e7681", fontSize: 10 }}
        />
        <Tooltip
          contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
          formatter={(v: number, name: string) => [
            name === "vol" ? `${v.toFixed(2)}%` : name === "ret" ? `${v.toFixed(2)}%` : v.toFixed(3),
            name === "vol" ? "波动率" : name === "ret" ? "收益率" : "夏普",
          ]}
        />
        <Scatter name="有效前沿" data={coloredFrontier} fill="#58a6ff">
          {coloredFrontier.map((entry, idx) => (
            <Cell key={idx} fill={entry.color} opacity={0.7} />
          ))}
        </Scatter>
        {/* 当前优化结果标记点 */}
        <ReferenceDot
          x={result.expected_volatility}
          y={result.expected_return}
          r={8}
          fill="#f85149"
          stroke="#ff7b72"
          strokeWidth={2}
          label={{ value: "★", position: "top", fill: "#f85149", fontSize: 14 }}
        />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

// ── 组合优化结论生成 ─────────────────────────────────────────────

function buildPortfolioInsight(result: PortfolioOptResult) {
  const { expected_return, expected_volatility, sharpe_ratio, cvar_95, weights } = result
  const maxW = Math.max(...Object.values(weights)) * 100
  const activeN = Object.values(weights).filter((w) => w > 0.01).length
  const methodLabel = METHOD_OPTIONS.find((m) => m.value === result.method)?.label ?? result.method

  const verdict: InsightVerdict =
    sharpe_ratio >= 1.5 && expected_return > 0 ? "good"
    : sharpe_ratio >= 0.8 && expected_return > 0 ? "warn"
    : "bad"

  const grade =
    sharpe_ratio >= 1.5 ? "优秀（Sharpe ≥ 1.5）"
    : sharpe_ratio >= 1.0 ? "良好（Sharpe ≥ 1.0）"
    : sharpe_ratio >= 0.5 ? "一般（Sharpe < 1.0）"
    : "较弱（Sharpe < 0.5）"

  const summary = `采用「${methodLabel}」优化后，组合年化收益预期 ${expected_return >= 0 ? "+" : ""}${expected_return.toFixed(2)}%，年化波动率 ${expected_volatility.toFixed(2)}%，夏普比率 ${sharpe_ratio.toFixed(2)}，综合评级：${grade}。`

  const findings: InsightItem[] = [
    {
      text: `夏普比率 ${sharpe_ratio.toFixed(3)} — ${sharpe_ratio >= 1.5 ? "风险调整收益优秀，远超无风险资产" : sharpe_ratio >= 1.0 ? "风险调整收益良好，具备实盘部署参考价值" : "风险调整收益偏低，建议优化资产池或调整方法"}`,
      type: sharpe_ratio >= 1.5 ? "good" : sharpe_ratio >= 1.0 ? "good" : sharpe_ratio >= 0.5 ? "warn" : "bad",
    },
    {
      text: `95% CVaR ${cvar_95.toFixed(2)}% — 极端情景下单日最大预期损失`,
      sub: cvar_95 > 10 ? "尾部风险偏高，建议降低单资产权重上限或加入低相关性资产" : "尾部风险可控",
      type: cvar_95 > 10 ? "bad" : cvar_95 > 5 ? "warn" : "good",
    },
    {
      text: `最大单资产权重 ${maxW.toFixed(1)}% — ${maxW > 40 ? "集中度过高，面临个股黑天鹅风险" : maxW > 25 ? "集中度适中" : "分散度良好"}`,
      type: maxW > 40 ? "bad" : maxW > 25 ? "warn" : "good",
    },
    {
      text: `有效持仓 ${activeN} 只 — ${activeN < 3 ? "过度集中，建议增加资产数量" : activeN <= 8 ? "资产数量合理" : "资产过多，可能稀释阿尔法"}`,
      type: activeN < 3 ? "bad" : activeN <= 8 ? "good" : "warn",
    },
  ]

  const recommendations: InsightItem[] = [
    ...(sharpe_ratio < 1.0 ? [{
      text: "尝试切换优化方法",
      sub: "当前结果夏普偏低，可试验「最大夏普」或「风险平价」方法，或更换资产池",
      type: "warn" as const,
    }] : []),
    ...(maxW > 35 ? [{
      text: "设置权重上限约束",
      sub: `最大权重 ${maxW.toFixed(1)}% 过高，建议在后端 API 参数中加入 max_weight=0.30 约束`,
      type: "warn" as const,
    }] : []),
    {
      text: "周期性再平衡",
      sub: "建议每季度重新运行优化，市场结构变化会使优化权重失效",
      type: "neutral" as const,
    },
    {
      text: "结合回测验证",
      sub: "优化结果基于历史协方差，建议在「回测」页面对该权重组合做历史验证，防止过拟合",
      type: "neutral" as const,
    },
    ...(expected_return <= 0 ? [{
      text: "收益预期为负，重新筛选资产",
      sub: "检查各资产的历史收益数据区间，或更换具有正收益预期的标的组合",
      type: "bad" as const,
    }] : []),
  ]

  return { verdict, summary, findings, recommendations }
}

function ResultPanel({ result }: { result: PortfolioOptResult }) {
  const methodLabel = METHOD_OPTIONS.find((m) => m.value === result.method)?.label ?? result.method
  const insight = buildPortfolioInsight(result)

  return (
    <div className="space-y-5">
      {/* 关键指标 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "优化方法", value: methodLabel, color: "text-[#58a6ff]" },
          { label: "年化收益率", value: `${result.expected_return >= 0 ? "+" : ""}${result.expected_return.toFixed(2)}%`, color: result.expected_return >= 0 ? "text-[#3fb950]" : "text-[#f85149]" },
          { label: "年化波动率", value: `${result.expected_volatility.toFixed(2)}%`, color: "text-[#e3b341]" },
          { label: "夏普比率", value: result.sharpe_ratio.toFixed(3), color: result.sharpe_ratio >= 1 ? "text-[#3fb950]" : "text-[#e6edf3]" },
          { label: "95% CVaR", value: `${result.cvar_95.toFixed(2)}%`, color: "text-[#f85149]" },
          { label: "资产数量", value: Object.keys(result.weights).length, color: "text-[#e6edf3]" },
          { label: "最大权重", value: `${(Math.max(...Object.values(result.weights)) * 100).toFixed(1)}%`, color: "text-[#e6edf3]" },
          { label: "最小权重", value: `${(Math.min(...Object.values(result.weights).filter((w) => w > 0.001)) * 100).toFixed(1)}%`, color: "text-[#e6edf3]" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-3">
            <p className="text-xs text-[#6e7681] mb-1">{label}</p>
            <p className={`font-mono font-semibold text-sm ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 权重分布饼图 */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">权重分布</h3>
          <WeightPieChart weights={result.weights} />
        </div>

        {/* 有效前沿 */}
        {result.frontier.length > 0 && (
          <div className="card lg:col-span-2">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
              有效前沿
              <span className="ml-2 text-xs text-[#6e7681] font-normal">红点 = 当前优化组合</span>
            </h3>
            <EfficientFrontierChart frontier={result.frontier} result={result} />
          </div>
        )}
      </div>

      {/* 权重 + 风险贡献表格 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">资产明细</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
                <th className="text-left py-2 pr-3">标的</th>
                <th className="text-right py-2 pr-3">权重</th>
                <th className="text-right py-2 pr-3">权重条</th>
                <th className="text-right py-2">风险贡献</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(result.weights)
                .sort(([, a], [, b]) => b - a)
                .map(([sym, w]) => {
                  const rc = result.risk_contributions[sym] ?? 0
                  const wPct = w * 100
                  return (
                    <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                      <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                        {wPct.toFixed(1)}%
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex justify-end items-center gap-1">
                          <div className="w-32 h-2 rounded bg-[#21262d] overflow-hidden">
                            <div
                              className="h-full rounded bg-[#58a6ff]"
                              style={{ width: `${Math.min(wPct, 100)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="py-2 text-right font-mono text-xs text-[#8b949e]">
                        {rc.toFixed(1)}%
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 结论与建议 */}
      <InsightBox
        verdict={insight.verdict}
        summary={insight.summary}
        findings={insight.findings}
        recommendations={insight.recommendations}
      />

      {/* 下一步操作 CTA */}
      <div className="card border-[#30363d] space-y-3">
        <p className="text-xs font-semibold text-[#8b949e]">📍 优化完成，建议下一步</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
          <Link to="/risk"
            className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[#f85149]/25 text-[#f85149] bg-[#1a0f0f] hover:bg-[#f85149]/10 transition-colors">
            <span className="text-base">🛡️</span>
            <div>
              <p className="font-medium">验证风险水平</p>
              <p className="text-[10px] text-[#f85149]/70">在风控页运行 VaR 确认组合风险</p>
            </div>
          </Link>
          <Link to="/backtest"
            className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[#58a6ff]/25 text-[#58a6ff] bg-[#111d2e] hover:bg-[#58a6ff]/10 transition-colors">
            <span className="text-base">🔬</span>
            <div>
              <p className="font-medium">对权重最高标的回测</p>
              <p className="text-[10px] text-[#58a6ff]/70">验证最优权重的历史表现</p>
            </div>
          </Link>
          <Link to="/orders"
            className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[#3fb950]/25 text-[#3fb950] bg-[#0d2018] hover:bg-[#3fb950]/10 transition-colors">
            <span className="text-base">📋</span>
            <div>
              <p className="font-medium">执行再平衡</p>
              <p className="text-[10px] text-[#3fb950]/70">按优化权重手动调整仓位</p>
            </div>
          </Link>
        </div>
        {/* 权重摘要供手动参考 */}
        <div className="bg-[#0d1117] rounded-lg p-3 text-[10px] text-[#6e7681]">
          <p className="font-medium text-[#8b949e] mb-1.5">优化权重（再平衡参考）</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(result.weights)
              .sort(([,a],[,b]) => b - a)
              .map(([sym, w]) => (
                <span key={sym} className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5">
                  <span className="font-mono text-[#e6edf3]">{sym}</span>
                  <span className="ml-1 text-[#58a6ff]">{(w * 100).toFixed(1)}%</span>
                </span>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────

interface FormState {
  symbolsText: string
  market: Market
  start_date: string
  end_date: string
  method: PortfolioOptMethod
  include_frontier: boolean
}

export function PortfolioOptimizer() {
  const { mutate: runOpt, isPending, data: result, error } = usePortfolioOptimize()

  const [form, setForm] = useState<FormState>({
    symbolsText: MARKET_DEFAULTS.US.join(", "),
    market: "US",
    start_date: yearsAgo(3),
    end_date: today(),
    method: "max_sharpe",
    include_frontier: true,
  })

  function handleMarketChange(m: string) {
    const market = m as Market
    setForm((f) => ({
      ...f,
      market,
      symbolsText: (MARKET_DEFAULTS[market] ?? MARKET_DEFAULTS.US).join(", "),
    }))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const symbols = form.symbolsText
      .split(/[,\s\n]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)

    if (symbols.length < 2) {
      alert("请输入至少 2 个标的代码")
      return
    }

    runOpt({
      symbols,
      market: form.market,
      start_date: form.start_date,
      end_date: form.end_date,
      method: form.method,
      include_frontier: form.include_frontier,
    })
  }

  return (
    <AppShell title="组合优化器" help={PAGE_HELP["portfolio-optimizer"]}>
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        {/* 配置面板 */}
        <form onSubmit={handleSubmit} className="xl:col-span-1 card h-fit space-y-4">
          <h2 className="text-sm font-semibold text-[#e6edf3]">优化配置</h2>

          {/* 市场 */}
          <div>
            <label className="label">市场</label>
            <div className="flex gap-1 mt-1">
              {(["US", "HK", "A"] as Market[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => handleMarketChange(m)}
                  className={`flex-1 py-1.5 rounded text-xs font-medium border transition-colors ${
                    form.market === m
                      ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                      : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                  }`}
                >
                  {m === "A" ? "A股" : m}
                </button>
              ))}
            </div>
          </div>

          {/* 标的列表 */}
          <div>
            <label className="label">
              标的列表
              <span className="ml-1 text-[#6e7681] text-[10px]">逗号或换行分隔</span>
            </label>
            <textarea
              className="input w-full mt-1 font-mono text-xs resize-none"
              rows={5}
              value={form.symbolsText}
              onChange={(e) => setForm((f) => ({ ...f, symbolsText: e.target.value }))}
              placeholder="AAPL, MSFT, GOOGL"
            />
          </div>

          {/* 日期区间 */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">开始</label>
              <input className="input w-full mt-1" type="date" value={form.start_date}
                onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
            </div>
            <div>
              <label className="label">结束</label>
              <input className="input w-full mt-1" type="date" value={form.end_date}
                onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
            </div>
          </div>

          {/* 快捷日期 */}
          <div className="flex gap-1">
            {[1, 2, 3, 5].map((y) => (
              <button
                key={y}
                type="button"
                onClick={() => setForm((f) => ({ ...f, start_date: yearsAgo(y) }))}
                className="flex-1 text-xs py-1 rounded border border-[#30363d] text-[#6e7681] hover:text-[#e6edf3] hover:border-[#58a6ff]/40 transition-colors"
              >
                {y}年
              </button>
            ))}
          </div>

          {/* 优化方法 */}
          <div>
            <label className="label">优化方法</label>
            <div className="space-y-1.5 mt-1">
              {METHOD_OPTIONS.map((m) => (
                <label
                  key={m.value}
                  className={`flex items-start gap-2 p-2 rounded cursor-pointer border transition-colors ${
                    form.method === m.value
                      ? "border-[#58a6ff]/40 bg-[#1f6feb]/10"
                      : "border-[#30363d] hover:border-[#58a6ff]/20"
                  }`}
                >
                  <input
                    type="radio"
                    name="method"
                    value={m.value}
                    checked={form.method === m.value}
                    onChange={() => setForm((f) => ({ ...f, method: m.value }))}
                    className="mt-0.5 accent-[#58a6ff]"
                  />
                  <div>
                    <p className="text-xs font-medium text-[#e6edf3]">{m.label}</p>
                    <p className="text-[10px] text-[#6e7681]">{m.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* 有效前沿开关 */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={form.include_frontier}
              onChange={(e) => setForm((f) => ({ ...f, include_frontier: e.target.checked }))}
              className="accent-[#58a6ff]"
            />
            <span className="text-xs text-[#8b949e]">计算有效前沿（较慢）</span>
          </label>

          <button
            type="submit"
            disabled={isPending}
            className="btn btn-primary w-full"
          >
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 开始优化"}
          </button>
        </form>

        {/* 右侧结果区 */}
        <div className="xl:col-span-3">
          {isPending && (
            <div className="card flex flex-col items-center justify-center py-20 gap-3">
              <Spinner size="lg" />
              <p className="text-[#8b949e] text-sm">正在下载历史数据并运行优化算法…</p>
            </div>
          )}

          {error && !isPending && (
            <div className="card border-[#f85149]/30">
              <p className="text-[#f85149] text-sm font-medium mb-1">优化失败</p>
              <p className="text-[#8b949e] text-xs">{error.message}</p>
            </div>
          )}

          {!isPending && !result && !error && (
            <div className="card flex flex-col items-center justify-center py-20 gap-3 border-dashed">
              <p className="text-4xl">📊</p>
              <p className="text-[#e6edf3] font-medium">配置标的并运行优化</p>
              <p className="text-[#8b949e] text-sm text-center max-w-sm">
                支持均值-方差优化、风险平价、CVaR 最小化，并可视化有效前沿
              </p>
            </div>
          )}

          {result && !isPending && <ResultPanel result={result} />}
        </div>
      </div>
    </AppShell>
  )
}
