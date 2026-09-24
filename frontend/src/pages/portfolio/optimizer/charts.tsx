// 组合优化结果的两张图：权重饼图与有效前沿散点（纯展示，无自身状态）。
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceDot, Cell, PieChart, Pie,
} from "recharts"
import { PALETTE, type PortfolioOptResult } from "./options"

export function WeightPieChart({ weights }: { weights: Record<string, number> }) {
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

export function EfficientFrontierChart({
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
