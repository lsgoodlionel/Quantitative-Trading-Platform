// 组合优化 Tab（原 /portfolio-optimizer 整页）：均值-方差族优化 + Topk 轮动。
// 由 pages/PortfolioOptimizer.tsx 迁入持仓页的一个 Tab（V3 · H1），逻辑未改。
import { useState } from "react"
import { useLocation } from "react-router-dom"
import { Spinner } from "@/components/ui/Spinner"
import { useAdvancedPortfolioOptimize } from "@/hooks/usePortfolioAdvanced"
import { TopkDropoutPanel } from "@/pages/portfolio/TopkDropoutPanel"
import type { Market } from "@/types"
import { OptimizerForm, type FormState } from "./OptimizerForm"
import { OptimizerResult } from "./OptimizerResult"
import { MARKET_DEFAULTS, isBlackLitterman, today, yearsAgo } from "./options"
import { parseSymbols, readInitialSelection } from "./selection"

type OptimizerView = "optimize" | "topk"

const VIEW_TABS: { key: OptimizerView; label: string; desc: string }[] = [
  { key: "optimize", label: "组合优化", desc: "均值-方差 / 风险平价 / BL / CVaR" },
  { key: "topk", label: "Topk 轮动组合", desc: "打分 → 持 topK、控换手轮动" },
]

export function OptimizerTab() {
  const { search } = useLocation()
  const [view, setView] = useState<OptimizerView>("optimize")
  const { mutate: runOpt, isPending, data: result, error } = useAdvancedPortfolioOptimize()
  // 惰性初始化：只读一次 URL，之后表单完全由用户驱动
  const [initialSelection] = useState(() => readInitialSelection(search))

  const [form, setForm] = useState<FormState>({
    symbolsText: initialSelection.symbolsText,
    market: initialSelection.market,
    start_date: yearsAgo(3),
    end_date: today(),
    method: "max_sharpe",
    include_frontier: true,
    risk_model: "sample_cov",
    expected_returns_method: "mean_historical",
    views: [],
    linkage_method: "single",
    cvar_beta: 0.95,
  })

  const currentSymbols = parseSymbols(form.symbolsText)
  // 记录发起优化时的市场，供离散配置拉取最新价格（表单市场可能后续被改动）
  const [submittedMarket, setSubmittedMarket] = useState<Market>(initialSelection.market)

  function handleMarketChange(m: string) {
    const market = m as Market
    setForm((f) => ({
      ...f,
      market,
      symbolsText: (MARKET_DEFAULTS[market] ?? MARKET_DEFAULTS.US).join(", "),
    }))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const symbols = parseSymbols(form.symbolsText)

    if (symbols.length < 2) {
      alert("请输入至少 2 个标的代码")
      return
    }

    if (isBlackLitterman(form.method)) {
      const cleanViews = form.views.filter(
        (v) => v.assets.every((a) => a) &&
          (v.kind === "absolute" ? v.assets.length >= 1 : v.assets.length >= 2),
      )
      if (cleanViews.length === 0) {
        alert("Black-Litterman 需要至少 1 条完整观点（选择标的并填写收益）")
        return
      }
    }

    setSubmittedMarket(form.market)
    runOpt({
      symbols,
      market: form.market,
      start_date: form.start_date,
      end_date: form.end_date,
      method: form.method,
      include_frontier: form.include_frontier,
      risk_model: form.risk_model,
      expected_returns_method: form.expected_returns_method,
      views: isBlackLitterman(form.method) ? form.views : undefined,
      linkage_method: form.linkage_method,
      cvar_beta: form.cvar_beta,
    })
  }

  return (
    <div>
      {/* 模式切换：均值-方差优化 vs Topk 轮动组合 */}
      <div className="flex gap-1 mb-5 border-b border-[#30363d]">
        {VIEW_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setView(t.key)}
            title={t.desc}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              view === t.key
                ? "border-[#58a6ff] text-[#e6edf3]"
                : "border-transparent text-[#6e7681] hover:text-[#e6edf3]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {view === "topk" && <TopkDropoutPanel />}

      {view === "optimize" && (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
          <OptimizerForm
            form={form}
            setForm={setForm}
            onMarketChange={handleMarketChange}
            onSubmit={handleSubmit}
            isPending={isPending}
            symbols={currentSymbols}
          />

          {/* 右侧结果区 */}
          <div className="xl:col-span-3">
            {isPending && (
              <div className="card flex flex-col items-center justify-center py-20 gap-3">
                <Spinner size="lg" />
                <p className="text-[#8b949e] text-sm">正在下载历史数据并运行优化算法…</p>
              </div>
            )}

            {error && !isPending && (
              <div className="card border-[#f85149]/30">
                <p className="text-[#f85149] text-sm font-medium mb-1">优化失败</p>
                <p className="text-[#8b949e] text-xs">{error.message}</p>
              </div>
            )}

            {!isPending && !result && !error && (
              <div className="card flex flex-col items-center justify-center py-20 gap-3 border-dashed">
                <p className="text-4xl">📊</p>
                <p className="text-[#e6edf3] font-medium">配置标的并运行优化</p>
                <p className="text-[#8b949e] text-sm text-center max-w-sm">
                  支持均值-方差优化、风险平价、CVaR 最小化，并可视化有效前沿
                </p>
              </div>
            )}

            {result && !isPending && <OptimizerResult result={result} market={submittedMarket} />}
          </div>
        </div>
      )}
    </div>
  )
}
