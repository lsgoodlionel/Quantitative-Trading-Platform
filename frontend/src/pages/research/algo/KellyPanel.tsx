// 凯利准则面板：由胜率/盈亏比推荐仓位，并给出期望对数增长曲线。
import { useState } from "react"
import { Link } from "react-router-dom"
import {
  LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useKelly } from "@/hooks/useQuant"
import { MetaGrid, ParamRow, SectionCard } from "./shared"

export function KellyPanel() {
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
          <InsightBox
            verdict={result.edge > 0 ? "good" : "bad"}
            summary={`当前策略期望值（Edge）为 ${(result.edge*100).toFixed(2)}%，盈亏比 b=${result.odds_ratio.toFixed(2)}。完整凯利仓位 f*=${(result.full_kelly*100).toFixed(1)}%，推荐使用 ${(result.recommended*100).toFixed(1)}% 仓位（${frac === "0.5" ? "½" : frac === "0.25" ? "¼" : frac === "0.75" ? "¾" : "Full"} Kelly）。`}
            findings={[
              { text: `期望值 ${(result.edge*100).toFixed(2)}% — ${result.edge > 0 ? "正期望策略，具备长期盈利能力" : "负期望策略，任何仓位都会长期亏损"}`, type: result.edge > 0 ? "good" : "bad" },
              { text: `完整凯利 f*=${(result.full_kelly*100).toFixed(1)}%，半凯利=${(result.half_kelly*100).toFixed(2)}%`, sub: "实战中通常使用半凯利或¼凯利以降低破产风险", type: "neutral" },
              { text: `全凯利破产概率 ${(result.ruin_probability_full*100).toFixed(1)}% vs 半凯利破产概率 ${(result.ruin_probability_half*100).toFixed(1)}%`, type: result.ruin_probability_full > 0.2 ? "warn" : "good" },
              ...(result.full_kelly > parseFloat(maxF) ? [{ text: `凯利最优仓位 ${(result.full_kelly*100).toFixed(1)}% 超过设定上限 ${(parseFloat(maxF)*100).toFixed(0)}%，已受上限约束`, type: "warn" as const }] : []),
            ]}
            recommendations={[
              ...(result.edge <= 0 ? [{ text: "负期望策略请勿实盘", sub: "改善策略的胜率或盈亏比，直到期望值转为正数再考虑实盘", type: "bad" as const }] : []),
              { text: `实际建议仓位 ${(result.recommended*100).toFixed(1)}%`, sub: "在「实盘策略 → 启动」中，单笔资金使用比例参考此值，不建议超过¼ Kelly", type: result.edge > 0 ? "good" : "warn" },
              { text: "随策略表现动态调整", sub: "策略实盘运行后，根据实际胜率和盈亏比重新输入计算，每季度至少更新一次", type: "neutral" },
            ]}
          />
        </>}
        {/* Kelly → 交易应用 */}
        {result && result.edge > 0 && (
          <div className="card border-[#3fb950]/25 space-y-2">
            <p className="text-xs font-semibold text-[#3fb950]">✅ 正期望策略 — 如何应用凯利仓位</p>
            <div className="flex flex-wrap gap-2">
              <Link to="/trading?tab=live"
                className="px-3 py-2 rounded-lg text-xs border border-[#3fb950]/30 text-[#3fb950] bg-[#0d2018] hover:bg-[#3fb950]/15 transition-colors">
                <p className="font-medium">▶ 启动模拟盘</p>
                <p className="text-[9px] text-[#3fb950]/70 mt-0.5">在策略参数中，每笔仓位不超过 {(result.recommended*100).toFixed(0)}%</p>
              </Link>
              <Link to="/backtest"
                className="px-3 py-2 rounded-lg text-xs border border-[#58a6ff]/30 text-[#58a6ff] bg-[#111d2e] hover:bg-[#58a6ff]/15 transition-colors">
                <p className="font-medium">🔬 验证策略胜率</p>
                <p className="text-[9px] text-[#58a6ff]/70 mt-0.5">先回测确认胜率和盈亏比再实盘</p>
              </Link>
              <Link to="/risk"
                className="px-3 py-2 rounded-lg text-xs border border-[#e3b341]/30 text-[#e3b341] bg-[#1a1400] hover:bg-[#e3b341]/15 transition-colors">
                <p className="font-medium">🛡️ 设置风控上限</p>
                <p className="text-[9px] text-[#e3b341]/70 mt-0.5">将 {(result.recommended*100).toFixed(0)}% 作为最大持仓比例</p>
              </Link>
            </div>
          </div>
        )}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">输入历史胜率和盈亏数据后点击计算</div>
        )}
      </div>
    </div>
  )
}
