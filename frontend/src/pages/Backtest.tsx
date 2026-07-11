import { useState, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useStrategies, useRunBacktest } from "@/hooks/useBacktest"
import { ConfigPanel } from "@/components/backtest/ConfigPanel"
import { BacktestResultPanel } from "@/components/backtest/BacktestResultPanel"
import { OptimizeTab } from "@/components/backtest/OptimizeTab"
import { MonteCarloTab } from "@/components/backtest/MonteCarloTab"
import { HyperoptTab } from "@/components/backtest/HyperoptTab"
import { WalkForwardTab } from "@/components/backtest/WalkForwardTab"
import { BiasCheckTab } from "@/components/backtest/BiasCheckTab"
import { RobustnessTab } from "@/components/backtest/RobustnessTab"
import { MARKET_CFGS, today, yearsAgo } from "@/components/backtest/config"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import type { BacktestResult, BacktestRequest, Market } from "@/types"

// ── Tab 类型 ──────────────────────────────────────────────────
type MainTab = "backtest" | "optimize" | "hyperopt" | "walkforward" | "biascheck" | "robustness" | "montecarlo"

// ── 主页面 ────────────────────────────────────────────────────
export function Backtest() {
  const [searchParams] = useSearchParams()
  const { data: strategies } = useStrategies()
  const { mutate: runBacktest, isPending, error } = useRunBacktest()
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [activeTab, setActiveTab] = useState<MainTab>("backtest")

  const [form, setForm] = useState<BacktestRequest>(() => {
    const mkt = (searchParams.get("market") as Market) ?? "US"
    const cfg = MARKET_CFGS.find((c) => c.value === mkt) ?? MARKET_CFGS[0]
    let params: Record<string, unknown> = {}
    try { params = JSON.parse(searchParams.get("params") ?? "{}") } catch {}
    return {
      strategy_name: searchParams.get("strategy") ?? "double_ma",
      symbol:        searchParams.get("symbol")   ?? "AAPL",
      market:        mkt,
      frequency:     cfg.defaultFreq,
      start_date:    yearsAgo(2),
      end_date:      today(),
      initial_cash:  100_000,
      params,
    }
  })

  useEffect(() => {
    const s   = searchParams.get("strategy")
    const sym = searchParams.get("symbol")
    const mkt = searchParams.get("market") as Market | null
    let params: Record<string, unknown> = {}
    try { params = JSON.parse(searchParams.get("params") ?? "{}") } catch {}

    if (s || sym || mkt) {
      setForm((prev) => ({
        ...prev,
        ...(s   ? { strategy_name: s } : {}),
        ...(sym ? { symbol: sym }       : {}),
        ...(mkt ? { market: mkt }       : {}),
        ...(Object.keys(params).length ? { params } : {}),
      }))
    }
  }, [searchParams])

  function updateForm<K extends keyof BacktestRequest>(key: K, val: BacktestRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: val }))
  }

  function handleRun(e: React.FormEvent) {
    e.preventDefault()
    runBacktest(form, { onSuccess: (data) => setResult(data) })
  }

  const stratList = strategies ?? []

  const TABS: { key: MainTab; label: string }[] = [
    { key: "backtest", label: "📊 策略回测" },
    { key: "optimize", label: "🔍 参数优化" },
    { key: "hyperopt", label: "🎯 Hyperopt" },
    { key: "walkforward", label: "🔁 Walk-Forward" },
    { key: "biascheck", label: "🔬 偏差检测" },
    { key: "robustness", label: "🎰 稳健性" },
    { key: "montecarlo", label: "🎲 蒙特卡洛" },
  ]

  return (
    <AppShell title="回测" help={PAGE_HELP.backtest}>
      {/* 主 Tab */}
      <div className="flex gap-1 mb-5 border-b border-[#21262d]">
        {TABS.map(({ key, label }) => (
          <button key={key}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              activeTab === key
                ? "border-[#58a6ff] text-[#58a6ff]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
            onClick={() => setActiveTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {/* 策略回测 */}
      {activeTab === "backtest" && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          {/* 配置 */}
          <div className="xl:col-span-1">
            <ConfigPanel
              form={form} strategies={stratList} isLoading={isPending} error={error}
              onChange={updateForm} onSubmit={handleRun}
            />
          </div>

          {/* 结果 */}
          <div className="xl:col-span-2">
            {isPending && (
              <div className="card flex items-center justify-center h-48">
                <div className="text-center">
                  <Spinner size="lg" className="mx-auto mb-3" />
                  <p className="text-[#8b949e] text-sm">回测运行中…</p>
                </div>
              </div>
            )}
            {!isPending && !result && (
              <div className="card">
                <EmptyState
                  title="配置策略参数后点击开始回测"
                  description="支持美股/港股/A股，含回撤分析、月度收益热力图、蒙特卡洛验证"
                />
              </div>
            )}
            {result && !isPending && <BacktestResultPanel result={result} form={form} />}
          </div>
        </div>
      )}

      {/* 参数优化 */}
      {activeTab === "optimize" && <OptimizeTab strategies={stratList} />}

      {/* Hyperopt 参数优化 */}
      {activeTab === "hyperopt" && <HyperoptTab strategies={stratList} />}

      {/* Walk-Forward 分析 */}
      {activeTab === "walkforward" && <WalkForwardTab strategies={stratList} />}

      {/* 前视/递归偏差检测 */}
      {activeTab === "biascheck" && <BiasCheckTab strategies={stratList} />}

      {/* 稳健性：蒙特卡洛稳健性 + 统计显著性 */}
      {activeTab === "robustness" && <RobustnessTab strategies={stratList} />}

      {/* 蒙特卡洛 */}
      {activeTab === "montecarlo" && <MonteCarloTab strategies={stratList} />}
    </AppShell>
  )
}
