import { useCallback } from "react"
import { useSearchParams } from "react-router-dom"

/**
 * 把页面 Tab 状态放进 URL 的 `?tab=`（V3 · H1）。
 *
 * 「URL 即状态」：刷新后停在原 Tab、链接可分享、可后退。
 * 切 Tab 走 push（不是 replace），所以浏览器「后退」能回到上一个 Tab。
 *
 * 写入时只改 `tab` 一个键，页面上其它查询参数（如行情的 `symbol`、
 * 组合优化的 `symbols`）原样保留 —— 否则切一下 Tab 就把标的选择弄丢了。
 *
 * @param tabs   合法 Tab 列表；URL 上出现列表外的值时回落到 fallback
 * @param fallback 缺省 Tab
 */
export function useUrlTab<T extends string>(
  tabs: readonly T[],
  fallback: T,
): [T, (tab: T) => void] {
  const [searchParams, setSearchParams] = useSearchParams()

  const raw = searchParams.get("tab")
  // URL 可能被手改或来自旧版本链接：不认识的 Tab 一律回落，避免渲染空白页
  const active = tabs.includes(raw as T) ? (raw as T) : fallback

  const setTab = useCallback(
    (tab: T) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("tab", tab)
          return next
        },
        { replace: false },
      )
    },
    [setSearchParams],
  )

  return [active, setTab]
}
