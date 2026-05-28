import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar as RBar, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { useGBM, useBSM, useGARCH, useKelly, useCointegration, useHMM } from "@/hooks/useQuant"
import Editor from "@monaco-editor/react"
import { usePresets, useStrategySource, useValidateStrategy } from "@/hooks/useStrategy"
import { SectionCard, ParamRow, MetaGrid, CHART_COLORS } from "@/pages/algolab/shared"
import { MLPanel } from "@/pages/algolab/MLStrategyPanel"

// ── Shared helpers ────────────────────────────────────────────────

type AlgoTab = "gbm" | "bsm" | "garch" | "kelly" | "coint" | "hmm" | "editor" | "ml"

const TABS: { id: AlgoTab; label: string; cn: string }[] = [
  { id: "gbm",    label: "GBM 蒙卡",   cn: "几何布朗运动" },
  { id: "bsm",    label: "BSM 期权",   cn: "Black-Scholes-Merton" },
  { id: "garch",  label: "GARCH",      cn: "波动率建模" },
  { id: "kelly",  label: "凯利准则",    cn: "仓位优化" },
  { id: "coint",  label: "协整",        cn: "统计套利" },
  { id: "hmm",    label: "HMM 状态",   cn: "市场状态识别" },
  { id: "editor", label: "策略编辑器",  cn: "自定义策略" },
  { id: "ml",     label: "ML 策略",    cn: "机器学习预测" },
]



// ── GBM Panel ─────────────────────────────────────────────────────

function GBMPanel() {
  const { mutate, isPending, data: result, error } = useGBM()
  const { toast } = useToast()
  const [S0, setS0] = useState("100")
  const [mu, setMu] = useState("0.10")
  const [sigma, setSigma] = useState("0.20")
  const [T, setT] = useState("1.0")
  const [nPaths, setNPaths] = useState("1000")

  function run() {
    const s = parseFloat(S0), m = parseFloat(mu), sg = parseFloat(sigma), t = parseFloat(T)
    if ([s, m, sg, t].some(isNaN)) { toast("请输入有效数字", "warning"); return }
    mutate({ S0: s, mu: m, sigma: sg, T: t, n_paths: parseInt(nPaths) || 1000, seed: 42 })
  }

  // 构建蒙卡路径图数据（取前20条）
  const pathChartData = result ? result.time_axis.map((t, i) => {
    const pt: Record<string, number> = { t }
    result.sample_paths.slice(0, 20).forEach((p, j) => { pt[`p${j}`] = p[i] })
    return pt
  }) : []

  // 期末价格分布直方图（简化为20个桶）
  const distData = result ? (() => {
    const min = result.final_p5, max = result.final_p95
    const buckets = 20
    const w = (max - min) / buckets
    const counts = new Array(buckets).fill(0)
    result.sample_paths.forEach(p => {
      const v = p[p.length - 1]
      const b = Math.min(Math.floor((v - min) / w), buckets - 1)
      if (b >= 0) counts[b]++
    })
    return counts.map((c, i) => ({ price: +(min + (i + 0.5) * w).toFixed(1), count: c }))
  })() : []

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="参数配置" sub="几何布朗运动">
          <ParamRow label="当前价格 S₀"><input className="input w-28 font-mono" value={S0} onChange={e => setS0(e.target.value)} /></ParamRow>
          <ParamRow label="年化漂移率 μ"><input className="input w-28 font-mono" value={mu} onChange={e => setMu(e.target.value)} placeholder="0.10" /></ParamRow>
          <ParamRow label="年化波动率 σ"><input className="input w-28 font-mono" value={sigma} onChange={e => setSigma(e.target.value)} placeholder="0.20" /></ParamRow>
          <ParamRow label="时间跨度 T (年)"><input className="input w-28 font-mono" value={T} onChange={e => setT(e.target.value)} placeholder="1.0" /></ParamRow>
          <ParamRow label="模拟路径数">
            <select className="select" value={nPaths} onChange={e => setNPaths(e.target.value)}>
              {["100","500","1000","5000","10000"].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "运行模拟"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <MetaGrid items={[
            { label: "期末均值", value: `$${result.final_mean.toFixed(2)}` },
            { label: "95% 上界", value: `$${result.final_p95.toFixed(2)}`, accent: "up" },
            { label: "5% 下界", value: `$${result.final_p5.toFixed(2)}`, accent: "down" },
            { label: "95% VaR", value: `$${result.var_95.toFixed(2)}`, accent: "down" },
            { label: "95% CVaR", value: `$${result.cvar_95.toFixed(2)}`, accent: "down" },
            { label: "亏损概率", value: `${(result.prob_loss * 100).toFixed(1)}%`, accent: result.prob_loss > 0.5 ? "down" : "up" },
            { label: "期望收益率", value: `${(result.expected_return * 100).toFixed(2)}%`, accent: result.expected_return >= 0 ? "up" : "down" },
            { label: "标准差", value: `$${result.final_std.toFixed(2)}` },
          ]} />
          <SectionCard title="Monte Carlo 路径（前20条）">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={pathChartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="t" tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={v => v.toFixed(2)} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={50} tickFormatter={v => `$${v.toFixed(0)}`} />
                <ReferenceLine y={result.S0} stroke="#58a6ff" strokeDasharray="4 4" />
                {Array.from({ length: 20 }, (_, i) => (
                  <Line key={i} dataKey={`p${i}`} dot={false} strokeWidth={0.8} stroke="#3fb950" opacity={0.4} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </SectionCard>
          <SectionCard title="期末价格分布">
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={distData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="price" tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={v => `$${v}`} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={40} />
                <RBar dataKey="count" fill="#58a6ff" opacity={0.8} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">设置参数后点击运行模拟</div>
        )}
      </div>
    </div>
  )
}

// ── BSM Panel ─────────────────────────────────────────────────────

function BSMPanel() {
  const { mutate, isPending, data: result, error } = useBSM()
  const { toast } = useToast()
  const [S, setS] = useState("150")
  const [K, setK] = useState("155")
  const [r, setR] = useState("0.05")
  const [sigma, setSigma] = useState("0.25")
  const [T, setT] = useState("0.25")
  const [optType, setOptType] = useState<"call" | "put">("call")

  function run() {
    const sv = parseFloat(S), kv = parseFloat(K), rv = parseFloat(r), sv2 = parseFloat(sigma), tv = parseFloat(T)
    if ([sv, kv, rv, sv2, tv].some(isNaN)) { toast("请输入有效数字", "warning"); return }
    mutate({ S: sv, K: kv, r: rv, sigma: sv2, T: tv, option_type: optType })
  }

  const greeksColor = (v: number) => v >= 0 ? "text-[#3fb950]" : "text-[#f85149]"

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="期权参数" sub="BSM 定价">
          <div className="flex gap-2 mb-4">
            {(["call", "put"] as const).map(t => (
              <button key={t} onClick={() => setOptType(t)}
                className={`flex-1 py-1.5 rounded text-sm font-medium border transition-colors ${
                  optType === t
                    ? t === "call" ? "bg-[#162a1e] text-[#3fb950] border-[#3fb950]/40" : "bg-[#2a1b1b] text-[#f85149] border-[#f85149]/40"
                    : "text-[#8b949e] border-[#30363d]"
                }`}>
                {t === "call" ? "认购 Call" : "认沽 Put"}
              </button>
            ))}
          </div>
          <ParamRow label="标的现价 S"><input className="input w-28 font-mono" value={S} onChange={e => setS(e.target.value)} /></ParamRow>
          <ParamRow label="行权价 K"><input className="input w-28 font-mono" value={K} onChange={e => setK(e.target.value)} /></ParamRow>
          <ParamRow label="无风险利率 r"><input className="input w-28 font-mono" value={r} onChange={e => setR(e.target.value)} placeholder="0.05" /></ParamRow>
          <ParamRow label="年化波动率 σ"><input className="input w-28 font-mono" value={sigma} onChange={e => setSigma(e.target.value)} placeholder="0.25" /></ParamRow>
          <ParamRow label="到期年数 T"><input className="input w-28 font-mono" value={T} onChange={e => setT(e.target.value)} placeholder="0.25" /></ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "计算定价"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <MetaGrid items={[
            { label: "理论价格", value: `$${result.price.toFixed(4)}` },
            { label: "内在价值", value: `$${result.intrinsic_value.toFixed(4)}` },
            { label: "时间价值", value: `$${result.time_value.toFixed(4)}` },
            { label: "d1", value: result.d1.toFixed(4) },
            { label: "d2", value: result.d2.toFixed(4) },
            { label: "N(d1)", value: result.nd1.toFixed(4) },
          ]} />
          <SectionCard title="Greeks 敏感性">
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
              {[
                { name: "Δ Delta", value: result.delta, desc: "标的价格↑1元，期权价格变化" },
                { name: "Γ Gamma", value: result.gamma, desc: "Delta 的变化率（凸性）" },
                { name: "Θ Theta", value: result.theta, desc: "每天时间衰减损失" },
                { name: "ν Vega",  value: result.vega,  desc: "波动率↑1%，期权价格变化" },
                { name: "ρ Rho",   value: result.rho,   desc: "利率↑1%，期权价格变化" },
              ].map(g => (
                <div key={g.name} className="bg-[#1c2128] border border-[#21262d] rounded-lg p-3 text-center">
                  <p className="text-xs text-[#8b949e] mb-1">{g.name}</p>
                  <p className={`font-mono text-lg font-bold ${greeksColor(g.value)}`}>{g.value.toFixed(4)}</p>
                  <p className="text-[10px] text-[#6e7681] mt-1 leading-tight">{g.desc}</p>
                </div>
              ))}
            </div>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">填写参数后点击计算</div>
        )}
      </div>
    </div>
  )
}

// ── GARCH Panel ───────────────────────────────────────────────────

const DEMO_RETURNS = Array.from({ length: 252 }, (_, i) =>
  +(0.0005 + 0.015 * Math.sin(i / 30) * (1 + Math.random() * 0.5) * (Math.random() > 0.5 ? 1 : -1)).toFixed(5)
)

function GARCHPanel() {
  const { mutate, isPending, data: result, error } = useGARCH()
  const [horizon, setHorizon] = useState("30")

  function run() {
    mutate({ returns: DEMO_RETURNS, forecast_horizon: parseInt(horizon) || 30 })
  }

  const histData = result?.conditional_vol.map((v, i) => ({ t: i, vol: +(v * 100).toFixed(3) })) ?? []
  const forecastData = result?.forecast_vol.map((v, i) => ({ t: i + 1, vol: +(v * 100).toFixed(3) })) ?? []

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="GARCH(1,1) 配置">
          <p className="text-xs text-[#6e7681] mb-4">使用内置演示收益率序列（252 个数据点）</p>
          <ParamRow label="预测步数">
            <select className="select" value={horizon} onChange={e => setHorizon(e.target.value)}>
              {["10","20","30","60","120"].map(v => <option key={v} value={v}>{v}天</option>)}
            </select>
          </ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "拟合 GARCH"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <MetaGrid items={[
            { label: "ω (omega)", value: result.omega.toExponential(3) },
            { label: "α (alpha)", value: result.alpha.toFixed(4), accent: result.alpha > 0.15 ? "down" : "up" },
            { label: "β (beta)",  value: result.beta.toFixed(4) },
            { label: "持续性 α+β", value: result.persistence.toFixed(4), accent: result.persistence > 0.98 ? "down" : "up" },
            { label: "长期年化波动", value: `${(result.long_run_vol_annualized * 100).toFixed(2)}%` },
            { label: "冲击半衰期", value: `${result.half_life_days.toFixed(1)}天` },
            { label: "AIC", value: result.aic.toFixed(2) },
            { label: "对数似然", value: result.log_likelihood.toFixed(2) },
          ]} />
          <SectionCard title="条件波动率（历史）" sub="年化 %">
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={histData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="vol-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="t" tick={{ fill: "#8b949e", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={40} tickFormatter={v => `${v}%`} />
                <Area dataKey="vol" stroke="#58a6ff" strokeWidth={1.5} fill="url(#vol-fill)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </SectionCard>
          <SectionCard title="波动率预测" sub="未来 n 天年化 %">
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={forecastData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="t" tick={{ fill: "#8b949e", fontSize: 10 }} label={{ value: "天", position: "right", fill: "#6e7681", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={40} tickFormatter={v => `${v}%`} />
                <ReferenceLine y={result.long_run_vol_annualized * 100} stroke="#e3b341" strokeDasharray="4 4" label={{ value: "长期均值", fill: "#e3b341", fontSize: 10 }} />
                <Line dataKey="vol" stroke="#3fb950" strokeWidth={2} dot={{ r: 2, fill: "#3fb950" }} />
              </LineChart>
            </ResponsiveContainer>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">点击"拟合 GARCH"运行模型</div>
        )}
      </div>
    </div>
  )
}

// ── Kelly Panel ───────────────────────────────────────────────────

function KellyPanel() {
  const { mutate, isPending, data: result, error } = useKelly()
  const { toast } = useToast()
  const [wr, setWr] = useState("0.55")
  const [aw, setAw] = useState("150")
  const [al, setAl] = useState("100")
  const [frac, setFrac] = useState("0.5")
  const [maxF, setMaxF] = useState("0.25")

  function run() {
    const w = parseFloat(wr), win = parseFloat(aw), loss = parseFloat(al), f = parseFloat(frac), m = parseFloat(maxF)
    if ([w, win, loss, f, m].some(isNaN)) { toast("请输入有效数字", "warning"); return }
    mutate({ win_rate: w, avg_win: win, avg_loss: loss, fraction: f, max_f: m })
  }

  const curveData = result?.growth_curve.filter(d => isFinite(d.expected_log_growth)) ?? []

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="凯利参数">
          <ParamRow label="胜率 (0~1)"><input className="input w-28 font-mono" value={wr} onChange={e => setWr(e.target.value)} placeholder="0.55" /></ParamRow>
          <ParamRow label="平均盈利"><input className="input w-28 font-mono" value={aw} onChange={e => setAw(e.target.value)} placeholder="150" /></ParamRow>
          <ParamRow label="平均亏损"><input className="input w-28 font-mono" value={al} onChange={e => setAl(e.target.value)} placeholder="100" /></ParamRow>
          <ParamRow label="分数凯利比">
            <select className="select" value={frac} onChange={e => setFrac(e.target.value)}>
              <option value="0.25">0.25 (¼ Kelly)</option>
              <option value="0.5">0.50 (½ Kelly)</option>
              <option value="0.75">0.75 (¾ Kelly)</option>
              <option value="1.0">1.00 (Full)</option>
            </select>
          </ParamRow>
          <ParamRow label="最大仓位上限"><input className="input w-28 font-mono" value={maxF} onChange={e => setMaxF(e.target.value)} placeholder="0.25" /></ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "计算仓位"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <MetaGrid items={[
            { label: "盈亏比 b", value: result.odds_ratio.toFixed(3) },
            { label: "期望值 Edge", value: `${(result.edge * 100).toFixed(2)}%`, accent: result.edge > 0 ? "up" : "down" },
            { label: "完整凯利 f*", value: `${(result.full_kelly * 100).toFixed(2)}%` },
            { label: "半凯利 f*/2", value: `${(result.half_kelly * 100).toFixed(2)}%` },
            { label: "¼凯利", value: `${(result.quarter_kelly * 100).toFixed(2)}%` },
            { label: "推荐仓位", value: `${(result.recommended * 100).toFixed(2)}%`, accent: "up" },
            { label: "全凯利破产概率", value: `${(result.ruin_probability_full * 100).toFixed(1)}%`, accent: "down" },
            { label: "半凯利破产概率", value: `${(result.ruin_probability_half * 100).toFixed(1)}%`, accent: "up" },
          ]} />
          <SectionCard title="期望对数增长曲线" sub="不同仓位比例的理论增长率">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={curveData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="f" tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={v => `${(+v * 100).toFixed(0)}%`} label={{ value: "仓位比例", position: "insideBottom", fill: "#6e7681", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={48} />
                <ReferenceLine x={result.full_kelly} stroke="#e3b341" strokeDasharray="4 4" label={{ value: "f*", fill: "#e3b341", fontSize: 11 }} />
                <ReferenceLine x={result.recommended} stroke="#3fb950" strokeDasharray="4 4" label={{ value: "推荐", fill: "#3fb950", fontSize: 11 }} />
                <Line dataKey="expected_log_growth" stroke="#58a6ff" strokeWidth={2} dot={false} name="期望对数增长" />
                <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 12 }} formatter={(v: number) => [v.toFixed(6), "期望对数增长"]} labelFormatter={v => `仓位 ${(+v * 100).toFixed(0)}%`} />
              </LineChart>
            </ResponsiveContainer>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">输入历史胜率和盈亏数据后点击计算</div>
        )}
      </div>
    </div>
  )
}

// ── Cointegration Panel ───────────────────────────────────────────

const DEMO_X = Array.from({ length: 150 }, (_, i) => 100 + i * 0.1 + Math.random() * 3)
const DEMO_Y = DEMO_X.map(x => 1.5 * x + 10 + (Math.random() - 0.5) * 8)

function CointegrationPanel() {
  const { mutate, isPending, data: result, error } = useCointegration()
  const [entryZ, setEntryZ] = useState("2.0")
  const [exitZ, setExitZ] = useState("0.5")
  const [lookback, setLookback] = useState("60")

  function run() {
    mutate({ y: DEMO_Y, x: DEMO_X, lookback: parseInt(lookback), entry_z: parseFloat(entryZ), exit_z: parseFloat(exitZ), use_log: false })
  }

  const zData = result?.z_score_series.map((z, i) => ({ t: i, z: +z.toFixed(3) })) ?? []
  const signalColor = result?.signal === "BUY_SPREAD" ? CHART_COLORS.green : result?.signal === "SELL_SPREAD" ? CHART_COLORS.red : CHART_COLORS.muted

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="协整配置" sub="Engle-Granger">
          <p className="text-xs text-[#6e7681] mb-4">使用内置演示价格序列（Y ≈ 1.5·X + noise）</p>
          <ParamRow label="滚动窗口"><select className="select" value={lookback} onChange={e => setLookback(e.target.value)}>{["30","60","90","120"].map(v => <option key={v} value={v}>{v}天</option>)}</select></ParamRow>
          <ParamRow label="开仓 Z 阈值"><input className="input w-28 font-mono" value={entryZ} onChange={e => setEntryZ(e.target.value)} /></ParamRow>
          <ParamRow label="平仓 Z 阈值"><input className="input w-28 font-mono" value={exitZ} onChange={e => setExitZ(e.target.value)} /></ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "运行检验"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <div className="flex items-center gap-4 mb-2">
            <span className={`text-lg font-bold ${result.is_cointegrated ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {result.is_cointegrated ? "✓ 协整" : "✗ 非协整"}
            </span>
            <span className="badge" style={{ color: signalColor, borderColor: signalColor }}>
              当前信号: {result.signal}
            </span>
          </div>
          <MetaGrid items={[
            { label: "对冲比例 β", value: result.hedge_ratio.toFixed(4) },
            { label: "ADF 统计量", value: result.adf_stat.toFixed(4) },
            { label: "ADF p值", value: result.adf_pvalue.toFixed(4), accent: result.adf_pvalue < 0.05 ? "up" : "down" },
            { label: "当前 Z-score", value: result.z_score_last.toFixed(3), accent: Math.abs(result.z_score_last) > parseFloat(entryZ) ? "down" : "up" },
            { label: "价差均值", value: result.spread_mean.toFixed(4) },
            { label: "价差标准差", value: result.spread_std.toFixed(4) },
            { label: "相关系数", value: result.correlation.toFixed(4) },
            { label: "均值回归半衰期", value: `${result.half_life_days.toFixed(1)}天` },
          ]} />
          <SectionCard title="Z-score 时间序列">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={zData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="z-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="t" tick={{ fill: "#8b949e", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} width={40} />
                <ReferenceLine y={parseFloat(entryZ)} stroke="#f85149" strokeDasharray="4 4" />
                <ReferenceLine y={-parseFloat(entryZ)} stroke="#3fb950" strokeDasharray="4 4" />
                <ReferenceLine y={0} stroke="#6e7681" />
                <Area dataKey="z" stroke="#58a6ff" strokeWidth={1.5} fill="url(#z-fill)" dot={false} name="Z-score" />
              </AreaChart>
            </ResponsiveContainer>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">配置参数后点击运行检验</div>
        )}
      </div>
    </div>
  )
}

// ── HMM Panel ─────────────────────────────────────────────────────

const DEMO_HMM = Array.from({ length: 200 }, (_, i) => {
  const regime = i < 100 ? "bull" : "bear"
  return +(regime === "bull" ? (Math.random() - 0.48) * 0.012 : (Math.random() - 0.52) * 0.025).toFixed(5)
})

function HMMPanel() {
  const { mutate, isPending, data: result, error } = useHMM()
  const [nStates, setNStates] = useState("2")

  function run() { mutate({ returns: DEMO_HMM, n_states: parseInt(nStates) }) }

  const stateColors = ["#3fb950", "#58a6ff", "#f85149", "#e3b341", "#bc8cff"]
  const seqData = result?.state_sequence.map((s, i) => ({ t: i, state: s, r: DEMO_HMM[i] })) ?? []

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="HMM 配置">
          <p className="text-xs text-[#6e7681] mb-4">使用内置演示收益率（前100牛市，后100熊市）</p>
          <ParamRow label="状态数">
            <select className="select" value={nStates} onChange={e => setNStates(e.target.value)}>
              <option value="2">2 (牛/熊)</option>
              <option value="3">3 (牛/震/熊)</option>
            </select>
          </ParamRow>
          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "识别状态"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>
      </div>

      <div className="xl:col-span-3 space-y-4">
        {result && <>
          <div className="flex items-center gap-4 flex-wrap mb-2">
            <span className="text-sm text-[#8b949e]">当前状态:</span>
            <span className="font-bold text-lg" style={{ color: stateColors[result.current_state] }}>
              {result.state_labels[result.current_state]}
            </span>
            <span className="text-xs text-[#6e7681]">({(result.current_state_prob * 100).toFixed(1)}% 置信)</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {result.state_labels.map((label, k) => (
              <div key={k} className="bg-[#1c2128] border border-[#21262d] rounded-lg p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-3 h-3 rounded-full" style={{ background: stateColors[k] }} />
                  <span className="text-sm text-[#e6edf3] font-medium">{label}</span>
                </div>
                <p className="text-xs text-[#8b949e]">年化收益: <span className="font-mono text-[#e6edf3]">{(result.state_means[k] * 100).toFixed(1)}%</span></p>
                <p className="text-xs text-[#8b949e]">年化波动: <span className="font-mono text-[#e6edf3]">{(result.state_vols[k] * 100).toFixed(1)}%</span></p>
              </div>
            ))}
          </div>
          <SectionCard title="状态序列" sub="Viterbi 解码">
            <ResponsiveContainer width="100%" height={160}>
              <ScatterChart margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="t" type="number" tick={{ fill: "#8b949e", fontSize: 10 }} />
                <YAxis dataKey="state" type="number" ticks={Array.from({ length: result.n_states }, (_, i) => i)}
                  tickFormatter={i => result.state_labels[i] ?? `S${i}`} tick={{ fill: "#8b949e", fontSize: 10 }} width={55} />
                {result.state_labels.map((_, k) => (
                  <Scatter key={k} data={seqData.filter(d => d.state === k)} fill={stateColors[k]} opacity={0.7} r={2} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </SectionCard>
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">点击"识别状态"运行 HMM</div>
        )}
      </div>
    </div>
  )
}


// ── Strategy Editor Panel ─────────────────────────────────────────

function StrategyEditorPanel() {
  const { data: presets = [] } = usePresets()
  const { mutate: validate, isPending: validating, data: validResult } = useValidateStrategy()
  const { toast } = useToast()

  const [selectedPreset, setSelectedPreset] = useState<string>("blank")
  const [code, setCode] = useState<string>("")
  const [loadKey, setLoadKey] = useState<string>("blank")

  const { data: sourceData, isLoading: sourceLoading } = useStrategySource(loadKey)

  // When source is fetched, populate the editor
  if (sourceData && sourceData.source && sourceData.source !== code && loadKey) {
    setCode(sourceData.source)
  }

  function handleLoadTemplate() {
    setLoadKey(selectedPreset === loadKey ? selectedPreset + "_" : selectedPreset)
    setCode("")  // will be replaced when query resolves
  }

  function handleValidate() {
    if (!code.trim()) { toast("请先输入策略代码", "warning"); return }
    validate({ code })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      {/* Sidebar */}
      <div className="xl:col-span-1 space-y-4">
        {/* Template Picker */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">模板选择</h3>
          <div className="space-y-1.5 mb-3">
            <label className="label block mb-1">内置模板</label>
            <button
              onClick={() => setSelectedPreset("blank")}
              className={`w-full text-left px-3 py-1.5 rounded text-xs transition-colors ${
                selectedPreset === "blank"
                  ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                  : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
              }`}
            >
              📄 空白模板
            </button>
            {presets.map((p) => (
              <button
                key={p.name}
                onClick={() => setSelectedPreset(p.name)}
                className={`w-full text-left px-3 py-1.5 rounded text-xs transition-colors ${
                  selectedPreset === p.name
                    ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                    : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
                }`}
                title={p.description}
              >
                📊 {p.name}
              </button>
            ))}
          </div>
          <button
            className="btn btn-secondary w-full text-sm"
            onClick={handleLoadTemplate}
            disabled={sourceLoading}
          >
            {sourceLoading ? <Spinner size="sm" className="mx-auto" /> : "加载模板"}
          </button>
        </div>

        {/* Validate */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">代码验证</h3>
          <button
            className="btn btn-primary w-full"
            onClick={handleValidate}
            disabled={validating}
          >
            {validating ? <Spinner size="sm" className="mx-auto" /> : "✓ 验证代码"}
          </button>

          {validResult && (
            <div className="mt-3 space-y-2">
              {/* Status */}
              <div className={`flex items-center gap-2 text-sm font-medium ${
                validResult.valid ? "text-[#3fb950]" : "text-[#f85149]"
              }`}>
                <span>{validResult.valid ? "✓" : "✗"}</span>
                <span>{validResult.valid ? "验证通过" : "存在错误"}</span>
              </div>

              {/* Errors */}
              {validResult.errors.map((e, i) => (
                <div key={i} className="text-xs text-[#f85149] bg-[#2a1b1b] rounded px-2 py-1.5 leading-snug">
                  {e}
                </div>
              ))}

              {/* Warnings */}
              {validResult.warnings.map((w, i) => (
                <div key={i} className="text-xs text-[#e3b341] bg-[#2a2010] rounded px-2 py-1.5 leading-snug">
                  ⚠ {w}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Help */}
        <div className="card text-xs text-[#8b949e] space-y-1.5">
          <p className="font-semibold text-[#e6edf3]">API 速查</p>
          <p><code className="text-[#79c0ff]">ctx.bar</code> — 当前 K 线</p>
          <p><code className="text-[#79c0ff]">ctx.history</code> — DataFrame</p>
          <p><code className="text-[#79c0ff]">ctx.cash</code> — 可用资金</p>
          <p><code className="text-[#79c0ff]">ctx.qty</code> — 当前仓位</p>
          <p><code className="text-[#79c0ff]">ctx.buy(qty)</code> — 买入</p>
          <p><code className="text-[#79c0ff]">ctx.sell(qty)</code> — 卖出</p>
          <p><code className="text-[#79c0ff]">ctx.sell_all()</code> — 清仓</p>
        </div>
      </div>

      {/* Monaco Editor */}
      <div className="xl:col-span-3">
        <div className="card p-0 overflow-hidden" style={{ height: 560 }}>
          <div className="flex items-center justify-between px-4 py-2 bg-[#1c2128] border-b border-[#21262d]">
            <span className="text-xs text-[#8b949e] font-mono">strategy.py</span>
            <span className="text-xs text-[#6e7681]">Python · StrategyBase</span>
          </div>
          <Editor
            height="calc(100% - 37px)"
            defaultLanguage="python"
            value={code}
            onChange={(v) => setCode(v ?? "")}
            theme="vs-dark"
            options={{
              fontSize: 13,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              minimap: { enabled: false },
              lineNumbers: "on",
              wordWrap: "on",
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              renderLineHighlight: "line",
              bracketPairColorization: { enabled: true },
            }}
          />
        </div>
        <p className="text-xs text-[#6e7681] mt-2 px-1">
          提示：编辑完成后点击「验证代码」检查语法，然后前往「回测」页面运行策略。
        </p>
      </div>
    </div>
  )
}

// ── AlgoLab Page ──────────────────────────────────────────────────

export function AlgoLab() {
  const [tab, setTab] = useState<AlgoTab>("gbm")

  return (
    <AppShell title="算法实验室" help={PAGE_HELP.algolab}>
      {/* Tab Bar */}
      <div className="flex flex-wrap gap-2 mb-6 border-b border-[#21262d] pb-4">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium border transition-colors ${
              tab === t.id
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
            }`}
          >
            {t.label}
            <span className="ml-1.5 text-xs text-[#6e7681] hidden sm:inline">· {t.cn}</span>
          </button>
        ))}
      </div>

      {/* Panel */}
      {tab === "gbm"   && <GBMPanel />}
      {tab === "bsm"   && <BSMPanel />}
      {tab === "garch" && <GARCHPanel />}
      {tab === "kelly" && <KellyPanel />}
      {tab === "coint" && <CointegrationPanel />}
      {tab === "hmm"    && <HMMPanel />}
      {tab === "editor" && <StrategyEditorPanel />}
      {tab === "ml"     && <MLPanel />}
    </AppShell>
  )
}
