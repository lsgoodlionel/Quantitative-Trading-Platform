import { useState, useEffect, useMemo } from "react"
import { Link } from "react-router-dom"
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useRiskConfig, useRiskSummary, useUpdateRiskConfig, useVaRAnalysis } from "@/hooks/useRisk"
import { usePositions, useAccount } from "@/hooks/usePositions"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import type { RiskConfig, RiskRule, Market } from "@/types"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightVerdict, InsightItem } from "@/components/ui/InsightBox"

const RULE_LABELS: Record<string, string> = {
  max_position_pct:    "最大持仓比例 (%)",
  max_order_value:     "单笔最大订单价值 ($)",
  max_daily_orders:    "每日最大订单数量",
  allowed_markets:     "允许市场",
  allowed_symbols:     "允许标的",
  daily_loss_limit:    "每日亏损限额 ($)",
  max_drawdown:        "最大回撤限制 (%)",
  max_leverage:        "最大杠杆倍数",
  position_concentration: "仓位集中度限制 (%)",
}

const SEVERITY_STYLES: Record<string, string> = {
  block:   "text-[#f85149] bg-[#2a1b1b] border-[#f85149]/30",
  warning: "text-[#e3b341] bg-[#272111] border-[#e3b341]/30",
  halt:    "text-[#ff9f43] bg-[#2a1b1b] border-[#ff9f43]/30",
}

interface RuleRowProps {
  rule: RiskRule
  onChange: (updated: RiskRule) => void
}

function RuleRow({ rule, onChange }: RuleRowProps) {
  const isArray = Array.isArray(rule.value)

  return (
    <div className="flex items-center gap-4 py-3 border-b border-[#21262d]/60 last:border-0">
      {/* Toggle */}
      <button
        onClick={() => onChange({ ...rule, enabled: !rule.enabled })}
        className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
          rule.enabled ? "bg-[#3fb950]" : "bg-[#30363d]"
        }`}
        aria-label={rule.enabled ? "禁用" : "启用"}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
            rule.enabled ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>

      {/* Label */}
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${rule.enabled ? "text-[#e6edf3]" : "text-[#6e7681]"}`}>
          {RULE_LABELS[rule.rule_type] ?? rule.rule_type}
        </p>
      </div>

      {/* Value input */}
      <div className="w-44">
        {isArray ? (
          <input
            className="input w-full text-xs font-mono"
            value={(rule.value as string[]).join(", ")}
            onChange={(e) =>
              onChange({ ...rule, value: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })
            }
            disabled={!rule.enabled}
            placeholder="US, HK"
          />
        ) : (
          <input
            className="input w-full text-xs font-mono"
            type="number"
            value={rule.value as number}
            onChange={(e) => onChange({ ...rule, value: parseFloat(e.target.value) || 0 })}
            disabled={!rule.enabled}
          />
        )}
      </div>

      {/* Severity badge */}
      <span className={`text-xs px-2 py-0.5 rounded border ${SEVERITY_STYLES[rule.severity] ?? ""}`}>
        {rule.severity}
      </span>
    </div>
  )
}

// ── VaR Panel ─────────────────────────────────────────────────────

function VaRPanel({ market }: { market: Market }) {
  const { data: positions = [] } = usePositions(market)
  const { data: account } = useAccount(market)
  const { mutate: computeVar, isPending, data: varResult, error } = useVaRAnalysis()
  const { toast } = useToast()

  const openPositions = useMemo(() => positions.filter((p) => p.qty !== 0), [positions])
  const totalValue = account?.portfolio_value ?? 0

  function handleCompute() {
    if (openPositions.length === 0) { toast("当前无持仓", "warning"); return }
    if (totalValue <= 0) { toast("组合净值为零", "warning"); return }

    const positionWeights = openPositions.map((p) => ({
      symbol: p.symbol,
      market,
      weight: (p.market_value ?? p.avg_cost * Math.abs(p.qty)) / totalValue,
    }))

    computeVar({ positions: positionWeights, portfolio_value: totalValue, lookback_days: 252 })
  }

  // Return series chart data
  const chartData = useMemo(() => {
    if (!varResult?.return_series) return []
    return varResult.return_series.slice(-120).map((r, i) => ({
      i,
      r: parseFloat((r * 100).toFixed(3)),
    }))
  }, [varResult])

  const varColor = (v: number) => v > 5 ? "text-[#f85149]" : v > 3 ? "text-[#e3b341]" : "text-[#3fb950]"

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#e6edf3]">VaR / CVaR 分析</h3>
        <button className="btn btn-secondary text-xs" onClick={handleCompute} disabled={isPending}>
          {isPending ? <Spinner size="sm" /> : "计算"}
        </button>
      </div>

      {error && <p className="text-xs text-[#f85149]">{error.message}</p>}

      {!varResult && !isPending && (
        <p className="text-xs text-[#6e7681] py-2">点击「计算」运行 {market} 市场持仓风险分析</p>
      )}

      {varResult && (
        <>
          {/* VaR grid */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="bg-[#1c2128] border border-[#21262d] rounded-lg p-2.5">
              <p className="text-[#6e7681] mb-1">95% VaR (历史)</p>
              <p className={`font-mono text-base font-semibold ${varColor(varResult.hist_var_95_pct)}`}>
                {varResult.hist_var_95_pct.toFixed(2)}%
              </p>
              <p className="text-[#6e7681] mt-0.5">${varResult.hist_var_95_value.toFixed(0)}</p>
            </div>
            <div className="bg-[#1c2128] border border-[#21262d] rounded-lg p-2.5">
              <p className="text-[#6e7681] mb-1">99% VaR (历史)</p>
              <p className={`font-mono text-base font-semibold ${varColor(varResult.hist_var_99_pct)}`}>
                {varResult.hist_var_99_pct.toFixed(2)}%
              </p>
              <p className="text-[#6e7681] mt-0.5">${varResult.hist_var_99_value.toFixed(0)}</p>
            </div>
            <div className="bg-[#1c2128] border border-[#21262d] rounded-lg p-2.5">
              <p className="text-[#6e7681] mb-1">95% CVaR (ES)</p>
              <p className={`font-mono text-base font-semibold ${varColor(varResult.hist_cvar_95_pct)}`}>
                {varResult.hist_cvar_95_pct.toFixed(2)}%
              </p>
              <p className="text-[#6e7681] mt-0.5">${varResult.hist_cvar_95_value.toFixed(0)}</p>
            </div>
            <div className="bg-[#1c2128] border border-[#21262d] rounded-lg p-2.5">
              <p className="text-[#6e7681] mb-1">99% CVaR (ES)</p>
              <p className={`font-mono text-base font-semibold ${varColor(varResult.hist_cvar_99_pct)}`}>
                {varResult.hist_cvar_99_pct.toFixed(2)}%
              </p>
              <p className="text-[#6e7681] mt-0.5">${varResult.hist_cvar_99_value.toFixed(0)}</p>
            </div>
          </div>

          {/* Stats row */}
          <div className="flex gap-4 text-xs text-[#8b949e] flex-wrap border-t border-[#21262d] pt-2">
            <span>均值 <span className="font-mono text-[#e6edf3]">{varResult.mean_return_pct.toFixed(3)}%</span></span>
            <span>波动率 <span className="font-mono text-[#e6edf3]">{varResult.std_return_pct.toFixed(3)}%</span></span>
            <span>偏度 <span className="font-mono text-[#e6edf3]">{varResult.skewness.toFixed(3)}</span></span>
            <span>峰度 <span className="font-mono text-[#e6edf3]">{varResult.kurtosis.toFixed(3)}</span></span>
            <span>样本 <span className="font-mono text-[#e6edf3]">{varResult.n_days}日</span></span>
          </div>

          {/* VaR 结论 */}
          {(() => {
            const v95 = varResult.hist_var_95_pct
            const v99 = varResult.hist_var_99_pct
            const cv95 = varResult.hist_cvar_95_pct
            const kurt = varResult.kurtosis
            const skew = varResult.skewness

            const verdict: InsightVerdict = v95 > 5 ? "bad" : v95 > 3 ? "warn" : "good"
            const riskLevel = v95 > 5 ? "高风险" : v95 > 3 ? "中等风险" : "低风险"
            const summary = `当前持仓在 95% 置信度下单日最大损失为 ${v95.toFixed(2)}%（$${varResult.hist_var_95_value.toFixed(0)}），风险等级：${riskLevel}。极端情景下（CVaR 95%）预期损失达 ${cv95.toFixed(2)}%。`

            const findings: InsightItem[] = [
              {
                text: `95% VaR ${v95.toFixed(2)}% — 在正常市场条件下，100个交易日中预期不超过5次触发该损失`,
                type: v95 > 5 ? "bad" : v95 > 3 ? "warn" : "good",
              },
              {
                text: `99% VaR ${v99.toFixed(2)}% vs 95% CVaR ${cv95.toFixed(2)}% — ${cv95 > v99 * 1.5 ? "两者差距较大，说明尾部分布肥尾显著" : "尾部风险集中度正常"}`,
                type: cv95 > v99 * 1.5 ? "warn" : "good",
              },
              {
                text: `偏度 ${skew.toFixed(3)} — ${skew < -0.5 ? "明显左偏（负偏），组合有大亏概率高于正态假设" : skew > 0.5 ? "右偏，大盈概率相对更高" : "近似对称分布"}`,
                type: skew < -0.5 ? "bad" : "neutral",
              },
              {
                text: `峰度 ${kurt.toFixed(3)} — ${kurt > 4 ? "肥尾分布（峰度 > 4），极端损失频率远高于正态分布" : kurt > 3 ? "略有肥尾" : "接近正态分布"}`,
                type: kurt > 4 ? "bad" : kurt > 3 ? "warn" : "good",
              },
            ]

            const recommendations: InsightItem[] = [
              ...(v95 > 5 ? [{
                text: "立即审查高风险持仓",
                sub: "VaR 超过 5%，建议减少波动率最高的持仓，或通过期权对冲尾部风险",
                type: "bad" as const,
              }] : []),
              ...(kurt > 4 ? [{
                text: "考虑 CVaR 约束而非 VaR",
                sub: "肥尾分布下 VaR 低估了真实风险，组合优化时应使用 CVaR 最小化目标",
                type: "warn" as const,
              }] : []),
              {
                text: "定期更新风险计量",
                sub: "市场波动率随时间变化，建议每周重新计算 VaR，高波动期缩短为每日",
                type: "neutral" as const,
              },
              {
                text: "将 VaR 阈值与风控规则对齐",
                sub: "确保「每日亏损限额」风控规则数值 ≥ 组合净值 × VaR%，避免风控被动触发",
                type: "neutral" as const,
              },
            ]

            return (
              <InsightBox
                verdict={verdict}
                summary={summary}
                findings={findings}
                recommendations={recommendations}
              />
            )
          })()}

          {/* Return distribution chart */}
          <div>
            <p className="text-xs text-[#8b949e] mb-2">组合日收益率（近120日）</p>
            <ResponsiveContainer width="100%" height={100}>
              <AreaChart data={chartData} margin={{ top: 2, right: 4, left: 0, bottom: 2 }}>
                <defs>
                  <linearGradient id="ret-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="i" hide />
                <YAxis tick={{ fill: "#8b949e", fontSize: 9 }} width={32} tickFormatter={(v) => `${v}%`} />
                <ReferenceLine y={0} stroke="#6e7681" />
                <ReferenceLine y={-varResult.hist_var_95_pct} stroke="#f85149" strokeDasharray="3 3" opacity={0.7} />
                <Tooltip
                  formatter={(v: number) => [`${v.toFixed(3)}%`, "日收益率"]}
                  contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 10 }}
                  itemStyle={{ color: "#e6edf3" }}
                />
                <Area type="monotone" dataKey="r" stroke="#58a6ff" strokeWidth={1} fill="url(#ret-grad)" dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
            <p className="text-[10px] text-[#6e7681] mt-1">红虚线 = 95% VaR 阈值</p>
          </div>
        </>
      )}
    </div>
  )
}

export function Risk() {
  const { data: config, isLoading } = useRiskConfig()
  const { data: summary } = useRiskSummary()
  const { mutate: updateConfig, isPending: saving } = useUpdateRiskConfig()
  const { toast } = useToast()

  const [market, setMarket] = useState<Market>("US")
  const [localConfig, setLocalConfig] = useState<RiskConfig | null>(null)

  useEffect(() => {
    if (config && !localConfig) setLocalConfig(config)
  }, [config, localConfig])

  function handleRuleChange(idx: number, updated: RiskRule) {
    if (!localConfig) return
    const rules = localConfig.rules.map((r, i) => (i === idx ? updated : r))
    setLocalConfig({ ...localConfig, rules })
  }

  function handleSave() {
    if (!localConfig) return
    updateConfig(localConfig, {
      onSuccess: () => toast("风控配置已更新", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleReset() {
    if (config) setLocalConfig(config)
  }

  return (
    <AppShell title="风控配置" help={PAGE_HELP["risk"]}>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Rules */}
        <div className="lg:col-span-2">
          {/* Market selector for VaR analysis */}
          <div className="card">
            <div className="card-header flex items-center justify-between">
              <h2 className="text-sm font-semibold text-[#e6edf3]">风控规则</h2>
              <div className="flex gap-2">
                <button className="btn btn-ghost text-xs" onClick={handleReset} disabled={saving}>
                  重置
                </button>
                <button className="btn btn-primary text-xs" onClick={handleSave} disabled={saving || !localConfig}>
                  {saving ? <Spinner size="sm" /> : "保存"}
                </button>
              </div>
            </div>

            {isLoading && <div className="flex justify-center py-12"><Spinner size="lg" /></div>}

            {localConfig && (
              <div>
                {localConfig.rules.map((rule, idx) => (
                  <RuleRow
                    key={rule.rule_type}
                    rule={rule}
                    onChange={(updated) => handleRuleChange(idx, updated)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right sidebar: Summary + VaR */}
        <div className="space-y-4">
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">今日汇总</h3>
            {summary ? (
              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">提交订单数</span>
                  <span className="font-mono text-[#e6edf3]">{summary.orders_today}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">今日实现盈亏</span>
                  <span className={`font-mono ${summary.realized_pnl_today >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {summary.realized_pnl_today >= 0 ? "+" : ""}${summary.realized_pnl_today.toFixed(2)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">权益峰值</span>
                  <span className="font-mono text-[#e6edf3]">${summary.peak_portfolio_value.toLocaleString()}</span>
                </div>
              </div>
            ) : (
              <p className="text-[#6e7681] text-sm">加载中…</p>
            )}
          </div>

          {/* Violations */}
          {summary && (summary.violations?.length ?? 0) > 0 && (
            <div className="card border-[#f85149]/20">
              <h3 className="text-sm font-semibold text-[#f85149] mb-3">
                ⚠ 近期告警 ({summary.violations!.length})
              </h3>
              <div className="space-y-2">
                {summary.violations!.slice(0, 5).map((v, i) => (
                  <div key={i} className="text-xs bg-[#2a1b1b] border border-[#f85149]/20 rounded px-3 py-2">
                    <p className="text-[#f85149] font-medium">{RULE_LABELS[v.rule_type] ?? v.rule_type}</p>
                    <p className="text-[#8b949e] mt-0.5">{v.message}</p>
                    <p className="text-[#6e7681] mt-0.5">{v.timestamp.slice(0, 16)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {summary && (summary.violations?.length ?? 0) === 0 && (
            <div className="card border-[#3fb950]/20 bg-[#162a1e]/20">
              <p className="text-[#3fb950] text-sm text-center py-2">✓ 无风控告警</p>
            </div>
          )}

          {/* 违规后操作引导 */}
          {summary && (summary.violations?.length ?? 0) > 0 && (
            <div className="rounded-lg border border-[#f85149]/20 bg-[#1a0f0f] p-3 space-y-2">
              <p className="text-xs font-semibold text-[#f85149]">⚡ 检测到风控违规，建议操作：</p>
              <div className="flex flex-wrap gap-2">
                <Link to="/portfolio" className="px-3 py-1.5 rounded text-xs border border-[#f85149]/30 text-[#f85149] hover:bg-[#f85149]/10 transition-colors">
                  💼 查看超仓持仓
                </Link>
                <Link to="/orders" className="px-3 py-1.5 rounded text-xs border border-[#e3b341]/30 text-[#e3b341] hover:bg-[#e3b341]/10 transition-colors">
                  📋 前往下单减仓
                </Link>
                <Link to="/live-strategy" className="px-3 py-1.5 rounded text-xs border border-[#6e7681]/30 text-[#8b949e] hover:bg-[#21262d] transition-colors">
                  ⏸ 暂停运行策略
                </Link>
              </div>
            </div>
          )}

          {/* VaR Analysis */}
          <div>
            <div className="flex gap-1 mb-2">
              {(["US", "HK", "A"] as Market[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMarket(m)}
                  className={`flex-1 py-1 rounded text-xs font-medium transition-colors ${
                    market === m
                      ? "bg-[#1f6feb]/30 text-[#58a6ff]"
                      : "text-[#6e7681] hover:text-[#e6edf3]"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
            <VaRPanel market={market} />
          </div>

          {/* 风控 → 组合优化 引导 */}
          <div className="rounded-lg border border-[#30363d] bg-[#161b22] p-3 space-y-2">
            <p className="text-xs font-semibold text-[#8b949e]">💡 风控结果使用路径</p>
            <div className="space-y-1.5 text-[10px] text-[#6e7681]">
              <p>▸ VaR 过高（风险偏大）→ <Link to="/portfolio-optimizer" className="text-[#58a6ff] hover:underline">组合优化</Link> 重新分配权重，降低集中度</p>
              <p>▸ 波动率持续偏高 → <Link to="/algolab" className="text-[#58a6ff] hover:underline">算法实验室 · GARCH</Link> 建立动态止损模型</p>
              <p>▸ 回撤超限 → <Link to="/orders" className="text-[#58a6ff] hover:underline">订单中心</Link> 手动减仓，或 <Link to="/live-strategy" className="text-[#58a6ff] hover:underline">停止运行策略</Link></p>
              <p>▸ 调整完毕 → 回到风控页重新运行 VaR 验证</p>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
