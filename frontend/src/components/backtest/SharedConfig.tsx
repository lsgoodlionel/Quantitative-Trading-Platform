import { createContext, useContext, useMemo } from "react"
import type { ReactNode } from "react"
import type { BacktestRequest, Frequency, Market } from "@/types"

// ── 共享配置（V3 · H2）─────────────────────────────────────────
// 回测页所有 Tab 共用同一份 symbol / 策略 / 日期 / 市场 / 频率 / 初始资金，
// 改一处即全 Tab 生效，消除「每个表单重填一遍」的摩擦。

export interface SharedBacktestConfig {
  strategy_name: string
  symbol: string
  market: Market
  frequency: Frequency
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
}

/** 各验证端点通用的请求字段（不含 params —— 部分端点不接受）。 */
export interface SharedRequestBase {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
}

export function toRequestBase(config: SharedBacktestConfig): SharedRequestBase {
  return {
    strategy_name: config.strategy_name,
    symbol: config.symbol,
    market: config.market,
    frequency: config.frequency,
    start_date: config.start_date,
    end_date: config.end_date,
    initial_cash: config.initial_cash,
  }
}

export interface SharedConfigValue {
  config: SharedBacktestConfig
  update: <K extends keyof BacktestRequest>(key: K, value: BacktestRequest[K]) => void
  strategies: { name: string; description: string }[]
}

const SharedConfigContext = createContext<SharedConfigValue | null>(null)

interface SharedConfigProviderProps extends SharedConfigValue {
  children: ReactNode
}

export function SharedConfigProvider({ config, update, strategies, children }: SharedConfigProviderProps) {
  const value = useMemo<SharedConfigValue>(
    () => ({ config, update, strategies }),
    [config, update, strategies],
  )
  return <SharedConfigContext.Provider value={value}>{children}</SharedConfigContext.Provider>
}

export function useSharedConfig(): SharedConfigValue {
  const ctx = useContext(SharedConfigContext)
  if (!ctx) {
    throw new Error("useSharedConfig 必须在 <SharedConfigProvider> 内使用")
  }
  return ctx
}
