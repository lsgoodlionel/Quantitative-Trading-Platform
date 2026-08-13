import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 完整验证（V3 · H2）─────────────────────────────────────────

export const VALIDATION_STEPS = ["backtest", "optimize", "walkforward", "bias", "robustness"] as const
export type ValidationStep = (typeof VALIDATION_STEPS)[number]

export const STEP_LABELS: Record<ValidationStep, string> = {
  backtest: "策略回测",
  optimize: "参数寻优",
  walkforward: "Walk-Forward",
  bias: "偏差检测",
  robustness: "蒙特卡洛稳健性",
}

export interface FullValidationRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  params?: Record<string, unknown>
  steps?: ValidationStep[]
  param_space?: Record<string, unknown>
  loss_function?: string
  n_trials?: number
  n_scenarios?: number
}

export interface GradeFinding {
  step: string
  rule: string
  metric: string
  value: number
  threshold: number
  penalty: number
  detail: string
}

export interface GradeNotEvaluated {
  step: string
  rule: string
  reason: string
}

export interface ValidationGrade {
  score: number
  level: string
  level_label: string
  completed_steps: string[]
  failed_steps: string[]
  skipped_steps: string[]
  findings: GradeFinding[]
  not_evaluated: GradeNotEvaluated[]
  based_on: string
  is_complete: boolean
  disclaimer: string
}

export type ValidationStepResult = Record<string, unknown> & { error?: string }

export interface FullValidationResult {
  run_id: string
  requested_steps: string[]
  steps: Record<string, ValidationStepResult>
  grade: ValidationGrade
}

export function useFullValidation() {
  return useMutation<FullValidationResult, Error, FullValidationRequest>({
    mutationFn: (req) => api.post<FullValidationResult>("/api/v1/backtests/full-validation", req),
  })
}
