interface StatCardProps {
  label: string
  value: string
  sub?: string
  accent?: "up" | "down"
  help?: string
}

/** 与 Backtest 总览 MetricCard 同款的紧凑指标卡，供 Tearsheet / 交易分析复用 */
export function StatCard({ label, value, sub, accent, help }: StatCardProps) {
  return (
    <div className="card py-3 group relative">
      <p className="text-xs text-[#6e7681] mb-1 flex items-center gap-1">
        {label}
        {help && (
          <span className="text-[10px] text-[#3d444d] cursor-help" title={help}>ⓘ</span>
        )}
      </p>
      <p className={`font-mono text-base font-semibold ${
        accent === "up" ? "text-[#3fb950]" : accent === "down" ? "text-[#f85149]" : "text-[#e6edf3]"
      }`}>{value}</p>
      {sub && <p className="text-[10px] text-[#6e7681] mt-0.5">{sub}</p>}
    </div>
  )
}
