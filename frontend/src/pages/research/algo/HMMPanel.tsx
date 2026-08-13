// HMM 市场状态面板：Viterbi 解码牛/熊/震荡状态并给出当前状态置信度。
import { useState } from "react"
import {
  ScatterChart, Scatter, XAxis, YAxis,
  CartesianGrid, ResponsiveContainer,
} from "recharts"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useHMM } from "@/hooks/useQuant"
import { ParamRow, SectionCard } from "./shared"

const DEMO_HMM = Array.from({ length: 200 }, (_, i) => {
  const regime = i < 100 ? "bull" : "bear"
  return +(regime === "bull" ? (Math.random() - 0.48) * 0.012 : (Math.random() - 0.52) * 0.025).toFixed(5)
})

export function HMMPanel() {
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
          {(() => {
            const curState = result.current_state
            const curLabel = result.state_labels[curState]
            const curMean = result.state_means[curState]
            const curVol = result.state_vols[curState]
            // Find best and worst state by mean
            const bestStateIdx = result.state_means.indexOf(Math.max(...result.state_means))
            const worstStateIdx = result.state_means.indexOf(Math.min(...result.state_means))
            const isBullish = curState === bestStateIdx
            const isBearish = curState === worstStateIdx
            const verdict = isBullish ? "good" : isBearish ? "bad" : "warn"
            return (
              <InsightBox
                verdict={verdict}
                summary={`HMM 识别当前市场状态为「${curLabel}」（置信 ${(result.current_state_prob*100).toFixed(1)}%），${nStates}状态模型中${isBullish ? "处于最优状态，可适当积极" : isBearish ? "处于最差状态，建议防御" : "处于中间状态，维持中性仓位"}。`}
                findings={[
                  { text: `当前状态「${curLabel}」年化收益预期 ${(curMean*100).toFixed(1)}%，年化波动 ${(curVol*100).toFixed(1)}%`, type: curMean > 0 ? "good" : "bad" },
                  { text: `置信度 ${(result.current_state_prob*100).toFixed(1)}% — ${result.current_state_prob > 0.8 ? "状态判断高度确信" : result.current_state_prob > 0.6 ? "状态判断较为确信" : "状态判断不确定，可能处于状态切换期"}`, type: result.current_state_prob > 0.7 ? "good" : "warn" },
                  ...result.state_labels.map((label, k) => ({
                    text: `状态「${label}」：年化收益 ${(result.state_means[k]*100).toFixed(1)}%，波动 ${(result.state_vols[k]*100).toFixed(1)}%`,
                    type: result.state_means[k] > 0.05 ? "good" as const : result.state_means[k] < -0.05 ? "bad" as const : "neutral" as const,
                  })),
                ]}
                recommendations={[
                  { text: isBullish ? "当前牛市状态，适度提升权益仓位" : isBearish ? "当前熊市状态，降低权益仓位，增加现金或对冲" : "震荡状态，维持中性配置，等待方向明确", type: verdict },
                  { text: "监控状态切换时机", sub: "当置信度低于 60% 时表明状态可能正在转换，提前调整仓位", type: "warn" },
                  { text: "与技术指标交叉验证", sub: "HMM 结果建议与趋势指标（如 ADX、SuperTrend）结合使用，单一模型不宜单独决策", type: "neutral" },
                ]}
              />
            )
          })()}
        </>}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">点击"识别状态"运行 HMM</div>
        )}
      </div>
    </div>
  )
}
