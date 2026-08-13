// BSM 期权定价面板：理论价格 + Greeks 敏感性。
import { useState } from "react"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useBSM } from "@/hooks/useQuant"
import { MetaGrid, ParamRow, SectionCard } from "./shared"

export function BSMPanel() {
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
          {(() => {
            const sv = parseFloat(S), kv = parseFloat(K)
            const moneyness = sv / kv
            const mLabel = moneyness > 1.03 ? "实值（ITM）" : moneyness < 0.97 ? "虚值（OTM）" : "平值（ATM）"
            const optLabel = optType === "call" ? "认购" : "认沽"
            const isHighGamma = result.gamma > 0.05
            const isHighVega = result.vega > 5
            return (
              <InsightBox
                verdict={result.intrinsic_value > 0 ? "good" : "warn"}
                summary={`${optLabel}期权理论价格 $${result.price.toFixed(4)}，当前处于${mLabel}（S/K=${moneyness.toFixed(3)}），内在价值 $${result.intrinsic_value.toFixed(4)}，时间价值 $${result.time_value.toFixed(4)}。`}
                findings={[
                  { text: `Delta ${result.delta.toFixed(4)} — 标的每涨$1，期权价值变化 $${result.delta.toFixed(4)}，实际持仓对冲比约 1:${(1/Math.abs(result.delta)).toFixed(0)}`, type: "neutral" },
                  { text: `Theta ${result.theta.toFixed(4)} — 每日时间衰减 $${Math.abs(result.theta).toFixed(4)}，${parseFloat(T) < 0.1 ? "临近到期时间价值加速损耗，风险较高" : "时间价值损耗可控"}`, type: parseFloat(T) < 0.1 ? "warn" : "neutral" },
                  { text: `Gamma ${result.gamma.toFixed(4)} — ${isHighGamma ? "Gamma 较高，Delta 变化剧烈，需要频繁对冲" : "Gamma 较低，Delta 较稳定"}`, type: isHighGamma ? "warn" : "neutral" },
                  { text: `Vega ${result.vega.toFixed(4)} — ${isHighVega ? "波动率敏感性高，隐含波动率1%变化影响期权价值$" + result.vega.toFixed(2) : "波动率敏感性适中"}`, type: isHighVega ? "warn" : "neutral" },
                ]}
                recommendations={[
                  { text: `Delta 对冲需持有 ${Math.abs(result.delta * 100).toFixed(0)} 股${optLabel === "认购" ? "标的" : ""}以对冲 100 张期权`, sub: "每日 Delta 对冲成本 = Gamma × 标的日内波动²/2，请权衡对冲频率", type: "neutral" },
                  ...(parseFloat(T) < 0.05 ? [{ text: "临近到期，时间价值损耗加速", sub: "若处于虚值状态，当前期权大概率归零，注意资金安全", type: "bad" as const }] : []),
                  { text: "对比市场实际期权价格", sub: "BSM 理论价格 vs 市场报价的差值即「隐含波动率溢价」，可作为期权高估/低估的参考", type: "neutral" },
                ]}
              />
            )
          })()}
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">填写参数后点击计算</div>
        )}
      </div>
    </div>
  )
}
