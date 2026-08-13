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
 * 首次使用时用 `defaults` 播种，之后一律读用户自己的列表。
 *
 * 必须区分「从未初始化」与「用户主动清空」：两者 `loadWatchlist()` 都返回 []，
 * 若一律按空处理就会把默认列表塞回去 —— 用户删掉的标的每次刷新又冒出来。
 * 这里以 **storage key 是否存在** 为判据，清空时会写入一个空数组把 key 留住。
 */
export function loadWatchlistOrSeed(
  defaults: readonly WatchlistItem[],
): WatchlistItem[] {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(STORAGE_KEY)
  } catch {
    // 隐私模式读不到：直接给默认列表，本次会话内可用但不持久
    return [...defaults]
  }
  if (raw === null) return persist([...defaults])
  return loadWatchlist()
}

/**
 * 覆盖式写入。供调用方在本地状态变更后同步落盘。
 *
 * 泛型是为了**保留调用方更窄的元素类型**（如 Market.tsx 的 WatchItem，
 * 其 market 是 "US"|"HK"|"A" 而非 string），否则调用处得写 `as` 强转，
 * 而强转正是会掩盖真实类型错误的东西。
 */
export function saveWatchlist<T extends WatchlistItem>(items: readonly T[]): T[] {
  persist(items.slice(0, MAX_ITEMS))
  return items.slice(0, MAX_ITEMS)
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

/**
 * 清空自选池。
 *
 * 刻意写入空数组而不是 `removeItem` —— 删掉 key 会让
 * `loadWatchlistOrSeed` 判定为「从未初始化」，下次进页面又把默认列表播种回来。
 */
export function clearWatchlist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "[]")
  } catch {
    /* 同上：清理失败无副作用，忽略 */
  }
}
