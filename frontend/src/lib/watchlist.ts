// ── 自选池 localStorage 持久化 ──────────────────────────────────
// 选股器筛出的标的可一键加入自选池，跨页面 / 刷新后仍在。
// 与 useWorkflowStorage 同一约定：隐私模式或配额超限时降级为仅内存，不抛错。

export interface WatchlistItem {
  symbol: string
  market: string
  name: string
}

const STORAGE_KEY = "qb_watchlist"
const MAX_ITEMS = 100

function itemKey(item: Pick<WatchlistItem, "symbol" | "market">): string {
  return `${item.market}:${item.symbol}`
}

export function loadWatchlist(): WatchlistItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isWatchlistItem)
  } catch {
    return []
  }
}

function isWatchlistItem(value: unknown): value is WatchlistItem {
  if (typeof value !== "object" || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.symbol === "string" && typeof item.market === "string"
    && typeof item.name === "string"
}

function persist(items: WatchlistItem[]): WatchlistItem[] {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {
    /* 配额超限或隐私模式：自选池为非关键数据，降级为仅内存 */
  }
  return items
}

/**
 * 批量加入自选池（去重，保序）。返回 { list, added } —— added 为实际新增条数，
 * 便于调用方给出「已加入 N 只（M 只已存在）」这类精确反馈。
 */
export function addToWatchlist(
  incoming: readonly WatchlistItem[],
): { list: WatchlistItem[]; added: number } {
  const current = loadWatchlist()
  const existing = new Set(current.map(itemKey))
  const fresh = incoming.filter((item) => !existing.has(itemKey(item)))
  const merged = [...current, ...fresh].slice(0, MAX_ITEMS)
  return { list: persist(merged), added: merged.length - current.length }
}

export function removeFromWatchlist(
  target: Pick<WatchlistItem, "symbol" | "market">,
): WatchlistItem[] {
  const key = itemKey(target)
  return persist(loadWatchlist().filter((item) => itemKey(item) !== key))
}

export function clearWatchlist(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 同上：清理失败无副作用，忽略 */
  }
}
