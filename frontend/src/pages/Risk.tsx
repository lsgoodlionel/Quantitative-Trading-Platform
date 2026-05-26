import { useState, useEffect } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { useRiskConfig, useRiskSummary, useUpdateRiskConfig } from "@/hooks/useRisk"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import type { RiskConfig, RiskRule } from "@/types"

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

export function Risk() {
  const { data: config, isLoading } = useRiskConfig()
  const { data: summary } = useRiskSummary()
  const { mutate: updateConfig, isPending: saving } = useUpdateRiskConfig()
  const { toast } = useToast()

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
    <AppShell title="风控配置">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Rules */}
        <div className="lg:col-span-2">
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

        {/* Summary */}
        <div className="space-y-4">
          <div className="card">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">今日汇总</h3>
            {summary ? (
              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">提交订单数</span>
                  <span className="font-mono text-[#e6edf3]">{summary.daily_orders_submitted}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">今日实现盈亏</span>
                  <span className={`font-mono ${summary.daily_realized_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {summary.daily_realized_pnl >= 0 ? "+" : ""}${summary.daily_realized_pnl.toFixed(2)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#8b949e]">权益峰值</span>
                  <span className="font-mono text-[#e6edf3]">${summary.peak_equity.toLocaleString()}</span>
                </div>
              </div>
            ) : (
              <p className="text-[#6e7681] text-sm">加载中…</p>
            )}
          </div>

          {/* Violations */}
          {summary && summary.violations.length > 0 && (
            <div className="card border-[#f85149]/20">
              <h3 className="text-sm font-semibold text-[#f85149] mb-3">
                ⚠ 近期告警 ({summary.violations.length})
              </h3>
              <div className="space-y-2">
                {summary.violations.slice(0, 5).map((v, i) => (
                  <div key={i} className="text-xs bg-[#2a1b1b] border border-[#f85149]/20 rounded px-3 py-2">
                    <p className="text-[#f85149] font-medium">{RULE_LABELS[v.rule_type] ?? v.rule_type}</p>
                    <p className="text-[#8b949e] mt-0.5">{v.message}</p>
                    <p className="text-[#6e7681] mt-0.5">{v.timestamp.slice(0, 16)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {summary && summary.violations.length === 0 && (
            <div className="card border-[#3fb950]/20 bg-[#162a1e]/20">
              <p className="text-[#3fb950] text-sm text-center py-2">✓ 无风控告警</p>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  )
}
