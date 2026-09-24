import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { FullValidationPanel } from "@/components/backtest/FullValidationPanel"
import {
  SharedConfigProvider,
  toRequestBase,
  type SharedBacktestConfig,
} from "@/components/backtest/SharedConfig"
import { SharedConfigHeader } from "@/components/backtest/SharedConfigHeader"
import { SharedConfigNotice } from "@/components/backtest/SharedConfigNotice"
import { VALIDATION_STEPS, type FullValidationResult } from "@/hooks/useFullValidation"

const CONFIG: SharedBacktestConfig = {
  strategy_name: "double_ma",
  symbol: "AAPL",
  market: "US",
  frequency: "1d",
  start_date: "2023-01-01",
  end_date: "2023-12-31",
  initial_cash: 100_000,
  params: { fast_period: 10 },
}

function renderWithConfig(node: React.ReactNode, update = vi.fn()) {
  return render(
    <SharedConfigProvider config={CONFIG} update={update} strategies={[{ name: "double_ma", description: "双均线" }]}>
      {node}
    </SharedConfigProvider>,
  )
}

// ── 共享配置 ─────────────────────────────────────────────────────

describe("toRequestBase", () => {
  it("returns the seven fields shared by every validation endpoint", () => {
    expect(toRequestBase(CONFIG)).toEqual({
      strategy_name: "double_ma",
      symbol: "AAPL",
      market: "US",
      frequency: "1d",
      start_date: "2023-01-01",
      end_date: "2023-12-31",
      initial_cash: 100_000,
    })
  })

  it("omits params so endpoints that reject it are not broken", () => {
    expect(toRequestBase(CONFIG)).not.toHaveProperty("params")
  })
})

describe("SharedConfigNotice", () => {
  it("shows the config every tab is running against", () => {
    renderWithConfig(<SharedConfigNotice />)
    expect(screen.getByText(/double_ma · AAPL\(US\) · 1d · 2023-01-01~2023-12-31/)).toBeInTheDocument()
  })
})

describe("SharedConfigHeader", () => {
  const noop = () => {}

  it("renders the shared fields from context", () => {
    renderWithConfig(
      <SharedConfigHeader onRunBacktest={noop} onRunFullValidation={noop}
        isBacktestPending={false} isValidationPending={false} />,
    )
    expect(screen.getByLabelText("标的")).toHaveValue("AAPL")
    expect(screen.getByLabelText("初始资金")).toHaveValue(100_000)
  })

  it("propagates a symbol edit through the shared update callback", () => {
    const update = vi.fn()
    renderWithConfig(
      <SharedConfigHeader onRunBacktest={noop} onRunFullValidation={noop}
        isBacktestPending={false} isValidationPending={false} />,
      update,
    )
    fireEvent.change(screen.getByLabelText("标的"), { target: { value: "msft" } })
    expect(update).toHaveBeenCalledWith("symbol", "MSFT")
  })

  it("resets frequency to the market default when the market changes", () => {
    const update = vi.fn()
    renderWithConfig(
      <SharedConfigHeader onRunBacktest={noop} onRunFullValidation={noop}
        isBacktestPending={false} isValidationPending={false} />,
      update,
    )
    fireEvent.change(screen.getByLabelText("市场"), { target: { value: "A" } })
    expect(update).toHaveBeenCalledWith("market", "A")
    expect(update).toHaveBeenCalledWith("frequency", "1d")
  })

  it("triggers the full validation action", () => {
    const onRunFullValidation = vi.fn()
    renderWithConfig(
      <SharedConfigHeader onRunBacktest={noop} onRunFullValidation={onRunFullValidation}
        isBacktestPending={false} isValidationPending={false} />,
    )
    fireEvent.click(screen.getByRole("button", { name: "⚡ 完整验证" }))
    expect(onRunFullValidation).toHaveBeenCalledOnce()
  })

  it("disables both actions while a run is in flight", () => {
    renderWithConfig(
      <SharedConfigHeader onRunBacktest={noop} onRunFullValidation={noop}
        isBacktestPending isValidationPending={false} />,
    )
    screen.getAllByRole("button").slice(0, 2).forEach((btn) => expect(btn).toBeDisabled())
  })
})

// ── 完整验证报告 ─────────────────────────────────────────────────

function makeResult(overrides: Partial<FullValidationResult> = {}): FullValidationResult {
  return {
    run_id: "abc123",
    requested_steps: ["backtest", "bias"],
    steps: {
      backtest: { metrics: { sharpe_ratio: 1.2 } },
      bias: { error: "偏差检测引擎错误" },
    },
    grade: {
      score: 90,
      level: "A",
      level_label: "未触发任何检查项",
      completed_steps: ["backtest"],
      failed_steps: ["bias"],
      skipped_steps: ["optimize", "walkforward", "robustness"],
      findings: [],
      not_evaluated: [{ step: "bias", rule: "bias_detected", reason: "该步骤执行失败" }],
      based_on: "基于 1/5 步",
      is_complete: false,
      disclaimer: "本评级为规则化启发式汇总，不构成任何交易或实盘建议。",
    },
    ...overrides,
  }
}

describe("FullValidationPanel", () => {
  const noop = () => {}

  it("shows coverage and warns when not all five steps ran", () => {
    render(
      <FullValidationPanel result={makeResult()} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText("基于 1/5 步")).toBeInTheDocument()
    expect(screen.getByText(/本次未覆盖全部 5 步/)).toBeInTheDocument()
  })

  it("surfaces the per-step error instead of hiding it", () => {
    render(
      <FullValidationPanel result={makeResult()} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText("偏差检测引擎错误")).toBeInTheDocument()
    expect(screen.getByText("失败")).toBeInTheDocument()
  })

  it("lists rules that could not be evaluated", () => {
    render(
      <FullValidationPanel result={makeResult()} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText("bias_detected — 该步骤执行失败")).toBeInTheDocument()
  })

  it("always renders the disclaimer and never a go-live recommendation", () => {
    const { container } = render(
      <FullValidationPanel result={makeResult()} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText(/不构成任何交易或实盘建议/)).toBeInTheDocument()
    expect(container.textContent).not.toContain("建议实盘")
  })

  it("renders each finding with its threshold evidence", () => {
    const result = makeResult({
      grade: {
        ...makeResult().grade,
        score: 70,
        findings: [{
          step: "robustness", rule: "mc_p5_negative", metric: "p5_total_return_pct",
          value: -5, threshold: 0, penalty: 20, detail: "5% 分位为负",
        }],
      },
    })
    render(
      <FullValidationPanel result={result} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText("mc_p5_negative")).toBeInTheDocument()
    expect(screen.getByText("p5_total_return_pct = -5 · 阈值 0")).toBeInTheDocument()
    expect(screen.getByText("−20")).toBeInTheDocument()
  })

  it("toggles a step when its chip is clicked", () => {
    const onToggleStep = vi.fn()
    render(
      <FullValidationPanel result={null} isPending={false} error={null}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={onToggleStep} />,
    )
    fireEvent.click(screen.getByRole("button", { name: "偏差检测" }))
    expect(onToggleStep).toHaveBeenCalledWith("bias")
  })

  it("marks unselected steps as not pressed", () => {
    render(
      <FullValidationPanel result={null} isPending={false} error={null}
        selectedSteps={["backtest"]} onToggleStep={noop} />,
    )
    expect(screen.getByRole("button", { name: "策略回测" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "偏差检测" })).toHaveAttribute("aria-pressed", "false")
  })

  it("shows the error box when the request itself failed", () => {
    render(
      <FullValidationPanel result={null} isPending={false} error={new Error("数据不足")}
        selectedSteps={[...VALIDATION_STEPS]} onToggleStep={noop} />,
    )
    expect(screen.getByText("数据不足")).toBeInTheDocument()
  })
})
