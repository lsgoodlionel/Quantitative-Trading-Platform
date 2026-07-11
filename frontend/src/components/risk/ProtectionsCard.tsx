import { useEffect, useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useProtectionsConfig,
  useUpdateProtectionsConfig,
} from "@/hooks/useProtections"
import type { ProtectionRuleConfig, ProtectionType, ProtectionsConfig } from "@/types"

export const PROTECTION_LABELS: Record<ProtectionType, string> = {
  stoploss_guard: "止损熔断",
  cooldown_period: "冷却期",
  max_drawdown: "最大回撤熔断",
  low_profit_pairs: "低盈利标的锁定",
}

const PROTECTION_DESC: Record<ProtectionType, string> = {
  stoploss_guard: "回看窗口内止损次数达阈值即熔断",
  cooldown_period: "成交后在冷却时长内禁止再入场",
  max_drawdown: "窗口内权益回撤超阈值即全局熔断",
  low_profit_pairs: "标的长期低盈利/亏损则锁定",
}

type FieldKind = "number" | "bool"

interface FieldDef {
  key: keyof ProtectionRuleConfig
  label: string
  kind: FieldKind
  step?: number
  hint?: string
}

// 每种防护展示的类型专属字段（stop_duration_minutes 通用，放最后）
const PARAM_FIELDS: Record<ProtectionType, FieldDef[]> = {
  stoploss_guard: [
    { key: "lookback_minutes", label: "回看 (分钟)", kind: "number" },
    { key: "trade_limit", label: "止损次数阈值", kind: "number" },
    { key: "required_profit", label: "计入盈亏比上限", kind: "number", step: 0.01 },
    { key: "only_per_symbol", label: "仅锁标的 (不全局)", kind: "bool" },
    { key: "stop_duration_minutes", label: "锁定时长 (分钟)", kind: "number" },
  ],
  cooldown_period: [
    { key: "stop_duration_minutes", label: "冷却时长 (分钟)", kind: "number" },
  ],
  max_drawdown: [
    { key: "lookback_minutes", label: "回看 (分钟)", kind: "number" },
    { key: "trade_limit", label: "最少交易数", kind: "number" },
    { key: "max_allowed_drawdown", label: "回撤阈值 (0~1)", kind: "number", step: 0.01 },
    { key: "stop_duration_minutes", label: "锁定时长 (分钟)", kind: "number" },
  ],
  low_profit_pairs: [
    { key: "lookback_minutes", label: "回看 (分钟)", kind: "number" },
    { key: "required_trades", label: "最少交易数", kind: "number" },
    { key: "min_profit_ratio", label: "最低盈亏比 (0~1)", kind: "number", step: 0.01 },
    { key: "stop_duration_minutes", label: "锁定时长 (分钟)", kind: "number" },
  ],
}

function Toggle({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
        on ? "bg-[#3fb950]" : "bg-[#30363d]"
      }`}
      aria-label={label}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          on ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  )
}

interface RuleBlockProps {
  rule: ProtectionRuleConfig
  onChange: (updated: ProtectionRuleConfig) => void
}

function RuleBlock({ rule, onChange }: RuleBlockProps) {
  const fields = PARAM_FIELDS[rule.type] ?? []

  return (
    <div className="py-3 border-b border-[#21262d]/60 last:border-0">
      <div className="flex items-center gap-3">
        <Toggle
          on={rule.enabled}
          label={rule.enabled ? "禁用" : "启用"}
          onClick={() => onChange({ ...rule, enabled: !rule.enabled })}
        />
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium ${rule.enabled ? "text-[#e6edf3]" : "text-[#6e7681]"}`}>
            {PROTECTION_LABELS[rule.type]}
          </p>
          <p className="text-[11px] text-[#6e7681]">{PROTECTION_DESC[rule.type]}</p>
        </div>
      </div>

      {rule.enabled && (
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 pl-12">
          {fields.map((f) =>
            f.kind === "bool" ? (
              <label key={f.key} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-[#58a6ff]"
                  checked={Boolean(rule[f.key])}
                  onChange={(e) => onChange({ ...rule, [f.key]: e.target.checked })}
                />
                <span className="text-xs text-[#8b949e]">{f.label}</span>
              </label>
            ) : (
              <div key={f.key} className="flex items-center justify-between gap-2">
                <span className="text-xs text-[#8b949e] truncate">{f.label}</span>
                <input
                  type="number"
                  step={f.step ?? 1}
                  className="input w-24 text-xs font-mono"
                  value={Number(rule[f.key] ?? 0)}
                  onChange={(e) =>
                    onChange({ ...rule, [f.key]: parseFloat(e.target.value) || 0 })
                  }
                />
              </div>
            ),
          )}
        </div>
      )}
    </div>
  )
}

export function ProtectionsCard() {
  const { data: config, isLoading } = useProtectionsConfig()
  const { mutate: save, isPending: saving } = useUpdateProtectionsConfig()
  const { toast } = useToast()

  const [local, setLocal] = useState<ProtectionsConfig | null>(null)

  useEffect(() => {
    if (config && !local) setLocal(config)
  }, [config, local])

  function handleRuleChange(idx: number, updated: ProtectionRuleConfig) {
    if (!local) return
    const rules = local.rules.map((r, i) => (i === idx ? updated : r))
    setLocal({ ...local, rules })
  }

  function handleSave() {
    if (!local) return
    save(local, {
      onSuccess: () => toast("防护配置已更新", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleReset() {
    if (config) setLocal(config)
  }

  return (
    <div className="card mt-6">
      <div className="card-header flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-[#e6edf3]">动态保护 / 熔断</h2>
          {local && (
            <Toggle
              on={local.is_active}
              label={local.is_active ? "停用防护" : "启用防护"}
              onClick={() => setLocal({ ...local, is_active: !local.is_active })}
            />
          )}
          {local && !local.is_active && (
            <span className="text-[10px] text-[#6e7681]">已停用</span>
          )}
        </div>
        <div className="flex gap-2">
          <button className="btn btn-ghost text-xs" onClick={handleReset} disabled={saving}>
            重置
          </button>
          <button
            className="btn btn-primary text-xs"
            onClick={handleSave}
            disabled={saving || !local}
          >
            {saving ? <Spinner size="sm" /> : "保存"}
          </button>
        </div>
      </div>

      {isLoading && <div className="flex justify-center py-8"><Spinner size="lg" /></div>}

      {local && (
        <div className={local.is_active ? "" : "opacity-50"}>
          {local.rules.map((rule, idx) => (
            <RuleBlock
              key={rule.type}
              rule={rule}
              onChange={(updated) => handleRuleChange(idx, updated)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
