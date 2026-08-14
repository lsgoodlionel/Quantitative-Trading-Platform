import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useRunBacktest, useStrategies } from "@/hooks/useBacktest"
import { useFullValidation, VALIDATION_STEPS, type ValidationStep } from "@/hooks/useFullValidation"
import { BacktestDiagnosisPanel } from "@/components/backtest/BacktestDiagnosisPanel"
import { BacktestResultPanel } from "@/components/backtest/BacktestResultPanel"
import { FullValidationPanel } from "@/components/backtest/FullValidationPanel"
import { HistoryTab } from "@/components/backtest/HistoryTab"
import { OptimizeCombinedTab } from "@/components/backtest/OptimizeCombinedTab"
import { RobustnessTab } from "@/components/backtest/RobustnessTab"
import { SaveResultButton } from "@/components/backtest/SaveResultButton"
import { SharedConfigProvider, toRequestBase } from "@/components/backtest/SharedConfig"
import { SharedConfigHeader } from "@/components/backtest/SharedConfigHeader"
import { ValidationCombinedTab } from "@/components/backtest/ValidationCombinedTab"
import { MARKET_CFGS, today, yearsAgo } from "@/components/backtest/config"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import type { BacktestRequest, BacktestResult, Market } from "@/types"

// ── Tab 类型（V3 · H2：7 个 Tab 收敛到 4 个 + 历史）─────────────
type MainTab = "backtest" | "optimize" | "validation" | "robustness" | "history"

const TABS: { key: MainTab; label: string }[] = [
  { key: "backtest", label: "📊 回测 / 完整验证" },
  { key: "optimize", label: "🔍 参数寻优" },
  { key: "validation", label: "🔁 样本外验证" },
  { key: "robustness", label: "🎰 稳健性" },
  { key: "history", label: "🗂 历史" },
]

function parseParams(raw: string | null): Record<string, unknown> {
  try {
    return JSON.parse(raw ?? "{}") as Record<string, unknown>
  } catch {
    // URL 参数畸形（用户手改/截断）：退回空参数，用策略默认值
    return {}
  }
}

// ── 主页面 ────────────────────────────────────────────────────
export function Backtest() {
  const [searchParams] = useSearchParams()
  const { data: strategies } = useStrategies()
  const { mutate: runBacktest, isPending, error } = useRunBacktest()
  const fullValidation = useFullValidation()
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [activeTab, setActiveTab] = useState<MainTab>("backtest")
  const [selectedSteps, setSelectedSteps] = useState<ValidationStep[]>([...VALIDATION_STEPS])

  const [form, setForm] = useState<BacktestRequest>(() => {
    const mkt = (searchParams.get("market") as Market) ?? "US"
    const cfg = MARKET_CFGS.find((c) => c.value === mkt) ?? MARKET_CFGS[0]
    return {
      strategy_name: searchParams.get("strategy") ?? "double_ma",
      symbol: searchParams.get("symbol") ?? "AAPL",
      market: mkt,
      frequency: cfg.defaultFreq,
      start_date: yearsAgo(2),
      end_date: today(),
      initial_cash: 100_000,
      params: parseParams(searchParams.get("params")),
    }
  })

  useEffect(() => {
    const s = searchParams.get("strategy")
    const sym = searchParams.get("symbol")
    const mkt = searchParams.get("market") as Market | null
    const params = parseParams(searchParams.get("params"))

    if (s || sym || mkt) {
      setForm((prev) => ({
        ...prev,
        ...(s ? { strategy_name: s } : {}),
        ...(sym ? { symbol: sym } : {}),
        ...(mkt ? { market: mkt } : {}),
        ...(Object.keys(params).length ? { params } : {}),
      }))
    }
  }, [searchParams])

  const updateForm = useCallback(
    <K extends keyof BacktestRequest>(key: K, val: BacktestRequest[K]) => {
      setForm((prev) => ({ ...prev, [key]: val }))
    },
    [],
  )

  function handleRunBacktest() {
    setActiveTab("backtest")
    runBacktest(form, { onSuccess: (data) => setResult(data) })
  }

  function handleRunFullValidation() {
    setActiveTab("backtest")
    fullValidation.mutate({ ...toRequestBase(form), params: form.params, steps: selectedSteps })
  }

  function toggleStep(step: ValidationStep) {
    setSelectedSteps((prev) =>
      prev.includes(step) ? prev.filter((s) => s !== step) : [...prev, step],
    )
  }

  return (
    <AppShell title="回测" help={PAGE_HELP.backtest}>
      <SharedConfigProvider config={form} update={updateForm} strategies={strategies ?? []}>
        <SharedConfigHeader
          onRunBacktest={handleRunBacktest}
          onRunFullValidation={handleRunFullValidation}
          isBacktestPending={isPending}
          isValidationPending={fullValidation.isPending}
        />

        <div className="flex gap-1 mb-5 border-b border-[#21262d]">
          {TABS.map(({ key, label }) => (
            <button key={key} type="button"
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

        {activeTab === "backtest" && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <div className="xl:col-span-2 space-y-4">
              {isPending && (
                <div className="card flex items-center justify-center h-48">
                  <div className="text-center">
                    <Spinner size="lg" className="mx-auto mb-3" />
                    <p className="text-[#8b949e] text-sm">回测运行中…</p>
                  </div>
                </div>
              )}
              {error && (
                <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
                  {error.message}
                </p>
              )}
              {!isPending && !result && (
                <div className="card">
                  <EmptyState
                    title="点击顶部「▶ 运行回测」开始"
                    description="支持美股/港股/A股，含回撤分析、月度收益热力图、蒙特卡洛验证"
                  />
                </div>
              )}
              {result && !isPending && (
                <>
                  <SaveResultButton result={result} form={form} />
                  <BacktestResultPanel result={result} form={form} />
                </>
              )}
            </div>

            <div className="xl:col-span-1">
              <FullValidationPanel
                result={fullValidation.data ?? null}
                isPending={fullValidation.isPending}
                error={fullValidation.error}
                selectedSteps={selectedSteps}
                onToggleStep={toggleStep}
              />
              {/* AI 诊断挂在完整验证结果之后：先给可审计的规则判据，
                  再给人话翻译 —— AI 解读评级，不取代评级（V3 Wave C-a / I5） */}
              {fullValidation.data && !fullValidation.isPending && (
                <div className="mt-4">
                  <BacktestDiagnosisPanel result={fullValidation.data} />
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "optimize" && <OptimizeCombinedTab />}
        {activeTab === "validation" && <ValidationCombinedTab />}
        {activeTab === "robustness" && <RobustnessTab />}
        {activeTab === "history" && <HistoryTab />}
      </SharedConfigProvider>
    </AppShell>
  )
}
