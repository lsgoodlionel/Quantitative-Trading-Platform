// AI 个股研报 Tab（V3 Wave C-a / I4）
//
// 分节卡片 + 底部固定的 sources 与免责声明。研报不缓存 —— 依赖实时行情与新闻，
// 所以这里也不做「上次结果」的持久化，切标的即清空。
import { useEffect, useState } from "react"
import { AiSetupNotice } from "@/components/ai/AiSetupNotice"
import { ReportSources } from "@/components/ai/ReportSources"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import { useStockReport } from "@/hooks/useAiReports"
import type { Market } from "@/types"

/** 可选的回溯区间。与后端 MIN/MAX_LOOKBACK_DAYS 一致。 */
const LOOKBACK_OPTIONS: { value: number; label: string }[] = [
  { value: 90, label: "近 3 个月" },
  { value: 180, label: "近 6 个月" },
  { value: 365, label: "近 1 年" },
]

const DEFAULT_LOOKBACK = 180

interface AiReportTabProps {
  initialSymbol: string
  initialMarket: Market
}

export function AiReportTab({ initialSymbol, initialMarket }: AiReportTabProps) {
  const [symbol, setSymbol] = useState(initialSymbol)
  const [lookback, setLookback] = useState(DEFAULT_LOOKBACK)
  const report = useStockReport()
  const { reset } = report

  // 左栏换标的时旧研报必须消失：一份写着 AAPL 的报告挂在 TSLA 下面是危险的
  useEffect(() => {
    setSymbol(initialSymbol)
    reset()
  }, [initialSymbol, initialMarket, reset])

  function handleGenerate() {
    const trimmed = symbol.toUpperCase().trim()
    if (!trimmed) return
    report.mutate({ symbol: trimmed, market: initialMarket, lookback_days: lookback })
  }

  const data = report.data

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[#8b949e]">标的（{initialMarket}）</span>
            <input
              className="input w-32 font-mono"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              aria-label="研报标的"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[#8b949e]">回溯区间</span>
            <select
              className="input w-32"
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              aria-label="回溯区间"
            >
              {LOOKBACK_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary text-xs"
            disabled={report.isPending}
            onClick={handleGenerate}
          >
            {report.isPending ? "生成中…" : "🤖 生成 AI 研报"}
          </button>
        </div>
        <p className="text-[11px] text-[#6e7681] mt-3 leading-relaxed">
          研报由大模型基于平台既有的行情、技术指标、新闻、财报日历与期权隐含波动率汇总生成，
          不含任何买卖建议。底部会列出实际喂给模型的每一条新闻，可逐条核对。
        </p>
      </div>

      <AiSetupNotice error={report.error} />

      {report.isPending && (
        <div className="card flex items-center justify-center h-48">
          <div className="text-center">
            <Spinner size="lg" className="mx-auto mb-3" />
            <p className="text-[#8b949e] text-sm">正在汇总数据并生成研报…</p>
          </div>
        </div>
      )}

      {!report.isPending && !data && !report.error && (
        <div className="card">
          <EmptyState
            title="尚未生成研报"
            description="选好标的与回溯区间后点击「生成 AI 研报」"
          />
        </div>
      )}

      {data && !report.isPending && (
        <>
          <div className="card">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="text-lg font-semibold text-[#e6edf3] font-mono">
                {data.symbol}
              </span>
              <span className="text-xs text-[#8b949e]">{data.market}</span>
              <span className="text-[10px] text-[#6e7681] font-mono ml-auto">
                {data.provider_id} / {data.model}
              </span>
            </div>
          </div>

          {data.section_titles.map(({ key, title }) => (
            <section key={key} className="card">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-2">{title}</h3>
              <p className="text-xs text-[#c9d1d9] leading-relaxed whitespace-pre-line">
                {data.sections[key] ?? "—"}
              </p>
            </section>
          ))}

          <ReportSources
            sources={data.sources}
            dataNotes={data.data_notes}
            disclaimer={data.disclaimer}
            model={data.model}
            generatedAt={data.generated_at}
          />
        </>
      )}
    </div>
  )
}
