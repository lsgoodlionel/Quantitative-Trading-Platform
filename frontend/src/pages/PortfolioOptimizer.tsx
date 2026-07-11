import { useState } from "react"
import { Link } from "react-router-dom"
import { format, subYears } from "date-fns"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { api } from "@/lib/api"
import { usePortfolioAllocate } from "@/hooks/usePortfolio"
import { useAdvancedPortfolioOptimize } from "@/hooks/usePortfolioAdvanced"
import { TopkDropoutPanel } from "@/pages/portfolio/TopkDropoutPanel"
import type {
  AdvancedOptMethod, AdvancedOptResult, AdvancedRiskModel,
  AdvancedReturnsMethod, HrpLinkage, BLViewInput,
} from "@/hooks/usePortfolioAdvanced"
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceDot, Cell, PieChart, Pie,
} from "recharts"
import type {
  Bar, Market, AllocationMethod, AllocateResult,
} from "@/types"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"

// 类型别名：沿用页面内既有命名，底层改用高级 hook 的超集类型
type PortfolioOptMethod = AdvancedOptMethod
type PortfolioOptResult = AdvancedOptResult
type RiskModel = AdvancedRiskModel
type ExpectedReturnsMethod = AdvancedReturnsMethod

// ── 常量 ──────────────────────────────────────────────────────

const METHOD_OPTIONS: { value: PortfolioOptMethod; label: string; desc: string }[] = [
  { value: "max_sharpe",    label: "最大夏普",     desc: "最大化风险调整后收益" },
  { value: "min_volatility",label: "最小波动",     desc: "最小化组合波动率" },
  { value: "risk_parity",   label: "风险平价",     desc: "均等化各资产风险贡献" },
  { value: "hrp",           label: "层次风险平价 HRP", desc: "相关性聚类 + 递归二分，小样本更稳，无需求逆" },
  { value: "black_litterman", label: "Black-Litterman", desc: "市场均衡先验 + 你的观点，Idzorek 置信度加权" },
  { value: "min_cvar",      label: "最小 CVaR",    desc: "线性规划最小化尾部损失（条件风险价值）" },
  { value: "min_cdar",      label: "最小 CDaR",    desc: "线性规划最小化条件回撤风险" },
  { value: "equal_weight",  label: "等权重基准",   desc: "等权对照组" },
]

const HRP_LINKAGE_OPTIONS: { value: HrpLinkage; label: string }[] = [
  { value: "single",   label: "single（最近邻）" },
  { value: "complete", label: "complete（最远邻）" },
  { value: "average",  label: "average（平均）" },
  { value: "ward",     label: "ward（方差最小）" },
]

/** 需要 BL 观点输入的方法 */
function isBlackLitterman(m: PortfolioOptMethod): boolean {
  return m === "black_litterman"
}
/** 需要 HRP 连接方式选项的方法 */
function isHrp(m: PortfolioOptMethod): boolean {
  return m === "hrp"
}
/** 需要 CVaR/CDaR 置信水平选项的方法 */
function isTailRisk(m: PortfolioOptMethod): boolean {
  return m === "min_cvar" || m === "min_cdar"
}

const RISK_MODEL_OPTIONS: { value: RiskModel; label: string; desc: string }[] = [
  { value: "sample_cov",     label: "样本协方差",     desc: "历史样本协方差（基准）" },
  { value: "ledoit_wolf",    label: "Ledoit-Wolf 收缩", desc: "收缩估计，降噪、抗病态（推荐）" },
  { value: "exp_cov",        label: "指数加权",       desc: "近期数据权重更高，体制感知" },
  { value: "semicovariance", label: "下行半协方差",   desc: "只统计下行波动，偏重尾部风险" },
]

const RETURNS_OPTIONS: { value: ExpectedReturnsMethod; label: string; desc: string }[] = [
  { value: "mean_historical", label: "历史均值",   desc: "历史收益均值（基准）" },
  { value: "ema_historical",  label: "指数加权均值", desc: "趋势倾斜，近期表现权重更高" },
  { value: "capm",            label: "CAPM 隐含",   desc: "市场 β 隐含收益，抗噪声均值" },
]

const ALLOCATION_OPTIONS: { value: AllocationMethod; label: string; desc: string }[] = [
  { value: "greedy", label: "贪心", desc: "快速、无求解器" },
  { value: "lp",     label: "整数规划", desc: "L1 最优，略慢" },
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

// ── 离散配置面板（连续权重 → 整数股数）─────────────────────────

async function fetchLatestPrices(
  symbols: string[],
  market: Market,
): Promise<{ prices: Record<string, number>; missing: string[] }> {
  const prices: Record<string, number> = {}
  const missing: string[] = []
  await Promise.all(
    symbols.map(async (symbol) => {
      try {
        const qs = new URLSearchParams({ symbol, market, frequency: "1d" })
        const bar = await api.get<Bar | null>(`/api/v1/bars/latest?${qs}`)
        if (bar && bar.close > 0) prices[symbol] = bar.close
        else missing.push(symbol)
      } catch {
        missing.push(symbol)
      }
    }),
  )
  return { prices, missing }
}

function AllocationResultView({ result }: { result: AllocateResult }) {
  const targetSymbols = Object.keys(result.allocation_weights)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "配置方法", value: result.method === "lp" ? "整数规划" : "贪心", color: "text-[#58a6ff]" },
          { label: "已配置金额", value: `$${result.allocated_value.toLocaleString()}`, color: "text-[#3fb950]" },
          { label: "剩余现金", value: `$${result.leftover_cash.toLocaleString()}`, color: "text-[#e3b341]" },
          { label: "权重 RMSE", value: result.rmse.toFixed(4), color: "text-[#e6edf3]" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-3">
            <p className="text-xs text-[#6e7681] mb-1">{label}</p>
            <p className={`font-mono font-semibold text-sm ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">标的</th>
              <th className="text-right py-2 pr-3">股数</th>
              <th className="text-right py-2 pr-3">实际权重</th>
              <th className="text-right py-2">目标权重</th>
            </tr>
          </thead>
          <tbody>
            {targetSymbols
              .sort((a, b) => (result.allocation_weights[b] ?? 0) - (result.allocation_weights[a] ?? 0))
              .map((sym) => {
                const shares = result.shares[sym] ?? 0
                const realized = (result.allocation_weights[sym] ?? 0) * 100
                return (
                  <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                    <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#3fb950]">{shares}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{realized.toFixed(1)}%</td>
                    <td className="py-2 text-right font-mono text-xs text-[#8b949e]">
                      {realized.toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>

      {result.skipped.length > 0 && (
        <p className="text-xs text-[#e3b341]">
          ⚠ 以下标的因缺少最新价格被跳过，权重已在其余标的上重新归一化：{result.skipped.join(", ")}
        </p>
      )}
    </div>
  )
}

function AllocationPanel({
  result,
  market,
}: {
  result: PortfolioOptResult
  market: Market
}) {
  const { mutate: runAllocate, isPending, data: allocation, error, reset } = usePortfolioAllocate()
  const [budget, setBudget] = useState<number>(100000)
  const [method, setMethod] = useState<AllocationMethod>("greedy")
  const [priceError, setPriceError] = useState<string | null>(null)
  const [fetchingPrices, setFetchingPrices] = useState(false)

  async function handleAllocate() {
    setPriceError(null)
    reset()
    if (!(budget > 0)) {
      setPriceError("请输入大于 0 的现金预算")
      return
    }
    const symbols = Object.entries(result.weights)
      .filter(([, w]) => w > 1e-4)
      .map(([sym]) => sym)

    setFetchingPrices(true)
    const { prices, missing } = await fetchLatestPrices(symbols, market)
    setFetchingPrices(false)

    if (Object.keys(prices).length === 0) {
      setPriceError(`未能获取任何标的最新价格${missing.length ? `（${missing.join(", ")}）` : ""}`)
      return
    }

    runAllocate({
      weights: result.weights,
      latest_prices: prices,
      total_value: budget,
      method,
    })
  }

  const busy = fetchingPrices || isPending

  return (
    <div className="card space-y-4 border-[#3fb950]/25">
      <div>
        <h3 className="text-sm font-semibold text-[#e6edf3]">生成整数配股</h3>
        <p className="text-[11px] text-[#6e7681] mt-0.5">
          按最新价格与现金预算，将连续权重转换为可执行的整数股数
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <div>
          <label className="label">现金预算</label>
          <input
            type="number"
            min={0}
            step={1000}
            className="input w-full mt-1 font-mono"
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">配置算法</label>
          <div className="flex gap-1 mt-1">
            {ALLOCATION_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title={opt.desc}
                onClick={() => setMethod(opt.value)}
                className={`flex-1 py-1.5 rounded text-xs font-medium border transition-colors ${
                  method === opt.value
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                    : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={handleAllocate}
          className="btn btn-primary w-full"
        >
          {busy ? <Spinner size="sm" className="mx-auto" /> : "生成配股方案"}
        </button>
      </div>

      {fetchingPrices && (
        <p className="text-xs text-[#8b949e]">正在获取最新价格…</p>
      )}
      {priceError && (
        <p className="text-xs text-[#f85149]">{priceError}</p>
      )}
      {error && !busy && (
        <p className="text-xs text-[#f85149]">配置失败：{error.message}</p>
      )}
      {allocation && !busy && <AllocationResultView result={allocation} />}
    </div>
  )
}

// ── Black-Litterman：观点编辑器 ───────────────────────────────

const EMPTY_VIEW: BLViewInput = { kind: "absolute", assets: [""], value: 0.1, confidence: 0.5 }

function SymbolSelect({
  value,
  symbols,
  onChange,
}: {
  value: string
  symbols: string[]
  onChange: (s: string) => void
}) {
  return (
    <select className="input flex-1 text-xs font-mono py-1"
      value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">选择标的</option>
      {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  )
}

function BLViewsEditor({
  views,
  symbols,
  onChange,
}: {
  views: BLViewInput[]
  symbols: string[]
  onChange: (views: BLViewInput[]) => void
}) {
  function updateView(idx: number, patch: Partial<BLViewInput>) {
    onChange(views.map((v, i) => (i === idx ? { ...v, ...patch } : v)))
  }
  function setKind(idx: number, kind: BLViewInput["kind"]) {
    const v = views[idx]
    const assets = kind === "relative"
      ? [v.assets[0] ?? "", v.assets[1] ?? ""]
      : [v.assets[0] ?? ""]
    updateView(idx, { kind, assets })
  }
  function setAsset(idx: number, pos: number, sym: string) {
    const assets = [...views[idx].assets]
    assets[pos] = sym
    updateView(idx, { assets })
  }
  function addView() {
    onChange([...views, { ...EMPTY_VIEW, assets: [symbols[0] ?? ""] }])
  }
  function removeView(idx: number) {
    onChange(views.filter((_, i) => i !== idx))
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="label">投资者观点（Black-Litterman）</label>
        <button type="button" onClick={addView}
          className="text-[11px] px-2 py-0.5 rounded border border-[#58a6ff]/40 text-[#58a6ff] hover:bg-[#1f6feb]/10">
          + 添加观点
        </button>
      </div>
      {views.length === 0 && (
        <p className="text-[10px] text-[#e3b341]">至少添加 1 条观点后才能运行 BL 优化</p>
      )}
      <div className="space-y-2">
        {views.map((v, idx) => (
          <div key={idx} className="border border-[#30363d] rounded p-2 space-y-2 bg-[#0d1117]">
            <div className="flex items-center gap-1">
              {(["absolute", "relative"] as const).map((k) => (
                <button key={k} type="button" onClick={() => setKind(idx, k)}
                  className={`flex-1 py-1 rounded text-[10px] border transition-colors ${
                    v.kind === k
                      ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                      : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                  }`}>
                  {k === "absolute" ? "绝对（收益=）" : "相对（跑赢）"}
                </button>
              ))}
              <button type="button" onClick={() => removeView(idx)}
                title="删除观点"
                className="px-1.5 py-1 rounded text-[10px] text-[#f85149] border border-[#f85149]/30 hover:bg-[#f85149]/10">
                ✕
              </button>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <SymbolSelect value={v.assets[0] ?? ""} symbols={symbols}
                onChange={(s) => setAsset(idx, 0, s)} />
              {v.kind === "relative" && (
                <>
                  <span className="text-[#6e7681] text-[10px] shrink-0">跑赢</span>
                  <SymbolSelect value={v.assets[1] ?? ""} symbols={symbols}
                    onChange={(s) => setAsset(idx, 1, s)} />
                </>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-[#6e7681]">
                  {v.kind === "absolute" ? "年化收益 %" : "超额收益 %"}
                </label>
                <input type="number" step={1} className="input w-full mt-0.5 font-mono text-xs"
                  value={Math.round(v.value * 1000) / 10}
                  onChange={(e) => updateView(idx, { value: Number(e.target.value) / 100 })} />
              </div>
              <div>
                <label className="text-[10px] text-[#6e7681]">置信度 {(v.confidence * 100).toFixed(0)}%</label>
                <input type="range" min={0} max={1} step={0.05} className="w-full mt-2 accent-[#58a6ff]"
                  value={v.confidence}
                  onChange={(e) => updateView(idx, { confidence: Number(e.target.value) })} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Black-Litterman：先验 vs 后验回显 ──────────────────────────

function BLPosteriorView({ result }: { result: PortfolioOptResult }) {
  const prior = result.bl_prior_returns ?? {}
  const posterior = result.bl_posterior_returns ?? {}
  const symbols = Object.keys(posterior)
  if (symbols.length === 0) return null

  return (
    <div className="card border-[#bc8cff]/25">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[#e6edf3]">Black-Litterman 观点融合</h3>
        {result.bl_risk_aversion != null && (
          <span className="text-[11px] text-[#8b949e]">
            风险厌恶 δ = <span className="font-mono text-[#bc8cff]">{result.bl_risk_aversion.toFixed(2)}</span>
          </span>
        )}
      </div>
      {(result.bl_views?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {result.bl_views!.map((label, i) => (
            <span key={i} className="text-[10px] bg-[#161b22] border border-[#bc8cff]/30 rounded px-2 py-0.5 text-[#bc8cff]">
              {label}
            </span>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">标的</th>
              <th className="text-right py-2 pr-3">市场隐含先验</th>
              <th className="text-right py-2 pr-3">融合后验</th>
              <th className="text-right py-2">观点调整</th>
            </tr>
          </thead>
          <tbody>
            {symbols
              .sort((a, b) => (posterior[b] ?? 0) - (posterior[a] ?? 0))
              .map((sym) => {
                const pri = prior[sym] ?? 0
                const post = posterior[sym] ?? 0
                const delta = post - pri
                return (
                  <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                    <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">{pri.toFixed(2)}%</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{post.toFixed(2)}%</td>
                    <td className={`py-2 text-right font-mono text-xs ${
                      delta > 0.05 ? "text-[#3fb950]" : delta < -0.05 ? "text-[#f85149]" : "text-[#6e7681]"
                    }`}>
                      {delta >= 0 ? "+" : ""}{delta.toFixed(2)}%
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-[#6e7681] mt-2">
        先验来自市场组合的反向优化（δ·Σ·w_mkt）；后验按 Idzorek 置信度将观点与先验贝叶斯融合，再驱动最大夏普配权。
      </p>
    </div>
  )
}

function ResultPanel({ result, market }: { result: PortfolioOptResult; market: Market }) {
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

      {/* 估计器回显 */}
      {(result.risk_model || result.expected_returns_method) && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {result.risk_model && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              风险模型：
              <span className="text-[#58a6ff] ml-1">
                {RISK_MODEL_OPTIONS.find((o) => o.value === result.risk_model)?.label ?? result.risk_model}
              </span>
            </span>
          )}
          {result.expected_returns_method && !isHrp(result.method as PortfolioOptMethod) && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              预期收益：
              <span className="text-[#58a6ff] ml-1">
                {RETURNS_OPTIONS.find((o) => o.value === result.expected_returns_method)?.label ?? result.expected_returns_method}
              </span>
            </span>
          )}
          {result.linkage_method && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              聚类连接：
              <span className="text-[#58a6ff] ml-1">
                {HRP_LINKAGE_OPTIONS.find((o) => o.value === result.linkage_method)?.label ?? result.linkage_method}
              </span>
            </span>
          )}
          {result.cvar_beta != null && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              尾部置信水平：
              <span className="text-[#58a6ff] ml-1">{(result.cvar_beta * 100).toFixed(0)}%</span>
            </span>
          )}
        </div>
      )}

      {/* Black-Litterman 先验/后验融合 */}
      <BLPosteriorView result={result} />

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

      {/* 离散配置：连续权重 → 整数股数 */}
      <AllocationPanel result={result} market={market} />

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
  risk_model: RiskModel
  expected_returns_method: ExpectedReturnsMethod
  views: BLViewInput[]
  linkage_method: HrpLinkage
  cvar_beta: number
}

/** 从标的文本解析为大写代码数组 */
function parseSymbols(text: string): string[] {
  return text
    .split(/[,\s\n]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
}

type OptimizerView = "optimize" | "topk"

export function PortfolioOptimizer() {
  const [view, setView] = useState<OptimizerView>("optimize")
  const { mutate: runOpt, isPending, data: result, error } = useAdvancedPortfolioOptimize()

  const [form, setForm] = useState<FormState>({
    symbolsText: MARKET_DEFAULTS.US.join(", "),
    market: "US",
    start_date: yearsAgo(3),
    end_date: today(),
    method: "max_sharpe",
    include_frontier: true,
    risk_model: "sample_cov",
    expected_returns_method: "mean_historical",
    views: [],
    linkage_method: "single",
    cvar_beta: 0.95,
  })

  const currentSymbols = parseSymbols(form.symbolsText)
  // 记录发起优化时的市场，供离散配置拉取最新价格（表单市场可能后续被改动）
  const [submittedMarket, setSubmittedMarket] = useState<Market>("US")

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
    const symbols = parseSymbols(form.symbolsText)

    if (symbols.length < 2) {
      alert("请输入至少 2 个标的代码")
      return
    }

    if (isBlackLitterman(form.method)) {
      const cleanViews = form.views.filter(
        (v) => v.assets.every((a) => a) &&
          (v.kind === "absolute" ? v.assets.length >= 1 : v.assets.length >= 2),
      )
      if (cleanViews.length === 0) {
        alert("Black-Litterman 需要至少 1 条完整观点（选择标的并填写收益）")
        return
      }
    }

    setSubmittedMarket(form.market)
    runOpt({
      symbols,
      market: form.market,
      start_date: form.start_date,
      end_date: form.end_date,
      method: form.method,
      include_frontier: form.include_frontier,
      risk_model: form.risk_model,
      expected_returns_method: form.expected_returns_method,
      views: isBlackLitterman(form.method) ? form.views : undefined,
      linkage_method: form.linkage_method,
      cvar_beta: form.cvar_beta,
    })
  }

  return (
    <AppShell title="组合优化器" help={PAGE_HELP["portfolio-optimizer"]}>
      {/* 模式切换：均值-方差优化 vs Topk 轮动组合 */}
      <div className="flex gap-1 mb-5 border-b border-[#30363d]">
        {([
          { key: "optimize", label: "组合优化", desc: "均值-方差 / 风险平价 / BL / CVaR" },
          { key: "topk", label: "Topk 轮动组合", desc: "打分 → 持 topK、控换手轮动" },
        ] as { key: OptimizerView; label: string; desc: string }[]).map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setView(t.key)}
            title={t.desc}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              view === t.key
                ? "border-[#58a6ff] text-[#e6edf3]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {view === "topk" && <TopkDropoutPanel />}

      {view === "optimize" && (
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

          {/* Black-Litterman 观点输入 */}
          {isBlackLitterman(form.method) && (
            <BLViewsEditor
              views={form.views}
              symbols={currentSymbols}
              onChange={(views) => setForm((f) => ({ ...f, views }))}
            />
          )}

          {/* HRP 聚类连接方式 */}
          {isHrp(form.method) && (
            <div>
              <label className="label">聚类连接方式（HRP）</label>
              <select
                className="input w-full mt-1 text-xs"
                value={form.linkage_method}
                onChange={(e) => setForm((f) => ({ ...f, linkage_method: e.target.value as HrpLinkage }))}
              >
                {HRP_LINKAGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          )}

          {/* CVaR/CDaR 置信水平 */}
          {isTailRisk(form.method) && (
            <div>
              <label className="label">
                尾部置信水平 β
                <span className="ml-1 text-[#58a6ff] font-mono">{(form.cvar_beta * 100).toFixed(0)}%</span>
              </label>
              <input
                type="range" min={0.8} max={0.99} step={0.01}
                className="w-full mt-2 accent-[#58a6ff]"
                value={form.cvar_beta}
                onChange={(e) => setForm((f) => ({ ...f, cvar_beta: Number(e.target.value) }))}
              />
              <p className="text-[10px] text-[#6e7681] mt-1">
                关注最差 {((1 - form.cvar_beta) * 100).toFixed(0)}% 情景的{form.method === "min_cdar" ? "回撤" : "损失"}
              </p>
            </div>
          )}

          {/* 风险模型 */}
          <div>
            <label className="label">风险模型（协方差估计）</label>
            <select
              className="input w-full mt-1 text-xs"
              value={form.risk_model}
              onChange={(e) => setForm((f) => ({ ...f, risk_model: e.target.value as RiskModel }))}
            >
              {RISK_MODEL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-[#6e7681] mt-1">
              {RISK_MODEL_OPTIONS.find((o) => o.value === form.risk_model)?.desc}
            </p>
          </div>

          {/* 预期收益估计 */}
          <div>
            <label className="label">预期收益估计</label>
            <select
              className="input w-full mt-1 text-xs"
              value={form.expected_returns_method}
              onChange={(e) => setForm((f) => ({ ...f, expected_returns_method: e.target.value as ExpectedReturnsMethod }))}
            >
              {RETURNS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-[#6e7681] mt-1">
              {RETURNS_OPTIONS.find((o) => o.value === form.expected_returns_method)?.desc}
            </p>
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

          {result && !isPending && <ResultPanel result={result} market={submittedMarket} />}
        </div>
      </div>
      )}
    </AppShell>
  )
}
