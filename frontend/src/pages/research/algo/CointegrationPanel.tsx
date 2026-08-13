// 协整检验面板：Engle-Granger 检验 + 价差 Z-score 时序与配对交易信号。
import { useState } from "react"
import { Link } from "react-router-dom"
import {
  AreaChart, Area, XAxis, YAxis,
  CartesianGrid, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { InsightBox } from "@/components/ui/InsightBox"
import { Spinner } from "@/components/ui/Spinner"
import { useCointegration } from "@/hooks/useQuant"
import { CHART_COLORS, MetaGrid, ParamRow, SectionCard } from "./shared"

const DEMO_X = Array.from({ length: 150 }, (_, i) => 100 + i * 0.1 + Math.random() * 3)
const DEMO_Y = DEMO_X.map(x => 1.5 * x + 10 + (Math.random() - 0.5) * 8)

export function CointegrationPanel() {
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
          <InsightBox
            verdict={result.is_cointegrated ? (result.half_life_days < 30 ? "good" : "warn") : "bad"}
            summary={`Engle-Granger 协整检验 ${result.is_cointegrated ? "通过（ADF p值=" + result.adf_pvalue.toFixed(4) + "）" : "未通过（p值=" + result.adf_pvalue.toFixed(4) + " > 0.05，价差非平稳）"}。当前价差 Z-score=${result.z_score_last.toFixed(3)}，当前信号：${result.signal}。`}
            findings={[
              { text: `协整关系：${result.is_cointegrated ? "✓ 两序列存在长期均衡关系，价差具有均值回归性" : "✗ 未发现协整关系，配对交易假设不成立"}`, type: result.is_cointegrated ? "good" : "bad" },
              { text: `均值回归半衰期 ${result.half_life_days.toFixed(1)} 天 — ${result.half_life_days < 10 ? "回归极快，适合短线配对" : result.half_life_days < 30 ? "回归速度适中" : "回归较慢，需要较长持仓周期"}`, type: result.half_life_days < 30 ? "good" : "warn" },
              { text: `当前 Z-score ${result.z_score_last.toFixed(3)} — ${Math.abs(result.z_score_last) > parseFloat(entryZ) ? `超过开仓阈值 ±${entryZ}，满足开仓条件` : `在阈值 ±${entryZ} 内，暂无开仓信号`}`, type: Math.abs(result.z_score_last) > parseFloat(entryZ) ? "warn" : "neutral" },
              { text: `对冲比例 β=${result.hedge_ratio.toFixed(4)}，价差相关系数 ${result.correlation.toFixed(4)}`, type: result.correlation > 0.8 ? "good" : "warn" },
            ]}
            recommendations={[
              ...(result.is_cointegrated ? [{
                text: `当前信号「${result.signal}」`,
                sub: result.signal === "BUY_SPREAD" ? `买入 Y 同时卖出 ${result.hedge_ratio.toFixed(2)} 单位 X，等待价差回归均值` : result.signal === "SELL_SPREAD" ? `卖出 Y 同时买入 ${result.hedge_ratio.toFixed(2)} 单位 X` : "价差处于中性区间，持仓等待或平仓",
                type: result.signal !== "HOLD" ? ("good" as const) : ("neutral" as const),
              }] : [{ text: "当前资产组合不满足协整条件，更换配对标的", sub: "尝试选择同行业、同市场、相关性 > 0.8 的两只股票重新检验", type: "bad" as const }]),
              { text: "滚动更新对冲比例", sub: "β 随市场结构变化而漂移，建议每 30 天重新估算并调整仓位比例", type: "neutral" },
              { text: `止损设置：当 |Z-score| > ${(parseFloat(entryZ) * 1.5).toFixed(1)} 时强制平仓`, sub: "价差持续扩大可能意味着协整关系破裂，须及时止损", type: "warn" },
            ]}
          />
        </>}
        {/* 协整 → 配对策略回测 */}
        {result?.is_cointegrated && (
          <div className="card border-[#3fb950]/25 space-y-2">
            <p className="text-xs font-semibold text-[#3fb950]">✅ 协整关系成立 — 下一步</p>
            <div className="flex flex-wrap gap-2">
              <Link to="/backtest?strategy=pairs_trading"
                className="px-3 py-1.5 rounded text-xs border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                🔬 用配对套利策略回测
              </Link>
              <Link to="/trading?tab=live&strategy=pairs_trading"
                className="px-3 py-1.5 rounded text-xs border border-[#3fb950]/30 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors">
                ▶ 启动配对套利模拟盘
              </Link>
            </div>
          </div>
        )}
        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">配置参数后点击运行检验</div>
        )}
      </div>
    </div>
  )
}
