import { DraftCard } from "@/components/copilot/DraftCard"
import { ResultCard } from "@/components/copilot/ResultCard"
import { SetupGuidance } from "@/components/copilot/SetupGuidance"
import { describeErrorKind, type CopilotCard, type CopilotDraft } from "@/hooks/useCopilot"

/**
 * 消息流（纯展示，不发请求）。
 *
 * 一轮助手回复可能同时带：自然语言、结构化卡片、待确认草稿。三者分开渲染，
 * 草稿永远排在最后 —— 用户读完结论再看要不要点确认。
 */

export interface CopilotTurn {
  id: string
  role: "user" | "assistant"
  text: string
  cards?: CopilotCard[]
  drafts?: CopilotDraft[]
  needsSetup?: boolean
  setupUrl?: string | null
  errorKind?: string | null
  truncated?: boolean
}

export interface DraftOutcome {
  ok: boolean
  message: string
}

export interface CopilotTranscriptProps {
  turns: CopilotTurn[]
  canExecute: boolean
  /** 正在执行的草稿 ID；null = 没有 */
  executingDraftId: string | null
  draftOutcomes: Record<string, DraftOutcome>
  onConfirmDraft: (draft: CopilotDraft) => void
  onCancelDraft: (draft: CopilotDraft) => void
}

export function CopilotTranscript({
  turns,
  canExecute,
  executingDraftId,
  draftOutcomes,
  onConfirmDraft,
  onCancelDraft,
}: CopilotTranscriptProps) {
  if (turns.length === 0) return <EmptyHint />

  return (
    <div className="space-y-3">
      {turns.map((turn) => (
        <article key={turn.id} aria-label={turn.role === "user" ? "我的提问" : "助手回复"}>
          {turn.role === "user" ? (
            <p className="text-xs text-[#e6edf3] bg-[#1c2a3a] border border-[#388bfd]/30 rounded-md px-3 py-2 whitespace-pre-line">
              {turn.text}
            </p>
          ) : (
            <AssistantTurn
              turn={turn}
              canExecute={canExecute}
              executingDraftId={executingDraftId}
              draftOutcomes={draftOutcomes}
              onConfirmDraft={onConfirmDraft}
              onCancelDraft={onCancelDraft}
            />
          )}
        </article>
      ))}
    </div>
  )
}

function AssistantTurn({
  turn,
  canExecute,
  executingDraftId,
  draftOutcomes,
  onConfirmDraft,
  onCancelDraft,
}: { turn: CopilotTurn } & Omit<CopilotTranscriptProps, "turns">) {
  const errorHint = describeErrorKind(turn.errorKind ?? null)

  if (turn.needsSetup) {
    return <SetupGuidance text={turn.text} setupUrl={turn.setupUrl ?? null} />
  }

  return (
    <div className="space-y-2">
      {errorHint && (
        <p role="alert" className="text-[11px] text-[#f85149]">
          {errorHint}
        </p>
      )}
      {turn.text && (
        <p className="text-xs text-[#c9d1d9] leading-relaxed whitespace-pre-line">{turn.text}</p>
      )}
      {turn.truncated && (
        <p className="text-[10px] text-[#e3b341]">回复因工具调用轮次上限而提前结束。</p>
      )}
      {(turn.cards ?? []).map((card, index) => (
        <ResultCard key={`${card.kind}-${index}`} card={card} />
      ))}
      {(turn.drafts ?? []).map((draft) => (
        <DraftCard
          key={draft.draft_id}
          draft={draft}
          canExecute={canExecute}
          busy={executingDraftId === draft.draft_id}
          outcome={draftOutcomes[draft.draft_id] ?? null}
          onConfirm={onConfirmDraft}
          onCancel={onCancelDraft}
        />
      ))}
    </div>
  )
}

function EmptyHint() {
  return (
    <div className="text-[11px] text-[#8b949e] leading-relaxed space-y-1">
      <p>可以问我：</p>
      <ul className="list-disc pl-5 space-y-0.5">
        <li>AAPL 现在多少钱？</li>
        <li>筛选一下市盈率低于 15 的美股</li>
        <li>用双均线策略回测 AAPL 最近一年</li>
        <li>帮我拟一笔 AAPL 的买单（会先生成草稿，确认后才执行）</li>
      </ul>
    </div>
  )
}
