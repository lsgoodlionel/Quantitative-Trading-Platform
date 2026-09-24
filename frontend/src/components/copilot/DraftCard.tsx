import { useState } from "react"
import { draftTone, type CopilotDraft } from "@/hooks/useCopilot"

/**
 * 待确认草稿卡片。
 *
 * **必须显示完整参数**：标的、方向、数量、价格、预估金额。一个只写着
 * 「买入 AAPL」而不写数量的确认按钮，等于没有确认 —— 用户点下去时并不知道
 * 自己同意了什么。参数由后端解析并逐项给出（`fields`），这里只负责渲染，
 * 不做任何二次推断。
 *
 * 卡片上还写着执行时真正走的既有端点，任何人看一眼就知道 Copilot 没有
 * 另开一条绕过风控的通路。
 */

const TONE_STYLES = {
  danger: "border-[#f85149]/50 bg-[#2a1215]/40",
  warn: "border-[#e3b341]/50 bg-[#2a2415]/40",
} as const

const TONE_BUTTON = {
  danger: "btn-danger",
  warn: "btn-primary",
} as const

export interface DraftCardProps {
  draft: CopilotDraft
  /** false = 当前角色无权执行（Viewer），按钮禁用并说明原因 */
  canExecute: boolean
  busy: boolean
  /** null = 还没执行过 */
  outcome: { ok: boolean; message: string } | null
  onConfirm: (draft: CopilotDraft) => void
  onCancel: (draft: CopilotDraft) => void
}

export function DraftCard({
  draft,
  canExecute,
  busy,
  outcome,
  onConfirm,
  onCancel,
}: DraftCardProps) {
  const [dismissed, setDismissed] = useState(false)
  const tone = draftTone(draft.action)
  const settled = dismissed || outcome !== null

  return (
    <section
      className={`rounded-md border p-3 space-y-2 ${TONE_STYLES[tone]}`}
      aria-label={`待确认草稿：${draft.title}`}
    >
      <header className="flex items-center gap-2">
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-[#e3b341]/40 text-[#e3b341] bg-[#2a2415]">
          待确认
        </span>
        <h3 className="text-sm font-semibold text-[#e6edf3]">{draft.title}</h3>
      </header>

      <p className="text-[11px] text-[#8b949e] leading-relaxed">{draft.summary}</p>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
        {draft.fields.map((field) => (
          <div key={field.label} className="contents">
            <dt className="text-[11px] text-[#8b949e]">{field.label}</dt>
            <dd
              className={`text-[11px] text-right font-mono ${
                field.emphasis ? "text-[#e6edf3] font-semibold" : "text-[#c9d1d9]"
              }`}
            >
              {field.value}
            </dd>
          </div>
        ))}
      </dl>

      {draft.legs.length > 0 && <DraftLegs legs={draft.legs} />}

      {draft.warnings.map((warning) => (
        <p key={warning} className="text-[11px] text-[#e3b341]">
          ⚠ {warning}
        </p>
      ))}

      <p className="text-[10px] text-[#6e7681] font-mono">执行通路：{draft.endpoint}</p>

      {outcome === null ? (
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            className={`btn ${TONE_BUTTON[tone]} text-xs`}
            disabled={!canExecute || busy || settled}
            onClick={() => onConfirm(draft)}
          >
            {busy ? "执行中…" : "确认执行"}
          </button>
          <button
            type="button"
            className="btn btn-ghost text-xs"
            disabled={busy || settled}
            onClick={() => {
              setDismissed(true)
              onCancel(draft)
            }}
          >
            取消
          </button>
          {!canExecute && (
            <span className="text-[11px] text-[#8b949e] self-center">
              需要交易员及以上角色
            </span>
          )}
        </div>
      ) : (
        <p
          role="status"
          className={`text-[11px] ${outcome.ok ? "text-[#3fb950]" : "text-[#f85149]"}`}
        >
          {outcome.message}
        </p>
      )}

      {dismissed && outcome === null && (
        <p role="status" className="text-[11px] text-[#8b949e]">
          已取消，未执行任何操作。
        </p>
      )}
    </section>
  )
}

function DraftLegs({ legs }: { legs: Record<string, unknown>[] }) {
  return (
    <div className="max-h-40 overflow-y-auto rounded border border-[#30363d]">
      <table className="w-full text-[11px]">
        <thead className="text-[#8b949e]">
          <tr>
            <th className="text-left px-2 py-1 font-normal">标的</th>
            <th className="text-right px-2 py-1 font-normal">调整股数</th>
            <th className="text-right px-2 py-1 font-normal">价格</th>
            <th className="text-right px-2 py-1 font-normal">金额</th>
          </tr>
        </thead>
        <tbody className="font-mono text-[#c9d1d9]">
          {legs.map((leg, index) => (
            <tr key={`${String(leg.symbol)}-${index}`} className="border-t border-[#21262d]">
              <td className="px-2 py-1">{String(leg.symbol ?? "-")}</td>
              <td className="px-2 py-1 text-right">{String(leg.delta_qty ?? "-")}</td>
              <td className="px-2 py-1 text-right">{String(leg.price ?? "-")}</td>
              <td className="px-2 py-1 text-right">{String(leg.delta_value ?? "-")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
