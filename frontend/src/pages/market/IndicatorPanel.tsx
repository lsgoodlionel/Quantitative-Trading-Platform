// 行情查询 Tab 下方的技术指标副图：按当前选中的指标渲染对应图形。
import {
  LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import type { IndicatorKey } from "@/hooks/useIndicators"

// ── 指标面板 ──────────────────────────────────────────────────
interface IndicatorPanelProps {
  indicatorData: Record<string, (number | null)[]>
  times: string[]
  selectedIndicator: IndicatorKey
}

export function IndicatorPanel({ indicatorData, times, selectedIndicator }: IndicatorPanelProps) {
  const chartData = times.map((t, i) => {
    const pt: Record<string, number | string | null> = { time: t.slice(0, 10) }
    for (const [key, vals] of Object.entries(indicatorData)) {
      pt[key] = (vals as (number | null)[])[i]
    }
    return pt
  })

  const hasData = (k: string) => indicatorData[k]?.some((v) => v != null)

  // RSI
  if (selectedIndicator === "rsi" && hasData("rsi")) {
    return (
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis domain={[0, 100]} tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={36} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
            formatter={(v: number) => [v?.toFixed(2), "RSI"]} labelFormatter={(l) => l} />
          <ReferenceLine y={70} stroke="#f85149" strokeDasharray="4 2" />
          <ReferenceLine y={30} stroke="#3fb950" strokeDasharray="4 2" />
          <Line type="monotone" dataKey="rsi" stroke="#e3b341" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // MACD
  if (selectedIndicator === "macd" && hasData("macd")) {
    return (
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={52} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }} />
          <ReferenceLine y={0} stroke="#30363d" />
          <Line type="monotone" dataKey="macd" stroke="#58a6ff" strokeWidth={1.5} dot={false} name="MACD" isAnimationActive={false} />
          <Line type="monotone" dataKey="macd_signal" stroke="#f85149" strokeWidth={1.5} dot={false} name="Signal" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // CCI / Williams %R / ROC / MFI / ADX / ATR / OBV
  const singleLineMap: Record<string, { key: string; label: string; color: string }> = {
    cci:        { key: "cci",        label: "CCI",  color: "#bc8cff" },
    williams_r: { key: "williams_r", label: "W%R",  color: "#ff9f43" },
    roc:        { key: "roc",        label: "ROC",  color: "#54a0ff" },
    mfi:        { key: "mfi",        label: "MFI",  color: "#00d2d3" },
    adx:        { key: "adx",        label: "ADX",  color: "#e3b341" },
    atr:        { key: "atr",        label: "ATR",  color: "#8b949e" },
    obv:        { key: "obv",        label: "OBV",  color: "#3fb950" },
  }
  const single = singleLineMap[selectedIndicator]
  if (single && hasData(single.key)) {
    return (
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={52} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
            formatter={(v: number) => [v?.toFixed(2), single.label]} />
          <ReferenceLine y={0} stroke="#30363d" />
          <Line type="monotone" dataKey={single.key} stroke={single.color} strokeWidth={1.5} dot={false} name={single.label} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // KDJ
  if (selectedIndicator === "stoch" && hasData("stoch_k")) {
    return (
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis domain={[0, 100]} tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} width={36} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }} />
          <ReferenceLine y={80} stroke="#f85149" strokeDasharray="4 2" />
          <ReferenceLine y={20} stroke="#3fb950" strokeDasharray="4 2" />
          <Line type="monotone" dataKey="stoch_k" stroke="#58a6ff" strokeWidth={1.5} dot={false} name="K" isAnimationActive={false} />
          <Line type="monotone" dataKey="stoch_d" stroke="#f85149" strokeWidth={1.5} dot={false} name="D" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    )
  }

  return <div className="text-[#6e7681] text-xs text-center py-4">正在加载指标数据…</div>
}
