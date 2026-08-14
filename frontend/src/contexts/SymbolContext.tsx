// ── 全局标的上下文（V3 · H3 · 1.2）──────────────────────────────
//
// ⚠️ 这个上下文**不是**标的的唯一真相，URL 才是。
// 用户分享链接、刷新页面、点后退时，唯一还在的就是地址栏。
// 上下文只做两件事：
//   1. 缓存「当前标的」，让没带 ?symbol= 的页面（如验证页）也能读到；
//   2. 提供 setSymbol，改的同时**同步写回 URL**。
// 地址栏一旦带上 ?symbol=，它就覆盖缓存。
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useSearchParams } from "react-router-dom"
import { normalizeMarket, readSymbolSelection } from "@/pages/market/symbolParam"
import type { Market } from "@/types"

export interface SymbolContextValue {
  symbol: string
  market: Market
  /** 设置当前标的；market 省略时沿用当前市场。同时写回 URL 的 ?symbol=&market= */
  setSymbol: (symbol: string, market?: Market) => void
}

const SymbolContext = createContext<SymbolContextValue | null>(null)

interface SymbolProviderProps {
  children: ReactNode
}

export function SymbolProvider({ children }: SymbolProviderProps) {
  const [searchParams, setSearchParams] = useSearchParams()

  const urlSymbol = (searchParams.get("symbol") ?? "").trim().toUpperCase()
  const urlMarket = normalizeMarket(searchParams.get("market"))

  // 惰性初值：首屏直接采信地址栏（无 ?symbol= 时回落到该市场的默认标的）
  const [selection, setSelection] = useState(() =>
    readSymbolSelection(searchParams.toString() ? `?${searchParams.toString()}` : ""),
  )

  // 地址栏带标的时以它为准 —— 站内 <Link>、命令面板跳转、浏览器后退都走这条路
  useEffect(() => {
    if (!urlSymbol) return
    setSelection((prev) =>
      prev.symbol === urlSymbol && prev.market === urlMarket
        ? prev
        : { symbol: urlSymbol, market: urlMarket },
    )
  }, [urlSymbol, urlMarket])

  // setSymbol 省略 market 时要沿用「当前」市场；用 ref 读避免把 selection 塞进依赖，
  // 否则每次换标的都会重建回调，订阅方跟着重渲染
  const marketRef = useRef<Market>(selection.market)
  useEffect(() => {
    marketRef.current = selection.market
  }, [selection.market])

  const setSymbol = useCallback(
    (symbol: string, market?: Market) => {
      const nextSymbol = symbol.trim().toUpperCase()
      // 空输入不是错误，是没选中任何标的：忽略，避免把 URL 写成 ?symbol=
      if (!nextSymbol) return

      const nextMarket = market ?? marketRef.current
      setSelection({ symbol: nextSymbol, market: nextMarket })

      // replace 而非 push：换标的是页面内的视图调整，
      // 不该把「后退」变成逐个标的的回放，那会淹掉真正的页面级历史
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("symbol", nextSymbol)
          next.set("market", nextMarket)
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const value = useMemo<SymbolContextValue>(
    () => ({ symbol: selection.symbol, market: selection.market, setSymbol }),
    [selection.symbol, selection.market, setSymbol],
  )

  return <SymbolContext.Provider value={value}>{children}</SymbolContext.Provider>
}

/** 读取全局标的上下文。缺 Provider 时直接抛错，而不是返回一个假的默认标的。 */
export function useSymbolContext(): SymbolContextValue {
  const ctx = useContext(SymbolContext)
  if (!ctx) {
    throw new Error("useSymbolContext 必须在 <SymbolProvider> 内使用")
  }
  return ctx
}
