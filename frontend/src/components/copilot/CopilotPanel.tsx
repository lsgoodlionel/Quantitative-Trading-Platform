import { useCallback, useState } from "react"
import {
  CopilotTranscript,
  type CopilotTurn,
  type DraftOutcome,
} from "@/components/copilot/CopilotTranscript"
import { Spinner } from "@/components/ui/Spinner"
import {
  useCopilotChat,
  useExecuteDraft,
  type CopilotDraft,
  type CopilotMessageIn,
} from "@/hooks/useCopilot"
import { usePermissions } from "@/hooks/useRbac"

/**
 * Copilot 对话侧栏。
 *
 * 会话只存在内存里（契约 §五：本期不做持久化），关闭面板不清空，刷新页面即重置。
 * 发送时把整段历史一起带上 —— 后端不存会话，上下文全靠这里传。
 */

let turnSeq = 0
function nextTurnId(): string {
  turnSeq += 1
  return `turn-${turnSeq}`
}

export function CopilotPanel({ onClose }: { onClose: () => void }) {
  const [turns, setTurns] = useState<CopilotTurn[]>([])
  const [input, setInput] = useState("")
  const [executingDraftId, setExecutingDraftId] = useState<string | null>(null)
  const [draftOutcomes, setDraftOutcomes] = useState<Record<string, DraftOutcome>>({})

  const { canTrade } = usePermissions()
  const chat = useCopilotChat()
  const executeDraft = useExecuteDraft()

  const send = useCallback(async () => {
    const question = input.trim()
    if (!question || chat.isPending) return

    const history: CopilotMessageIn[] = [
      ...turns.map((t) => ({ role: t.role, content: t.text })),
      { role: "user" as const, content: question },
    ]
    setTurns((prev) => [...prev, { id: nextTurnId(), role: "user", text: question }])
    setInput("")

    try {
      const reply = await chat.mutateAsync(history)
      setTurns((prev) => [
        ...prev,
        {
          id: nextTurnId(),
          role: "assistant",
          text: reply.text,
          cards: reply.cards,
          drafts: reply.drafts,
          needsSetup: reply.needs_setup,
          setupUrl: reply.setup_url,
          errorKind: reply.error_kind,
          truncated: reply.truncated,
        },
      ])
    } catch (error) {
      setTurns((prev) => [
        ...prev,
        {
          id: nextTurnId(),
          role: "assistant",
          text: `请求失败：${error instanceof Error ? error.message : "未知错误"}`,
        },
      ])
    }
  }, [chat, input, turns])

  const confirmDraft = useCallback(
    async (draft: CopilotDraft) => {
      setExecutingDraftId(draft.draft_id)
      try {
        await executeDraft.mutateAsync(draft)
        setDraftOutcomes((prev) => ({
          ...prev,
          [draft.draft_id]: { ok: true, message: "已执行，请到订单页查看结果。" },
        }))
      } catch (error) {
        setDraftOutcomes((prev) => ({
          ...prev,
          [draft.draft_id]: {
            ok: false,
            message: `执行失败：${error instanceof Error ? error.message : "未知错误"}`,
          },
        }))
      } finally {
        setExecutingDraftId(null)
      }
    },
    [executeDraft],
  )

  const cancelDraft = useCallback((draft: CopilotDraft) => {
    setDraftOutcomes((prev) => ({
      ...prev,
      [draft.draft_id]: { ok: false, message: "已取消，未执行任何操作。" },
    }))
  }, [])

  return (
    <aside
      className="fixed right-0 top-0 z-40 flex h-full w-full max-w-md flex-col border-l border-[#30363d] bg-[#0d1117] shadow-2xl"
      aria-label="平台助手"
    >
      <header className="flex items-center gap-2 border-b border-[#21262d] px-4 py-3">
        <h2 className="text-sm font-semibold text-[#e6edf3]">平台助手</h2>
        <span className="text-[10px] text-[#6e7681]">写操作一律需要你确认</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭助手"
          className="ml-auto text-[#8b949e] hover:text-[#e6edf3]"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        <CopilotTranscript
          turns={turns}
          canExecute={canTrade}
          executingDraftId={executingDraftId}
          draftOutcomes={draftOutcomes}
          onConfirmDraft={confirmDraft}
          onCancelDraft={cancelDraft}
        />
        {chat.isPending && (
          <div className="flex items-center gap-2 pt-3 text-[11px] text-[#8b949e]">
            <Spinner size="sm" />
            思考中…
          </div>
        )}
      </div>

      <form
        className="border-t border-[#21262d] p-3"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <div className="flex gap-2">
          <input
            className="input flex-1 text-xs"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="问点什么，比如「AAPL 现在多少钱」"
            aria-label="向助手提问"
            disabled={chat.isPending}
          />
          <button
            type="submit"
            className="btn btn-primary text-xs"
            disabled={chat.isPending || input.trim().length === 0}
          >
            发送
          </button>
        </div>
      </form>
    </aside>
  )
}
