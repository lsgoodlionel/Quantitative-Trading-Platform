import { Navigate, useSearchParams } from "react-router-dom"

interface LegacyRedirectProps {
  /** 新页面路径，如 `/portfolio` */
  to: string
  /** 落地时选中的 Tab，写进 `?tab=` */
  tab: string
}

/**
 * 旧路径 → 新页面 Tab 的重定向（V3 · H1）。
 *
 * 两件必须做对的事：
 * 1. **replace**：不加的话用户按「后退」会被弹回旧路径、再被重定向，形成退不出去的循环。
 * 2. **保留查询参数**：书签和站内链接带着 `?symbols=` / `?strategy=` 这类参数，
 *    直接 `<Navigate to="/portfolio?tab=optimizer">` 会把它们丢掉，
 *    所有指向具体标的的链接就全废了。
 */
export function LegacyRedirect({ to, tab }: LegacyRedirectProps) {
  const [searchParams] = useSearchParams()

  const next = new URLSearchParams(searchParams)
  next.set("tab", tab)

  return <Navigate to={`${to}?${next.toString()}`} replace />
}
