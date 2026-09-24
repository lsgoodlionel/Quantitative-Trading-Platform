/**
 * AI 研报 / 回测诊断前端测试（V3 Wave C-a / I4+I5）
 *
 * 对应契约 docs/contracts/waveCa-ai-reports.md §五 验收 4：
 * 分节卡片、sources 与 disclaimer 固定展示。
 *
 * 外加两条本 Wave 的核心断言：
 * - 501（还没配模型）展示引导块而不是红色报错
 * - AI 解读与规则判据矛盾时，界面必须挑明冲突而不是照单全收
 */
import { describe, expect, it, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return { ...actual, api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }
})

import { api, ApiError } from "@/lib/api"
import { AiSetupNotice } from "@/components/ai/AiSetupNotice"
import { ReportSources } from "@/components/ai/ReportSources"
import { BacktestDiagnosisPanel } from "@/components/backtest/BacktestDiagnosisPanel"
import { AiReportTab } from "@/pages/market/AiReportTab"
import { formatStamp, isSetupNeeded, type StockReport } from "@/hooks/useAiReports"
import type { FullValidationResult } from "@/hooks/useFullValidation"

const REPORT: StockReport = {
  symbol: "AAPL",
  market: "US",
  sections: {
    overview: "半年区间震荡。",
    technical: "RSI 处于中性区间。",
    news: "消息面以产品迭代为主。",
    risks: "波动率抬升。",
    watchpoints: "关注下一次财报。",
  },
  section_titles: [
    { key: "overview", title: "概览" },
    { key: "technical", title: "技术面" },
    { key: "news", title: "消息面" },
    { key: "risks", title: "风险提示" },
    { key: "watchpoints", title: "关注要点" },
  ],
  sources: [
    {
      title: "Apple 发布季度财报",
      published_at: "2026-08-10T12:00:00+00:00",
      publisher: "Reuters",
      url: "https://example.test/a",
    },
  ],
  data_notes: ["期权隐含波动率获取失败：timeout"],
  technicals: { last_close: 190.5 },
  generated_at: "2026-08-14T08:00:00+00:00",
  model: "qwen2.5:14b",
  provider_id: "ollama",
  disclaimer: "本内容由大模型自动汇总生成，不构成任何投资建议。",
}

const VALIDATION: FullValidationResult = {
  run_id: "run-abc",
  requested_steps: ["backtest", "optimize"],
  steps: { optimize: { score_dispersion: 0.9 } },
  grade: {
    score: 85,
    level: "B",
    level_label: "触发轻度检查项",
    completed_steps: ["backtest", "optimize"],
    failed_steps: [],
    skipped_steps: ["walkforward", "bias", "robustness"],
    findings: [
      {
        step: "optimize",
        rule: "param_sensitivity",
        metric: "score_dispersion",
        value: 0.9,
        threshold: 0.5,
        penalty: 15,
        detail: "有效参数区间狭窄。",
      },
    ],
    not_evaluated: [],
    based_on: "基于 2/5 步",
    is_complete: false,
    disclaimer: "规则化启发式汇总。",
  },
}

const DIAGNOSIS = {
  run_id: "run-abc",
  grade_level: "B",
  grade_score: 85,
  coverage: "基于 2/5 步",
  is_complete: false,
  summary: "（基于 2/5 步）参数有效区间狭窄，需要收敛参数空间后重跑。",
  findings: [
    {
      title: "参数敏感性过高",
      detail: "换一组邻近参数结果可能大幅劣化。",
      severity: "high",
      rule: "param_sensitivity",
    },
  ],
  next_steps: ["缩小参数空间后重跑寻优"],
  contradictions: [],
  has_contradiction: false,
  generated_at: "2026-08-14T08:00:00+00:00",
  model: "qwen2.5:14b",
  provider_id: "ollama",
  disclaimer: "不构成任何投资建议。",
}

function renderWithProviders(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.post).mockReset()
})

// ── 展示辅助 ─────────────────────────────────────────────────────

describe("isSetupNeeded", () => {
  it("treats 501 as 'no model configured' rather than a failure", () => {
    expect(isSetupNeeded(new ApiError(501, "尚未配置"))).toBe(true)
  })

  it("does not treat a dead provider (503) as a setup problem", () => {
    expect(isSetupNeeded(new ApiError(503, "connection refused"))).toBe(false)
    expect(isSetupNeeded(new Error("boom"))).toBe(false)
  })
})

describe("formatStamp", () => {
  it("returns a readable stamp", () => {
    expect(formatStamp("2026-08-14T08:00:00Z")).toMatch(/^2026-08-14 /)
  })

  it("falls back to the raw value when it is not a date", () => {
    expect(formatStamp("不是时间")).toBe("不是时间")
    expect(formatStamp(null)).toBe("时间未知")
  })
})

describe("AiSetupNotice", () => {
  it("offers a link to the models page when nothing is configured", () => {
    renderWithProviders(<AiSetupNotice error={new ApiError(501, "尚未配置任何可用的模型服务")} />)

    expect(screen.getByText("还没有可用的模型服务")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute(
      "href", "/settings/models",
    )
  })

  it("shows a plain error for other failures", () => {
    renderWithProviders(<AiSetupNotice error={new ApiError(503, "connection refused")} />)

    expect(screen.queryByRole("link", { name: "去配置" })).toBeNull()
    expect(screen.getByText("connection refused")).toBeInTheDocument()
  })

  it("renders nothing without an error", () => {
    const { container } = renderWithProviders(<AiSetupNotice error={null} />)
    expect(container.textContent).toBe("")
  })
})

// ── 验收 4：sources 与 disclaimer 固定展示 ───────────────────────

describe("ReportSources", () => {
  it("lists the news actually fed to the model, with a link back to the original", () => {
    renderWithProviders(
      <ReportSources
        sources={REPORT.sources}
        dataNotes={REPORT.data_notes}
        disclaimer={REPORT.disclaimer}
        model={REPORT.model}
        generatedAt={REPORT.generated_at}
      />,
    )

    expect(screen.getByText("依据的新闻（1 条）")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Apple 发布季度财报" })).toHaveAttribute(
      "href", "https://example.test/a",
    )
    expect(screen.getByText(/Reuters/)).toBeInTheDocument()
  })

  it("always shows the disclaimer and the data gaps", () => {
    renderWithProviders(
      <ReportSources
        sources={[]}
        dataNotes={["期权隐含波动率获取失败：timeout"]}
        disclaimer={REPORT.disclaimer}
        model="m"
        generatedAt={REPORT.generated_at}
      />,
    )

    expect(screen.getByText(REPORT.disclaimer)).toBeInTheDocument()
    expect(screen.getByText(/期权隐含波动率获取失败/)).toBeInTheDocument()
    expect(screen.getByText(/本期无可用新闻/)).toBeInTheDocument()
  })
})

describe("AiReportTab", () => {
  it("renders every section as its own card plus sources and disclaimer", async () => {
    vi.mocked(api.post).mockResolvedValue(REPORT)
    renderWithProviders(<AiReportTab initialSymbol="AAPL" initialMarket="US" />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 研报/ }))

    await waitFor(() => expect(screen.getByText("概览")).toBeInTheDocument())
    for (const { title } of REPORT.section_titles) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
    expect(screen.getByText(REPORT.sections.overview)).toBeInTheDocument()
    expect(screen.getByText("依据的新闻（1 条）")).toBeInTheDocument()
    expect(screen.getByText(REPORT.disclaimer)).toBeInTheDocument()
  })

  it("sends the selected symbol, market and lookback window", async () => {
    vi.mocked(api.post).mockResolvedValue(REPORT)
    renderWithProviders(<AiReportTab initialSymbol="TSLA" initialMarket="US" />)

    fireEvent.change(screen.getByLabelText("回溯区间"), { target: { value: "365" } })
    fireEvent.click(screen.getByRole("button", { name: /生成 AI 研报/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/api/v1/ai/reports/stock",
      { symbol: "TSLA", market: "US", lookback_days: 365 },
    ))
  })

  it("guides the user to the models page when nothing is configured", async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(501, "尚未配置任何可用的模型服务"))
    renderWithProviders(<AiReportTab initialSymbol="AAPL" initialMarket="US" />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 研报/ }))

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "去配置" })).toBeInTheDocument(),
    )
  })

  it("shows an empty state before anything is generated", () => {
    renderWithProviders(<AiReportTab initialSymbol="AAPL" initialMarket="US" />)
    expect(screen.getByText("尚未生成研报")).toBeInTheDocument()
  })
})

// ── I5 回测 AI 诊断 ──────────────────────────────────────────────

describe("BacktestDiagnosisPanel", () => {
  it("posts the validation result itself, not just a run_id", async () => {
    vi.mocked(api.post).mockResolvedValue(DIAGNOSIS)
    renderWithProviders(<BacktestDiagnosisPanel result={VALIDATION} />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 诊断/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/api/v1/ai/reports/backtest",
      { run_id: "run-abc", grade: VALIDATION.grade, steps: VALIDATION.steps },
    ))
  })

  it("shows the coverage, findings and next steps", async () => {
    vi.mocked(api.post).mockResolvedValue(DIAGNOSIS)
    renderWithProviders(<BacktestDiagnosisPanel result={VALIDATION} />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 诊断/ }))

    await waitFor(() => expect(screen.getByText(DIAGNOSIS.summary)).toBeInTheDocument())
    expect(screen.getAllByText("基于 2/5 步").length).toBeGreaterThan(0)
    expect(screen.getByText("参数敏感性过高")).toBeInTheDocument()
    expect(screen.getByText("缩小参数空间后重跑寻优")).toBeInTheDocument()
    expect(screen.getByText(/未覆盖全部 5 步/)).toBeInTheDocument()
  })

  it("calls out a contradiction instead of presenting it as a finding", async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...DIAGNOSIS,
      summary: "参数很稳健。",
      has_contradiction: true,
      contradictions: [
        {
          rule: "param_sensitivity",
          claim: "参数很稳健",
          detail: "规则判据已触发「param_sensitivity」，但 AI 诊断称参数很稳健，请以规则判据为准。",
        },
      ],
    })
    renderWithProviders(<BacktestDiagnosisPanel result={VALIDATION} />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 诊断/ }))

    await waitFor(() =>
      expect(screen.getByText(/AI 解读与规则判据存在冲突/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/规则判据已触发/)).toBeInTheDocument()
  })

  it("shows the setup guidance when no model is configured", async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(501, "尚未配置任何可用的模型服务"))
    renderWithProviders(<BacktestDiagnosisPanel result={VALIDATION} />)

    fireEvent.click(screen.getByRole("button", { name: /生成 AI 诊断/ }))

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "去配置" })).toBeInTheDocument(),
    )
  })
})
