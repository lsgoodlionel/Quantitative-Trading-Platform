// 组合优化结果区：关键指标 → BL 先验/后验 → 图表 → 资产明细 → 结论 → 配股/再平衡。
import { Link } from "react-router-dom"
import { InsightBox } from "@/components/ui/InsightBox"
import type { InsightItem, InsightVerdict } from "@/components/ui/InsightBox"
import { RebalancePanel } from "@/pages/portfolio/RebalancePanel"
import type { Market } from "@/types"
import { AllocationPanel } from "./AllocationPanel"
import { EfficientFrontierChart, WeightPieChart } from "./charts"
import {
  HRP_LINKAGE_OPTIONS, METHOD_OPTIONS, RETURNS_OPTIONS, RISK_MODEL_OPTIONS,
  isHrp, type PortfolioOptMethod, type PortfolioOptResult,
} from "./options"

// ── 组合优化结论生成 ─────────────────────────────────────────────

function buildPortfolioInsight(result: PortfolioOptResult) {
  const { expected_return, expected_volatility, sharpe_ratio, cvar_95, weights } = result
  const maxW = Math.max(...Object.values(weights)) * 100
  const activeN = Object.values(weights).filter((w) => w > 0.01).length
  const methodLabel = METHOD_OPTIONS.find((m) => m.value === result.method)?.label ?? result.method

  const verdict: InsightVerdict =
    sharpe_ratio >= 1.5 && expected_return > 0 ? "good"
    : sharpe_ratio >= 0.8 && expected_return > 0 ? "warn"
    : "bad"

  const grade =
    sharpe_ratio >= 1.5 ? "优秀（Sharpe ≥ 1.5）"
    : sharpe_ratio >= 1.0 ? "良好（Sharpe ≥ 1.0）"
    : sharpe_ratio >= 0.5 ? "一般（Sharpe < 1.0）"
    : "较弱（Sharpe < 0.5）"

  const summary = `采用「${methodLabel}」优化后，组合年化收益预期 ${expected_return >= 0 ? "+" : ""}${expected_return.toFixed(2)}%，年化波动率 ${expected_volatility.toFixed(2)}%，夏普比率 ${sharpe_ratio.toFixed(2)}，综合评级：${grade}。`

  const findings: InsightItem[] = [
    {
      text: `夏普比率 ${sharpe_ratio.toFixed(3)} — ${sharpe_ratio >= 1.5 ? "风险调整收益优秀，远超无风险资产" : sharpe_ratio >= 1.0 ? "风险调整收益良好，具备实盘部署参考价值" : "风险调整收益偏低，建议优化资产池或调整方法"}`,
      type: sharpe_ratio >= 1.5 ? "good" : sharpe_ratio >= 1.0 ? "good" : sharpe_ratio >= 0.5 ? "warn" : "bad",
    },
    {
      text: `95% CVaR ${cvar_95.toFixed(2)}% — 极端情景下单日最大预期损失`,
      sub: cvar_95 > 10 ? "尾部风险偏高，建议降低单资产权重上限或加入低相关性资产" : "尾部风险可控",
      type: cvar_95 > 10 ? "bad" : cvar_95 > 5 ? "warn" : "good",
    },
    {
      text: `最大单资产权重 ${maxW.toFixed(1)}% — ${maxW > 40 ? "集中度过高，面临个股黑天鹅风险" : maxW > 25 ? "集中度适中" : "分散度良好"}`,
      type: maxW > 40 ? "bad" : maxW > 25 ? "warn" : "good",
    },
    {
      text: `有效持仓 ${activeN} 只 — ${activeN < 3 ? "过度集中，建议增加资产数量" : activeN <= 8 ? "资产数量合理" : "资产过多，可能稀释阿尔法"}`,
      type: activeN < 3 ? "bad" : activeN <= 8 ? "good" : "warn",
    },
  ]

  const recommendations: InsightItem[] = [
    ...(sharpe_ratio < 1.0 ? [{
      text: "尝试切换优化方法",
      sub: "当前结果夏普偏低，可试验「最大夏普」或「风险平价」方法，或更换资产池",
      type: "warn" as const,
    }] : []),
    ...(maxW > 35 ? [{
      text: "设置权重上限约束",
      sub: `最大权重 ${maxW.toFixed(1)}% 过高，建议在后端 API 参数中加入 max_weight=0.30 约束`,
      type: "warn" as const,
    }] : []),
    {
      text: "周期性再平衡",
      sub: "建议每季度重新运行优化，市场结构变化会使优化权重失效",
      type: "neutral" as const,
    },
    {
      text: "结合回测验证",
      sub: "优化结果基于历史协方差，建议在「回测」页面对该权重组合做历史验证，防止过拟合",
      type: "neutral" as const,
    },
    ...(expected_return <= 0 ? [{
      text: "收益预期为负，重新筛选资产",
      sub: "检查各资产的历史收益数据区间，或更换具有正收益预期的标的组合",
      type: "bad" as const,
    }] : []),
  ]

  return { verdict, summary, findings, recommendations }
}
// ── Black-Litterman：先验 vs 后验回显 ──────────────────────────

function BLPosteriorView({ result }: { result: PortfolioOptResult }) {
  const prior = result.bl_prior_returns ?? {}
  const posterior = result.bl_posterior_returns ?? {}
  const symbols = Object.keys(posterior)
  if (symbols.length === 0) return null

  return (
    <div className="card border-[#bc8cff]/25">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[#e6edf3]">Black-Litterman 观点融合</h3>
        {result.bl_risk_aversion != null && (
          <span className="text-[11px] text-[#8b949e]">
            风险厌恶 δ = <span className="font-mono text-[#bc8cff]">{result.bl_risk_aversion.toFixed(2)}</span>
          </span>
        )}
      </div>
      {(result.bl_views?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {result.bl_views!.map((label, i) => (
            <span key={i} className="text-[10px] bg-[#161b22] border border-[#bc8cff]/30 rounded px-2 py-0.5 text-[#bc8cff]">
              {label}
            </span>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">标的</th>
              <th className="text-right py-2 pr-3">市场隐含先验</th>
              <th className="text-right py-2 pr-3">融合后验</th>
              <th className="text-right py-2">观点调整</th>
            </tr>
          </thead>
          <tbody>
            {symbols
              .sort((a, b) => (posterior[b] ?? 0) - (posterior[a] ?? 0))
              .map((sym) => {
                const pri = prior[sym] ?? 0
                const post = posterior[sym] ?? 0
                const delta = post - pri
                return (
                  <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                    <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">{pri.toFixed(2)}%</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{post.toFixed(2)}%</td>
                    <td className={`py-2 text-right font-mono text-xs ${
                      delta > 0.05 ? "text-[#3fb950]" : delta < -0.05 ? "text-[#f85149]" : "text-[#6e7681]"
                    }`}>
                      {delta >= 0 ? "+" : ""}{delta.toFixed(2)}%
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-[#6e7681] mt-2">
        先验来自市场组合的反向优化（δ·Σ·w_mkt）；后验按 Idzorek 置信度将观点与先验贝叶斯融合，再驱动最大夏普配权。
      </p>
    </div>
  )
}

export function OptimizerResult({ result, market }: { result: PortfolioOptResult; market: Market }) {
  const methodLabel = METHOD_OPTIONS.find((m) => m.value === result.method)?.label ?? result.method
  const insight = buildPortfolioInsight(result)

  return (
    <div className="space-y-5">
      {/* 关键指标 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "优化方法", value: methodLabel, color: "text-[#58a6ff]" },
          { label: "年化收益率", value: `${result.expected_return >= 0 ? "+" : ""}${result.expected_return.toFixed(2)}%`, color: result.expected_return >= 0 ? "text-[#3fb950]" : "text-[#f85149]" },
          { label: "年化波动率", value: `${result.expected_volatility.toFixed(2)}%`, color: "text-[#e3b341]" },
          { label: "夏普比率", value: result.sharpe_ratio.toFixed(3), color: result.sharpe_ratio >= 1 ? "text-[#3fb950]" : "text-[#e6edf3]" },
          { label: "95% CVaR", value: `${result.cvar_95.toFixed(2)}%`, color: "text-[#f85149]" },
          { label: "资产数量", value: Object.keys(result.weights).length, color: "text-[#e6edf3]" },
          { label: "最大权重", value: `${(Math.max(...Object.values(result.weights)) * 100).toFixed(1)}%`, color: "text-[#e6edf3]" },
          { label: "最小权重", value: `${(Math.min(...Object.values(result.weights).filter((w) => w > 0.001)) * 100).toFixed(1)}%`, color: "text-[#e6edf3]" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-3">
            <p className="text-xs text-[#6e7681] mb-1">{label}</p>
            <p className={`font-mono font-semibold text-sm ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* 估计器回显 */}
      {(result.risk_model || result.expected_returns_method) && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {result.risk_model && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              风险模型：
              <span className="text-[#58a6ff] ml-1">
                {RISK_MODEL_OPTIONS.find((o) => o.value === result.risk_model)?.label ?? result.risk_model}
              </span>
            </span>
          )}
          {result.expected_returns_method && !isHrp(result.method as PortfolioOptMethod) && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              预期收益：
              <span className="text-[#58a6ff] ml-1">
                {RETURNS_OPTIONS.find((o) => o.value === result.expected_returns_method)?.label ?? result.expected_returns_method}
              </span>
            </span>
          )}
          {result.linkage_method && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              聚类连接：
              <span className="text-[#58a6ff] ml-1">
                {HRP_LINKAGE_OPTIONS.find((o) => o.value === result.linkage_method)?.label ?? result.linkage_method}
              </span>
            </span>
          )}
          {result.cvar_beta != null && (
            <span className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[#8b949e]">
              尾部置信水平：
              <span className="text-[#58a6ff] ml-1">{(result.cvar_beta * 100).toFixed(0)}%</span>
            </span>
          )}
        </div>
      )}

      {/* Black-Litterman 先验/后验融合 */}
      <BLPosteriorView result={result} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 权重分布饼图 */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">权重分布</h3>
          <WeightPieChart weights={result.weights} />
        </div>

        {/* 有效前沿 */}
        {result.frontier.length > 0 && (
          <div className="card lg:col-span-2">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
              有效前沿
              <span className="ml-2 text-xs text-[#6e7681] font-normal">红点 = 当前优化组合</span>
            </h3>
            <EfficientFrontierChart frontier={result.frontier} result={result} />
          </div>
        )}
      </div>

      {/* 权重 + 风险贡献表格 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">资产明细</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
                <th className="text-left py-2 pr-3">标的</th>
                <th className="text-right py-2 pr-3">权重</th>
                <th className="text-right py-2 pr-3">权重条</th>
                <th className="text-right py-2">风险贡献</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(result.weights)
                .sort(([, a], [, b]) => b - a)
                .map(([sym, w]) => {
                  const rc = result.risk_contributions[sym] ?? 0
                  const wPct = w * 100
                  return (
                    <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                      <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                        {wPct.toFixed(1)}%
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex justify-end items-center gap-1">
                          <div className="w-32 h-2 rounded bg-[#21262d] overflow-hidden">
                            <div
                              className="h-full rounded bg-[#58a6ff]"
                              style={{ width: `${Math.min(wPct, 100)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="py-2 text-right font-mono text-xs text-[#8b949e]">
                        {rc.toFixed(1)}%
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 结论与建议 */}
      <InsightBox
        verdict={insight.verdict}
        summary={insight.summary}
        findings={insight.findings}
        recommendations={insight.recommendations}
      />

      {/* 离散配置：连续权重 → 整数股数 */}
      <AllocationPanel result={result} market={market} />

      {/* 再平衡执行：预览 → 确认 → 走 OMS 下单（V3 A-b） */}
      <RebalancePanel weights={result.weights} market={market} />

      {/* 下一步操作 CTA */}
      <div className="card border-[#30363d] space-y-3">
        <p className="text-xs font-semibold text-[#8b949e]">📍 优化完成，建议下一步</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <Link to="/risk"
            className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[#f85149]/25 text-[#f85149] bg-[#1a0f0f] hover:bg-[#f85149]/10 transition-colors">
            <span className="text-base">🛡️</span>
            <div>
              <p className="font-medium">验证风险水平</p>
              <p className="text-[10px] text-[#f85149]/70">在风控页运行 VaR 确认组合风险</p>
            </div>
          </Link>
          <Link to="/backtest"
            className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[#58a6ff]/25 text-[#58a6ff] bg-[#111d2e] hover:bg-[#58a6ff]/10 transition-colors">
            <span className="text-base">🔬</span>
            <div>
              <p className="font-medium">对权重最高标的回测</p>
              <p className="text-[10px] text-[#58a6ff]/70">验证最优权重的历史表现</p>
            </div>
          </Link>
        </div>
        {/* 权重摘要供手动参考 */}
        <div className="bg-[#0d1117] rounded-lg p-3 text-[10px] text-[#6e7681]">
          <p className="font-medium text-[#8b949e] mb-1.5">优化权重（再平衡参考）</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(result.weights)
              .sort(([,a],[,b]) => b - a)
              .map(([sym, w]) => (
                <span key={sym} className="bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5">
                  <span className="font-mono text-[#e6edf3]">{sym}</span>
                  <span className="ml-1 text-[#58a6ff]">{(w * 100).toFixed(1)}%</span>
                </span>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}
