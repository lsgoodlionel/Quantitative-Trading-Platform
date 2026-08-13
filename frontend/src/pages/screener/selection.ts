// ── 选股器多选状态工具（V3 G3）──────────────────────────────────
// 纯函数、不可变更新：每次返回新的 Set，便于 React 正确检测变化。

import type { ScreenerCandidate } from "@/hooks/useScreener"

/** 候选唯一键：同一代码可能出现在不同市场，必须带市场前缀 */
export function candidateKey(c: Pick<ScreenerCandidate, "symbol" | "market">): string {
  return `${c.market}-${c.symbol}`
}

/** 切换单个键的选中态，返回新集合 */
export function toggleKey(selected: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(selected)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

/** 当前结果集是否已全选（空结果集不算全选） */
export function isAllSelected(
  rows: readonly ScreenerCandidate[],
  selected: ReadonlySet<string>,
): boolean {
  return rows.length > 0 && rows.every((c) => selected.has(candidateKey(c)))
}

/** 全选 / 取消全选：已全选则清空，否则选中当前结果集全部 */
export function toggleAllKeys(
  rows: readonly ScreenerCandidate[],
  selected: ReadonlySet<string>,
): Set<string> {
  return isAllSelected(rows, selected)
    ? new Set<string>()
    : new Set(rows.map(candidateKey))
}
