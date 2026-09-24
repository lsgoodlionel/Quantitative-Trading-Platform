// GBM 蒙特卡洛面板：几何布朗运动路径模拟 + 期末价格分布。
import { useState } from "react"
import {
  LineChart, Line, BarChart, Bar as RBar,
  XAxis, YAxis, CartesianGrid, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useGBM } from "@/hooks/useQuant"
import { MetaGrid, ParamRow, SectionCard } from "./shared"

export function GBMPanel() {
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
          <InsightBox
            verdict={result.prob_loss > 0.5 ? "bad" : result.prob_loss > 0.3 ? "warn" : "good"}
            summary={`在 μ=${(parseFloat(mu)*100).toFixed(1)}%、σ=${(parseFloat(sigma)*100).toFixed(1)}% 条件下，模拟 ${nPaths} 条路径后期望价格为 $${result.final_mean.toFixed(2)}（起点 $${result.S0}），亏损概率 ${(result.prob_loss*100).toFixed(1)}%。`}
            findings={[
              { text: `期望收益率 ${(result.expected_return*100).toFixed(2)}% — ${result.expected_return > 0 ? "漂移率为正，长期向上趋势占优" : "漂移率为负，长期预期亏损"}`, type: result.expected_return > 0 ? "good" : "bad" },
              { text: `亏损概率 ${(result.prob_loss*100).toFixed(1)}% — ${result.prob_loss > 0.5 ? "超过50%，该价格路径在该参数下大概率亏损" : result.prob_loss > 0.3 ? "亏损概率偏高（30%+），需要风控止损" : "亏损概率可控"}`, type: result.prob_loss > 0.5 ? "bad" : result.prob_loss > 0.3 ? "warn" : "good" },
              { text: `95% 区间 $${result.final_p5.toFixed(2)} ～ $${result.final_p95.toFixed(2)}，价格波动幅度 ${(((result.final_p95-result.final_p5)/result.S0)*100).toFixed(1)}%`, type: "neutral" },
              { text: `95% CVaR（预期亏损）$${result.cvar_95.toFixed(2)} — 极端情景下的平均损失`, type: result.cvar_95 > result.S0 * 0.3 ? "bad" : "warn" },
            ]}
            recommendations={[
              { text: "σ 参数对应真实标的年化波动率", sub: "可在「风控→VaR分析」查询当前持仓真实波动率，代入此处模拟", type: "neutral" },
              { text: "蒙卡结果仅供参考，非收益承诺", sub: "GBM 假设对数正态分布和恒定波动率，实际市场存在跳跃和波动率聚集", type: "warn" },
              ...(result.prob_loss > 0.4 ? [{ text: "在参数相似条件下建议使用止损", sub: `若亏损超过 VaR95 $${result.var_95.toFixed(2)} 即触发止损，可有效控制尾部损失`, type: "warn" as const }] : []),
            ]}
          />
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">设置参数后点击运行模拟</div>
        )}
      </div>
    </div>
  )
}
