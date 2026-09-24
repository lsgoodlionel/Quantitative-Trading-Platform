// ── 命令面板条目类型（V3 · H3）──────────────────────────────────
import type { Market } from "@/types"

/** 条目类型：页面 / 标的 / 动作。面板按这个顺序分组展示。 */
export type CommandKind = "page" | "symbol" | "action"

export interface CommandSymbolRef {
  symbol: string
  market: Market
}

export interface CommandItem {
  /** 全局唯一 id，同时用作 React key */
  id: string
  kind: CommandKind
  /** 展示名（中文为主） */
  label: string
  /** 一行说明，同时参与搜索匹配 */
  hint?: string
  /** 额外匹配词：英文名、缩写、旧页面名 */
  keywords?: readonly string[]
  /** 目标路由，含 `?tab=` —— 面板必须能直达具体 Tab，而不是页面默认 Tab */
  to: string
  /** 标的类条目携带的代码与市场 */
  symbol?: CommandSymbolRef
  /** 动作类条目：跳转时把「当前标的」预置进 URL */
  withSymbol?: boolean
}

/** 分组展示顺序（固定，不随搜索结果变化，避免条目位置跳来跳去） */
export const KIND_ORDER: readonly CommandKind[] = ["page", "symbol", "action"]

export const KIND_LABELS: Record<CommandKind, string> = {
  page: "页面",
  symbol: "标的",
  action: "动作",
}

export interface CommandGroup {
  kind: CommandKind
  label: string
  items: CommandItem[]
}
