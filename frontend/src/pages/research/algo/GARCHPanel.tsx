// GARCH(1,1) 面板：条件波动率拟合与未来波动率预测。
import { useState } from "react"
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, CartesianGrid, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useGARCH } from "@/hooks/useQuant"
import { MetaGrid, ParamRow, SectionCard } from "./shared"

const DEMO_RETURNS = Array.from({ length: 252 }, (_, i) =>
  +(0.0005 + 0.015 * Math.sin(i / 30) * (1 + Math.random() * 0.5) * (Math.random() > 0.5 ? 1 : -1)).toFixed(5)
)

export function GARCHPanel() {
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
          <InsightBox
            verdict={result.persistence > 0.98 ? "warn" : result.persistence > 0.9 ? "neutral" : "good"}
            summary={`GARCH(1,1) 拟合完成。波动率持续性 α+β=${result.persistence.toFixed(4)}，${result.persistence > 0.98 ? "接近单位根（非平稳），波动率冲击衰减极慢" : "波动率均值回归能力正常"}。长期均衡年化波动率为 ${(result.long_run_vol_annualized*100).toFixed(2)}%。`}
            findings={[
              { text: `α（ARCH项）=${result.alpha.toFixed(4)} — ${result.alpha > 0.15 ? "对新信息的反应过度（波动率杠杆效应显著）" : "对新信息反应适中"}`, type: result.alpha > 0.15 ? "warn" : "neutral" },
              { text: `β（GARCH项）=${result.beta.toFixed(4)} — 波动率记忆性，β越高历史波动率影响越持久`, type: "neutral" },
              { text: `冲击半衰期 ${result.half_life_days.toFixed(1)} 天 — ${result.half_life_days > 60 ? "极长，波动率冲击需要数月消散，适合配置低频止损" : result.half_life_days > 20 ? "正常，约1个月消散" : "较短，市场恢复快"}`, type: result.half_life_days > 60 ? "warn" : "neutral" },
              { text: `长期年化波动率 ${(result.long_run_vol_annualized*100).toFixed(2)}% — 用于设置 VaR 压力测试的基准波动率参数`, type: "neutral" },
            ]}
            recommendations={[
              { text: "将长期波动率代入 BSM 期权定价", sub: `当前长期波动率 ${(result.long_run_vol_annualized*100).toFixed(2)}% 可作为 σ 参数输入「BSM期权」面板`, type: "neutral" },
              ...(result.persistence > 0.97 ? [{ text: "波动率持续性极高，慎用简单移动平均止损", sub: "建议改用 GARCH 动态止损：止损宽度 = 2 × GARCH预测σ × 持仓成本", type: "warn" as const }] : []),
              { text: "结合预测区间动态调整仓位", sub: `未来 ${horizon} 天预测波动率 ${forecastData.length ? (forecastData[forecastData.length-1].vol).toFixed(2) : "—"}% vs 长期均值 ${(result.long_run_vol_annualized*100).toFixed(2)}%，波动率上升时降低仓位`, type: "neutral" },
            ]}
          />
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">点击"拟合 GARCH"运行模型</div>
        )}
      </div>
    </div>
  )
}
