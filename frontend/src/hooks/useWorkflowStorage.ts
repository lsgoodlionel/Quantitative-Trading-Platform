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

const STATE_KEY   = "qb_wf_state"
const HISTORY_KEY = "qb_wf_history"
const MAX_HISTORY = 20

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
  try { localStorage.removeItem(STATE_KEY) } catch {}
}

// ── 历史记录 ─────────────────────────────────────────────────

/** 追加一条历史记录并返回更新后的完整列表 */
export function appendWorkflowHistory(entry: WorkflowHistoryEntry): WorkflowHistoryEntry[] {
  const list = loadWorkflowHistory()
  const updated = [entry, ...list].slice(0, MAX_HISTORY)
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(updated)) } catch {}
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
  try { localStorage.removeItem(HISTORY_KEY) } catch {}
}
