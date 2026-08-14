// ── 工作流 localStorage 持久化 ────────────────────────────────────
// 将运行中的工作流状态写入 localStorage，页面导航后可恢复。
// 历史记录保留最近 20 条已完成 / 已启动模拟盘的流程摘要。

import type { WorkflowState, BacktestVerdict } from "@/components/workflow/workflowTypes"

// ── 类型 ──────────────────────────────────────────────────────

export interface WorkflowHistoryEntry {
  id: string
  timestamp: number
  symbol: string
  market: string
  strategyName: string
  strategyId: string
  verdict: BacktestVerdict | null
  sharpe: number | null
  drawdown: number | null
  winRate: number | null
  positionPct: number
  instanceId: string | null        // live-strategy instance_id（若已启动）
  phase: "completed" | "paper_only"
}

// ── 存储 Key ─────────────────────────────────────────────────

const STATE_KEY    = "qb_wf_state"
const HISTORY_KEY  = "qb_wf_history"
const PLAYBOOK_KEY = "qb_playbook_progress"
const MAX_HISTORY  = 20

// ── 当前进度 ─────────────────────────────────────────────────

export function saveWorkflowState(state: WorkflowState): void {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)) } catch { /* quota / SSR guard */ }
}

export function loadWorkflowState(): WorkflowState | null {
  try {
    const raw = localStorage.getItem(STATE_KEY)
    return raw ? (JSON.parse(raw) as WorkflowState) : null
  } catch {
    return null
  }
}

export function clearWorkflowState(): void {
  try { localStorage.removeItem(STATE_KEY) } catch { /* 隐私模式下 localStorage 不可用：清理失败无副作用，忽略 */ }
}

// ── 历史记录 ─────────────────────────────────────────────────

/** 追加一条历史记录并返回更新后的完整列表 */
export function appendWorkflowHistory(entry: WorkflowHistoryEntry): WorkflowHistoryEntry[] {
  const list = loadWorkflowHistory()
  const updated = [entry, ...list].slice(0, MAX_HISTORY)
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(updated)) } catch { /* 配额超限或隐私模式：历史记录为非关键数据，降级为仅内存 */ }
  return updated
}

export function loadWorkflowHistory(): WorkflowHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    return raw ? (JSON.parse(raw) as WorkflowHistoryEntry[]) : []
  } catch {
    return []
  }
}

export function clearWorkflowHistory(): void {
  try { localStorage.removeItem(HISTORY_KEY) } catch { /* 同上：清理失败无副作用，忽略 */ }
}

// ── Playbook 进度（V3 · H4）──────────────────────────────────
//
// 只记「用户点过哪些步骤」，不反查后端真实进度 —— 那需要一堆新端点，
// 而且判错了比不判更让人困惑。所以这是**引导标记**，不是进度追踪。

/** playbookId → 已标记完成的 stepId 列表 */
export type PlaybookProgress = Record<string, string[]>

/** localStorage 里的内容可能被手改或跨版本残留：只收下形状正确的条目 */
function sanitizeProgress(raw: unknown): PlaybookProgress {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return {}

  const entries = Object.entries(raw as Record<string, unknown>).filter(
    (entry): entry is [string, string[]] =>
      Array.isArray(entry[1]) && entry[1].every((v) => typeof v === "string"),
  )
  return Object.fromEntries(entries)
}

export function loadPlaybookProgress(): PlaybookProgress {
  try {
    const raw = localStorage.getItem(PLAYBOOK_KEY)
    return raw ? sanitizeProgress(JSON.parse(raw)) : {}
  } catch {
    // JSON 畸形或隐私模式：退回空进度，引导从头开始比整页崩掉好
    return {}
  }
}

export function savePlaybookProgress(progress: PlaybookProgress): void {
  try { localStorage.setItem(PLAYBOOK_KEY, JSON.stringify(progress)) } catch { /* 配额超限：进度为非关键数据，降级为仅内存 */ }
}

/** 切换某一步的完成标记，返回更新后的完整进度（不修改入参） */
export function togglePlaybookStep(playbookId: string, stepId: string): PlaybookProgress {
  const current = loadPlaybookProgress()
  const done = current[playbookId] ?? []
  const next = done.includes(stepId)
    ? done.filter((id) => id !== stepId)
    : [...done, stepId]

  const updated: PlaybookProgress = { ...current, [playbookId]: next }
  savePlaybookProgress(updated)
  return updated
}

/** 重置一条 Playbook 的全部标记，其它路径不受影响 */
export function resetPlaybookProgress(playbookId: string): PlaybookProgress {
  const current = loadPlaybookProgress()
  const updated = Object.fromEntries(
    Object.entries(current).filter(([id]) => id !== playbookId),
  )
  savePlaybookProgress(updated)
  return updated
}
