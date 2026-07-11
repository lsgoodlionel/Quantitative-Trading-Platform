import { RollingLine } from "@/components/charts/RollingLine"
import { RollingArea } from "@/components/charts/RollingArea"
import { EmptyState } from "@/components/ui/EmptyState"
import { StatCard } from "./StatCard"
import type { RollingStats, DrawdownPeriod } from "@/hooks/useBacktestReport"

interface TearsheetTabProps {
  rolling: RollingStats | null | undefined
  drawdownPeriods: DrawdownPeriod[]
}

function pct(v: number): string {
  return `${v.toFixed(2)}%`
}

export function TearsheetTab({ rolling, drawdownPeriods }: TearsheetTabProps) {
  if (!rolling || rolling.cum_returns.length === 0) {
    return (
      <div className="card">
        <EmptyState
          title="暂无 Tearsheet 数据"
          description="净值序列过短，无法生成滚动分析（至少需要 2 个交易日）"
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* 1. 头部标量条 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatCard label="Beta（全样本）" value={rolling.beta.toFixed(3)}
          help="策略收益对基准收益的 OLS 斜率" />
        <StatCard label="年化 Alpha" value={pct(rolling.alpha_annual_pct)}
          accent={rolling.alpha_annual_pct >= 0 ? "up" : "down"}
          help="全样本回归截距 × 年化周期" />
        <StatCard label="平均仓位暴露" value={pct(rolling.avg_exposure_pct)}
          help="有仓 bar 占比（0~100%）" />
        <StatCard label="累计换手" value={rolling.total_turnover.toFixed(2)}
          help="累计成交名义 / 平均净值" />
      </div>

      {/* 2. 累计收益（增长$1） */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          累计收益 <span className="text-xs text-[#6e7681] font-normal">（$1 增长曲线）</span>
        </h3>
        <RollingArea data={rolling.cum_returns} color="#58a6ff" height={220}
          label="累计增长" valueFormatter={(v) => `${v.toFixed(2)}×`} />
      </div>

      {/* 3. 滚动夏普 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          滚动夏普 <span className="text-xs text-[#6e7681] font-normal">（{rolling.window} 日窗口，参考线 y=1）</span>
        </h3>
        <RollingLine data={rolling.rolling_sharpe} color="#3fb950" height={200}
          refLine={1} label="滚动夏普" valueFormatter={(v) => v.toFixed(2)} />
      </div>

      {/* 4. 滚动波动 + 滚动 Beta */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">滚动波动率（年化 %）</h3>
          <RollingArea data={rolling.rolling_volatility} color="#e3b341" height={190}
            label="滚动波动" valueFormatter={pct} zeroFloor />
        </div>
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">滚动 Beta（vs 买入持有）</h3>
          <RollingLine data={rolling.rolling_beta} color="#bc8cff" height={190}
            refLine={1} label="滚动 Beta" valueFormatter={(v) => v.toFixed(2)} />
        </div>
      </div>

      {/* 5. 仓位暴露 + 累计换手 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">仓位暴露（0~100%）</h3>
          <RollingArea data={rolling.exposure_series} color="#58a6ff" height={190}
            label="暴露" valueFormatter={(v) => `${(v * 100).toFixed(0)}%`} zeroFloor />
        </div>
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">累计换手</h3>
          <RollingLine data={rolling.turnover_series} color="#f0883e" height={190}
            label="累计换手" valueFormatter={(v) => v.toFixed(2)} />
        </div>
      </div>

      {/* 6. 回撤区间表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          回撤区间 <span className="text-xs text-[#6e7681] font-normal">（按深度排序，Top {drawdownPeriods.length}）</span>
        </h3>
        {drawdownPeriods.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#21262d]">
                  <th className="text-center py-2 pr-3 w-8">#</th>
                  <th className="text-left py-2 pr-3">峰值日</th>
                  <th className="text-left py-2 pr-3">谷底日</th>
                  <th className="text-left py-2 pr-3">恢复日</th>
                  <th className="text-right py-2 pr-3">深度</th>
                  <th className="text-right py-2 pr-3">回撤天数</th>
                  <th className="text-right py-2 pr-3">恢复天数</th>
                  <th className="text-right py-2">水下天数</th>
                </tr>
              </thead>
              <tbody>
                {drawdownPeriods.map((d) => (
                  <tr key={d.rank} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                    <td className="py-1.5 pr-3 text-center text-[#6e7681]">{d.rank}</td>
                    <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{d.peak_date.slice(0, 10)}</td>
                    <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{d.valley_date.slice(0, 10)}</td>
                    <td className="py-1.5 pr-3 font-mono">
                      {d.recovery_date ? (
                        <span className="text-[#8b949e]">{d.recovery_date.slice(0, 10)}</span>
                      ) : (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#e3b341]/10 border border-[#e3b341]/30 text-[#e3b341]">进行中</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#f85149]">{d.depth_pct.toFixed(2)}%</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{d.drawdown_days}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">
                      {d.recovery_days ?? "—"}
                    </td>
                    <td className="py-1.5 text-right font-mono text-[#8b949e]">{d.max_underwater_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[#6e7681] text-sm text-center py-4">无显著回撤区间</p>
        )}
      </div>
    </div>
  )
}
