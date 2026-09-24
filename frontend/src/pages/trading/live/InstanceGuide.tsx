// 实例卡「后续操作」页：决策卡 + 四步操作路径（调参 → 对基准 → 风控 → 是否实盘）。
import type { LiveStrategyInstance, PaperSimResult, Market, Frequency } from "@/types"
import type { computeAdvice } from "./advice"
import type { RerunValues } from "./shared"

type Advice = ReturnType<typeof computeAdvice>

interface InstanceGuideProps {
  inst: LiveStrategyInstance
  paper: PaperSimResult | null
  advice: Advice | null
  simDaysActual: number
  onRerun: (values: RerunValues) => void
}

export function InstanceGuide({ inst, paper, advice, simDaysActual, onRerun }: InstanceGuideProps) {
  return (
          <div className="space-y-4">
            {/* 决策卡 */}
            {advice && (
              <div className={`rounded-lg p-4 border`}
                style={{ borderColor: `${advice.color}40`, background: `${advice.color}10` }}>
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-xl">
                    {advice.action === "proceed" ? "✅" : advice.action === "adjust" ? "❌" : "⏳"}
                  </span>
                  <p className="font-bold text-sm" style={{ color: advice.color }}>{advice.label}</p>
                </div>

                {advice.goods.length > 0 && (
                  <div className="mb-2">
                    <p className="text-[10px] text-[#6e7681] mb-1">✓ 积极指标</p>
                    {advice.goods.map((g, i) => (
                      <p key={i} className="text-xs text-[#3fb950]">▸ {g}</p>
                    ))}
                  </div>
                )}
                {advice.issues.length > 0 && (
                  <div>
                    <p className="text-[10px] text-[#6e7681] mb-1">✗ 待解决问题</p>
                    {advice.issues.map((s, i) => (
                      <p key={i} className="text-xs text-[#f85149]">▸ {s}</p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 操作步骤 */}
            <div className="space-y-2">
              <p className="text-xs font-semibold text-[#8b949e]">下一步操作路径</p>

              {/* Step 1: 参数调整 or 延长 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: `${advice?.color ?? "#58a6ff"}20`, color: advice?.color ?? "#58a6ff" }}>
                  1
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">
                    {advice?.action === "proceed" ? "✅ 延长模拟天数确认稳定性" : "⚙️ 调整参数或延长模拟天数"}
                  </p>
                  {advice?.action === "proceed" ? (
                    <p className="text-[10px] text-[#6e7681] leading-relaxed mb-2">
                      当前 {simDaysActual} 天结果良好，延长至更长周期验证稳定性
                    </p>
                  ) : (
                    <>
                      <p className="text-[10px] text-[#6e7681] leading-relaxed mb-2">
                        {advice?.paramHints[0] ?? "点击调整重跑，修改参数后重新模拟，直到指标达标"}
                      </p>
                      {advice && advice.paramHints.length > 1 && (
                        <ul className="space-y-0.5 mb-2">
                          {advice.paramHints.slice(1).map((h, i) => (
                            <li key={i} className="text-[9px] text-[#6e7681]">▸ {h}</li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                  {/* 快捷操作按钮 */}
                  <div className="flex flex-wrap gap-2">
                    {/* 延长天数快捷按钮 */}
                    {simDaysActual < 90 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 90 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 90 天
                      </button>
                    )}
                    {simDaysActual < 120 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 120 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 120 天
                      </button>
                    )}
                    {simDaysActual < 180 && (
                      <button
                        onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: 180 })}
                        className="px-2 py-1 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                        延长至 180 天
                      </button>
                    )}
                    <button
                      onClick={() => onRerun({ strategy_name: inst.strategy_name, symbol: inst.symbol, market: inst.market as Market, frequency: inst.frequency as Frequency, params: inst.params, sim_days: simDaysActual })}
                      className="px-2 py-1 rounded text-[10px] border border-[#e3b341]/40 text-[#e3b341] bg-[#272111]/40 hover:bg-[#e3b341]/10 transition-colors">
                      ⚙ 调整参数
                    </button>
                  </div>
                </div>
              </div>

              {/* Step 2: 基准对比 */}
              {paper && (
                <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                    style={{ background: "#58a6ff20", color: "#58a6ff" }}>2</div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">📊 对比买入持有基准</p>
                    <div className="flex items-center gap-3 text-xs mt-1">
                      <span className="text-[#6e7681]">策略收益</span>
                      <span className={`font-mono font-bold ${paper.total_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {paper.total_return_pct >= 0 ? "+" : ""}{paper.total_return_pct.toFixed(2)}%
                      </span>
                      <span className="text-[#6e7681]">vs 买入持有</span>
                      <span className={`font-mono font-bold ${paper.buy_hold_return_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {paper.buy_hold_return_pct >= 0 ? "+" : ""}{paper.buy_hold_return_pct.toFixed(2)}%
                      </span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${paper.total_return_pct > paper.buy_hold_return_pct ? "bg-[#3fb950]/15 text-[#3fb950]" : "bg-[#f85149]/15 text-[#f85149]"}`}>
                        {paper.total_return_pct > paper.buy_hold_return_pct ? "✓ 跑赢基准" : "✗ 未跑赢"}
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {/* Step 3: 风控 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: "#e3b34120", color: "#e3b341" }}>3</div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">⚙️ 配置风控规则</p>
                  <p className="text-[10px] text-[#6e7681]">设置最大仓位比例、日亏损上限，保护本金安全</p>
                  <a href="/settings"
                    className="inline-block mt-1.5 text-[10px] px-2 py-0.5 rounded border border-[#30363d] text-[#58a6ff] hover:bg-[#21262d] transition-colors">
                    去设置 →
                  </a>
                </div>
              </div>

              {/* Step 4: 是否进入实盘 */}
              <div className="flex gap-3 p-3 bg-[#161b22] rounded-lg">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ background: advice?.action === "proceed" ? "#3fb95020" : "#6e768120", color: advice?.action === "proceed" ? "#3fb950" : "#6e7681" }}>4</div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[#e6edf3] mb-0.5">
                    {advice?.action === "proceed" ? "🚀 可考虑开启实盘（谨慎）" : "🚫 暂缓实盘"}
                  </p>
                  <p className="text-[10px] text-[#6e7681]">
                    {advice?.action === "proceed"
                      ? "Sharpe > 1、最大回撤 < 20% 且跑赢基准，满足基础门槛。建议先小仓位（<5%）试水。"
                      : "指标尚未达标，继续在模拟盘中优化，或尝试不同策略组合后再评估。"}
                  </p>
                </div>
              </div>
            </div>

            {/* 实盘说明 */}
            <div className="bg-[#2a1a00] border border-[#e3b341]/30 rounded-lg p-3 text-[10px] text-[#e3b341] space-y-1">
              <p className="font-semibold">⚠ 实盘风险提示</p>
              <p>模拟结果基于历史数据，不保证未来表现。实盘交易需承担真实市场风险，请确保资金可承受全部亏损。
                建议实盘资金不超过模拟期间最大回撤对应的可承受额度。</p>
            </div>
          </div>
  )
}
