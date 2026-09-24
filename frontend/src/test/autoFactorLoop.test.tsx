import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

import {
  formatDecay,
  formatMetric,
  hasPendingRound,
  isPending,
  type LoopResult,
  type LoopSurvivor,
} from "@/hooks/useAutoFactorLoop"
import { AutoLoopRoundView } from "@/pages/lab/AutoLoopRoundView"

const HEALTHY: LoopSurvivor = {
  expr: "DIV(MOM20, ATR_RATIO)",
  tokens: ["MOM20", "ATR_RATIO", "DIV"],
  is_fitness: 1.2,
  is_ic_mean: 0.082,
  is_rank_ic_mean: 0.075,
  is_icir: 0.9,
  is_n_dates: 120,
  oos_fitness: 1.0,
  oos_ic_mean: 0.071,
  oos_rank_ic_mean: 0.066,
  oos_icir: 0.8,
  oos_n_dates: 60,
  ic_decay_ratio: 0.866,
  overfit_suspect: false,
  out_of_sample_evaluated: true,
}

const SUSPECT: LoopSurvivor = {
  ...HEALTHY,
  expr: "ZSCORE(MUL(RSI14, VOL_CHG))",
  tokens: ["RSI14", "VOL_CHG", "MUL", "ZSCORE"],
  is_ic_mean: 0.14,
  oos_ic_mean: 0.012,
  ic_decay_ratio: 0.0857,
  overfit_suspect: true,
}

const RESULT: LoopResult = {
  round_id: "abc123",
  seeds: ["MOM5"],
  survivors: [HEALTHY, SUSPECT],
  hypotheses_tested: 1873,
  multiple_testing_note:
    "本轮共评估了 1873 个不同表达式，下面这几条是从中挑出的最好的几条。评估的表达式越多，最好的那几条指标越亮眼 —— 这是多重检验的必然结果，不是发现。",
  llm_review: "两条因子都在吃短期动量，结构高度相似。",
  llm_error: null,
  next_seeds: ["SLOPE10(MOM20)"],
  rejected_seed_count: 2,
  invalid_seed_count: 0,
  truncated: false,
  out_of_sample_available: true,
  is_end: "2024-06-30",
  embargo_bars: 5,
  universe: ["AAPL", "MSFT", "NVDA"],
  artifact_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  artifact_error: null,
}

describe("formatMetric / formatDecay", () => {
  it("shows n/a instead of faking a zero when a metric is missing", () => {
    // 用 0 冒充「没算出来」是这类界面最常见的骗人方式
    expect(formatMetric(null)).toBe("n/a")
    expect(formatMetric(undefined)).toBe("n/a")
    expect(formatDecay(null)).toBe("n/a")
  })

  it("formats metrics and decay percentages", () => {
    expect(formatMetric(0.08234)).toBe("0.0823")
    expect(formatDecay(0.866)).toBe("87%")
  })
})

describe("isPending / hasPendingRound", () => {
  it("treats queued and running as pending", () => {
    expect(isPending("queued")).toBe(true)
    expect(isPending("running")).toBe(true)
    expect(isPending("done")).toBe(false)
    expect(isPending("error")).toBe(false)
    expect(isPending(undefined)).toBe(false)
  })

  it("stops polling once every round has settled", () => {
    const base = {
      created_at: 0,
      updated_at: 0,
      request: {},
      result: null,
      error: null,
    }
    expect(
      hasPendingRound({
        total: 1,
        items: [{ round_id: "a", status: "done", ...base }],
      }),
    ).toBe(false)
    expect(
      hasPendingRound({
        total: 2,
        items: [
          { round_id: "a", status: "done", ...base },
          { round_id: "b", status: "running", ...base },
        ],
      }),
    ).toBe(true)
  })
})

describe("AutoLoopRoundView", () => {
  it("shows in-sample and out-of-sample IC side by side", () => {
    render(<AutoLoopRoundView result={RESULT} />)

    // 两套指标必须同时可见，不能被合并成一个「综合分」
    expect(screen.getByText("0.0820")).toBeTruthy()   // IS IC
    expect(screen.getByText("0.0710")).toBeTruthy()   // OOS IC
    expect(screen.getByText("样本内 IC")).toBeTruthy()
    expect(screen.getByText("样本外 IC")).toBeTruthy()
  })

  it("shows hypotheses_tested together with the caveat about what it means", () => {
    render(<AutoLoopRoundView result={RESULT} />)

    expect(screen.getByText("1,873")).toBeTruthy()
    expect(screen.getByText("检验了多少个假设")).toBeTruthy()
    // 说明文字用后端给的原文，前端不另编一套措辞
    expect(screen.getByText(RESULT.multiple_testing_note)).toBeTruthy()
  })

  it("flags the overfit suspect but keeps it in the table", () => {
    render(<AutoLoopRoundView result={RESULT} />)

    expect(screen.getByText("过拟合嫌疑")).toBeTruthy()
    // 被标注的因子照样列出来 —— 标注不是丢弃的理由
    expect(screen.getByText(SUSPECT.expr)).toBeTruthy()
    expect(screen.getByText(HEALTHY.expr)).toBeTruthy()
  })

  it("says the loop never auto-deploys", () => {
    render(<AutoLoopRoundView result={RESULT} />)
    expect(screen.getByText(/不会自动注册为策略/)).toBeTruthy()
  })

  it("warns loudly when there is no out-of-sample data at all", () => {
    render(
      <AutoLoopRoundView
        result={{ ...RESULT, out_of_sample_available: false, survivors: [] }}
      />,
    )
    expect(screen.getByText(/没有样本外数据/)).toBeTruthy()
  })

  it("reports a missing out-of-sample metric as 无数据, not as a number", () => {
    render(
      <AutoLoopRoundView
        result={{
          ...RESULT,
          survivors: [
            {
              ...HEALTHY,
              out_of_sample_evaluated: false,
              oos_ic_mean: null,
              oos_rank_ic_mean: null,
              ic_decay_ratio: null,
            },
          ],
        }}
      />,
    )
    expect(screen.getAllByText("无数据").length).toBeGreaterThan(0)
  })

  it("surfaces the truncation flag when the evaluation budget ran out", () => {
    render(<AutoLoopRoundView result={{ ...RESULT, truncated: true }} />)
    expect(screen.getByText(/已达评估上限/)).toBeTruthy()
  })

  it("says plainly when there is no model review", () => {
    render(<AutoLoopRoundView result={{ ...RESULT, llm_review: null }} />)
    expect(screen.getByText(/未配置模型服务，不影响搜索结果/)).toBeTruthy()
  })

  it("surfaces an artifact persistence failure instead of pretending it succeeded", () => {
    render(
      <AutoLoopRoundView
        result={{ ...RESULT, artifact_id: null, artifact_error: "disk full" }}
      />,
    )
    expect(screen.getByText(/结果未能写入产物库：disk full/)).toBeTruthy()
  })
})
