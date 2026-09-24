import { useCallback, useEffect, useRef, useState } from "react"
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

// ── 异步入口（长任务）────────────────────────────────────────────
//
// 同步端点在默认参数下几十秒返回，够用。但寻优次数 / 蒙特卡洛场景数 / 回测区间
// 都是用户可调的，调大之后同步请求会撞上网关超时 —— 那时拿到的是 504，
// 已经跑掉的算力全部作废。异步路径把这种情况变成「提交 → 轮询」。

interface AsyncSubmitResponse {
  task_id: string
  status: string
}

interface AsyncResultResponse {
  task_id: string
  /** queued / running / done / error */
  status: string
  result: FullValidationResult | null
  error: string | null
}

/** 轮询间隔。完整验证以十秒计，1s 足够及时又不至于把后端刷满。 */
const POLL_INTERVAL_MS = 1000

export function useFullValidationAsync() {
  const [result, setResult] = useState<FullValidationResult | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isPending, setIsPending] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelled = useRef(false)

  // 组件卸载后必须停轮询，否则会对着已销毁的组件 setState
  useEffect(() => () => {
    cancelled.current = true
    if (timer.current) clearTimeout(timer.current)
  }, [])

  const poll = useCallback(async (taskId: string) => {
    if (cancelled.current) return
    try {
      const res = await api.get<AsyncResultResponse>(
        `/api/v1/backtests/full-validation/async/${taskId}`,
      )
      if (cancelled.current) return
      if (res.status === "done" && res.result) {
        setResult(res.result)
        setIsPending(false)
        return
      }
      if (res.status === "error") {
        setError(new Error(res.error ?? "验证任务失败"))
        setIsPending(false)
        return
      }
      timer.current = setTimeout(() => void poll(taskId), POLL_INTERVAL_MS)
    } catch (e) {
      if (cancelled.current) return
      setError(e instanceof Error ? e : new Error(String(e)))
      setIsPending(false)
    }
  }, [])

  const start = useCallback(async (req: FullValidationRequest) => {
    setResult(null)
    setError(null)
    setIsPending(true)
    try {
      const submitted = await api.post<AsyncSubmitResponse>(
        "/api/v1/backtests/full-validation/async", req,
      )
      await poll(submitted.task_id)
    } catch (e) {
      // 提交阶段的失败（配置非法 400 / 队列不可用 503）直接结束，不进轮询
      setError(e instanceof Error ? e : new Error(String(e)))
      setIsPending(false)
    }
  }, [poll])

  return { start, result, error, isPending }
}
