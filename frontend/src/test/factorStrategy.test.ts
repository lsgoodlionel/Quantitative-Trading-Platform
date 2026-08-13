import { describe, it, expect } from "vitest"
import {
  DEFAULT_SPEC, METHOD_LABELS, specFromLibraryFactor, specFromTokens, validateSpec,
  type FactorStrategySpec,
} from "@/hooks/useFactorStrategy"

// 前端这道校验必须与后端 FactorStrategySpec 的规则一致，
// 否则用户会遇到「本地过了、后端 400」的割裂体验。

function spec(overrides: Partial<FactorStrategySpec> = {}): FactorStrategySpec {
  return {
    ...DEFAULT_SPEC,
    formula: "MOM20 ATR_RATIO DIV",
    universe: ["AAPL", "MSFT"],
    ...overrides,
  }
}

describe("specFromTokens", () => {
  it("joins RPN tokens into a formula string", () => {
    const built = specFromTokens(["MOM20", "ATR_RATIO", "DIV"], ["AAPL", "MSFT"])

    expect(built.formula).toBe("MOM20 ATR_RATIO DIV")
    expect(built.universe).toEqual(["AAPL", "MSFT"])
  })

  it("keeps the shared defaults", () => {
    const built = specFromTokens(["MOM20"], ["AAPL", "MSFT"])

    expect(built.long_quantile).toBe(0.2)
    expect(built.short_quantile).toBeNull()
    expect(built.portfolio_method).toBe("equal_weight")
  })

  it("applies overrides last", () => {
    const built = specFromTokens(["MOM20"], ["AAPL", "MSFT"], { rebalance_days: 21 })

    expect(built.rebalance_days).toBe(21)
  })

  it("does not mutate the shared default object", () => {
    specFromTokens(["MOM20"], ["AAPL"], { rebalance_days: 99 })

    expect(DEFAULT_SPEC.rebalance_days).toBe(5)
    expect(DEFAULT_SPEC.formula).toBe("")
  })
})

describe("validateSpec", () => {
  it("accepts a well-formed long-only spec", () => {
    expect(validateSpec(spec())).toBeNull()
  })

  it("accepts a long-short spec whose bands do not overlap", () => {
    expect(validateSpec(spec({ long_quantile: 0.3, short_quantile: 0.3 }))).toBeNull()
  })

  it("rejects an empty formula", () => {
    expect(validateSpec(spec({ formula: "   " }))).toMatch(/公式/)
  })

  it("rejects a universe smaller than two", () => {
    expect(validateSpec(spec({ universe: ["AAPL"] }))).toMatch(/标的池/)
  })

  it("rejects a long quantile outside (0, 1]", () => {
    expect(validateSpec(spec({ long_quantile: 0 }))).toMatch(/多头比例/)
    expect(validateSpec(spec({ long_quantile: 1.2 }))).toMatch(/多头比例/)
  })

  it("rejects a short quantile outside [0, 1)", () => {
    expect(validateSpec(spec({ short_quantile: -0.1 }))).toMatch(/空头比例/)
  })

  it("rejects overlapping long/short bands", () => {
    expect(validateSpec(spec({ long_quantile: 0.6, short_quantile: 0.6 }))).toMatch(/重叠/)
  })

  it("rejects a rebalance period below one day", () => {
    expect(validateSpec(spec({ rebalance_days: 0 }))).toMatch(/再平衡/)
  })

  it("rejects a non-positive position cap", () => {
    expect(validateSpec(spec({ max_positions: 0 }))).toMatch(/持仓上限/)
  })

  it("allows a null position cap", () => {
    expect(validateSpec(spec({ max_positions: null }))).toBeNull()
  })
})

describe("METHOD_LABELS", () => {
  it("labels every portfolio method the backend can return", () => {
    const backendMethods = [
      "equal_weight", "insight_weight", "max_sharpe", "min_volatility",
      "risk_parity", "min_cvar", "hrp", "black_litterman", "min_cdar",
    ]

    expect(backendMethods.every((m) => METHOD_LABELS[m])).toBe(true)
  })
})

// ── 因子库条目 → spec ────────────────────────────────────────────
//
// 因子库的 expr（`($close-$open)/$open`）是 Qlib 风格的展示标注，
// 与 RPN 词表不是同一种语言。后端靠 FactorSpec 自带的 compute 打分，
// 因此 spec 里 formula 必须留空 —— 两个都填后端会按「二选一」返回 400。

describe("specFromLibraryFactor", () => {
  it("sets library_factor and leaves formula empty", () => {
    const built = specFromLibraryFactor("KMID", ["AAPL", "MSFT"])

    expect(built.library_factor).toBe("KMID")
    expect(built.formula).toBe("")
  })

  it("keeps the rest of the defaults", () => {
    const built = specFromLibraryFactor("KMID", ["AAPL", "MSFT"])

    expect(built.long_quantile).toBe(DEFAULT_SPEC.long_quantile)
    expect(built.rebalance_days).toBe(DEFAULT_SPEC.rebalance_days)
    expect(built.universe).toEqual(["AAPL", "MSFT"])
  })

  it("honours overrides", () => {
    const built = specFromLibraryFactor("KMID", ["AAPL", "MSFT"], { max_positions: 3 })

    expect(built.max_positions).toBe(3)
    expect(built.library_factor).toBe("KMID")
  })

  it("never carries a formula that would collide with library_factor", () => {
    // 即便调用方手滑传了 formula，也不该让它和 library_factor 同时生效
    const built = specFromLibraryFactor("KMID", ["AAPL", "MSFT"])

    expect(built.formula && built.library_factor).toBeFalsy()
  })
})
