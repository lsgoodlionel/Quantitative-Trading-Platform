// ── 命令面板搜索与跳转解析（V3 · H3）────────────────────────────
//
// 搜索有意做得很笨：不区分权重、不做模糊排序，只做大小写不敏感的子串匹配，
// 再按固定类型顺序分组。条目总量在两位数，排序算法带来的收益远小于
// 「结果位置会跳」带来的困惑。
//
// ⚠️ 本期不支持拼音/首字母搜索：那需要引入拼音库，违反「不新增依赖」。
import type { Market } from "@/types"
import type { CommandGroup, CommandItem, CommandSymbolRef } from "./commandTypes"
import { KIND_LABELS, KIND_ORDER } from "./commandTypes"

/** 条目参与匹配的全部文本 */
function haystack(item: CommandItem): string {
  return [item.label, item.hint ?? "", ...(item.keywords ?? []), item.id]
    .join(" ")
    .toLowerCase()
}

/** 子串匹配。空查询（或只有空白）返回全部条目。 */
export function filterCommands(
  items: readonly CommandItem[],
  query: string,
): CommandItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return [...items]
  return items.filter((item) => haystack(item).includes(q))
}

/** 按类型分组，顺序固定为 页面 → 标的 → 动作；空组丢弃。 */
export function groupCommands(items: readonly CommandItem[]): CommandGroup[] {
  return KIND_ORDER.map((kind) => ({
    kind,
    label: KIND_LABELS[kind],
    items: items.filter((item) => item.kind === kind),
  })).filter((group) => group.items.length > 0)
}

/**
 * 往已有路由上补查询参数，保留原有参数。
 *
 * 直接字符串拼 `?` 会把 `/portfolio?tab=optimizer` 拼成两个 `?`，
 * 也会在重复设置时留下同名参数 —— 统一走 URLSearchParams。
 */
export function withQuery(to: string, params: Record<string, string>): string {
  const [path, rawQuery = ""] = to.split("?")
  const search = new URLSearchParams(rawQuery)
  for (const [key, value] of Object.entries(params)) {
    search.set(key, value)
  }
  const query = search.toString()
  return query ? `${path}?${query}` : path
}

/**
 * 解析条目最终的跳转地址。
 *
 * - 标的条目：把自己的代码写进 URL（面板不直接调 setSymbol —— URL 才是唯一真相，
 *   跳转后由 SymbolProvider 从地址栏同步，这样分享出去的链接也带着标的）
 * - `withSymbol` 动作：把**当前**标的预置进目标页
 * - 其余：原样跳转
 */
export function resolveCommandTarget(
  item: CommandItem,
  current: { symbol: string; market: Market },
): string {
  const ref: CommandSymbolRef | null = item.symbol
    ? item.symbol
    : item.withSymbol
      ? { symbol: current.symbol, market: current.market }
      : null

  if (!ref || !ref.symbol) return item.to
  return withQuery(item.to, { symbol: ref.symbol, market: ref.market })
}
