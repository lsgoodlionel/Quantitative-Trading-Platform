import { useEffect, useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import type { ActiveModel, ProviderStatus } from "@/hooks/useLlmConfig"

export interface ActiveModelBarProps {
  active: ActiveModel
  /** 只有 ready 的 provider 才能被选为生效项 */
  readyProviders: ProviderStatus[]
  canEdit: boolean
  busy: boolean
  onApply: (providerId: string, model: string) => void
}

/** 顶部「当前生效」选择器 —— 其余所有 AI 功能都用这里选中的 provider + 模型。 */
export function ActiveModelBar({
  active,
  readyProviders,
  canEdit,
  busy,
  onApply,
}: ActiveModelBarProps) {
  const [providerId, setProviderId] = useState(active.provider_id ?? "")
  const [model, setModel] = useState(active.model ?? "")

  // 服务端状态变化（保存/删除后 refetch）时把本地草稿拉回一致
  useEffect(() => {
    setProviderId(active.provider_id ?? "")
    setModel(active.model ?? "")
  }, [active.provider_id, active.model])

  const selected = readyProviders.find((p) => p.id === providerId)
  const dirty = providerId !== active.provider_id || model !== active.model
  const canApply = canEdit && !busy && dirty && providerId.length > 0 && model.trim().length > 0

  if (readyProviders.length === 0) return null

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-baseline gap-3">
        <h2 className="text-sm font-semibold text-[#e6edf3]">当前生效</h2>
        <p className="text-xs text-[#8b949e]">
          {active.configured
            ? active.explicit
              ? "已由你手动指定"
              : "自动选择（未手动指定时按本地优先顺序取第一个可用项）"
            : "尚未有可用模型"}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <label className="flex-1 min-w-[180px]">
          <span className="sr-only">生效服务商</span>
          <select
            className="input w-full text-xs"
            value={providerId}
            onChange={(e) => {
              const next = readyProviders.find((p) => p.id === e.target.value)
              setProviderId(e.target.value)
              setModel(next?.default_model ?? "")
            }}
            aria-label="生效服务商"
          >
            {readyProviders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex-1 min-w-[180px]">
          <span className="sr-only">生效模型</span>
          <input
            className="input w-full font-mono text-xs"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            list="llm-active-models"
            placeholder="模型名"
            spellCheck={false}
            aria-label="生效模型"
          />
          <datalist id="llm-active-models">
            {(selected?.suggested_models ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>

        <button
          type="button"
          className="btn btn-primary text-xs py-1 px-4 disabled:opacity-40"
          onClick={() => onApply(providerId, model.trim())}
          disabled={!canApply}
          title={canEdit ? undefined : "需交易员及以上角色"}
        >
          {busy ? <Spinner size="sm" /> : "切换"}
        </button>
      </div>
    </div>
  )
}
