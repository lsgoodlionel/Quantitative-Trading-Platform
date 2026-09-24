// ── Playbook 类型（V3 · H4）─────────────────────────────────────
//
// Playbook 是 TradingWorkflow 的「数据化」版本：把原本硬编码在组件里的步骤
// 抽成纯数据，于是同一套渲染能承载三条不同路径。
//
// ⚠️ Playbook 是**引导**，不是进度追踪：完成状态由用户手动标记并存在
// localStorage，不反查后端「他到底跑没跑过回测」。判错比不判更让人困惑，
// 而且那需要一堆新端点。

export type PlaybookId = "single-symbol" | "discovery-portfolio" | "factor-research"

export interface PlaybookStep {
  /** 在同一条 Playbook 内唯一；同时作为 localStorage 里的标记键 */
  id: string
  title: string
  /** 这一步要做什么 */
  description: string
  /** 目标路由，含 `?tab=`，必须直达具体 Tab */
  to: string
  /** 完成判据：给用户自查的一句话，不是系统校验 */
  criteria: string
  /** 跳转时把当前标的预置进 URL */
  withSymbol?: boolean
}

export interface Playbook {
  id: PlaybookId
  name: string
  icon: string
  /** 一句话说明这条路径适合什么场景 */
  summary: string
  steps: readonly PlaybookStep[]
}
