import { useMutation } from "@tanstack/react-query"
import { ApiError, api } from "@/lib/api"

// ── AI 报告（V3 Wave C-a / I4+I5）─────────────────────────────────────────────
//
// 类型与 backend/app/api/v1/endpoints/ai_reports.py 的 schema 一一对应。
//
// 与 Copilot 不同，这两个端点在「一个模型都没配」时回的是 **501**（功能未启用）
// 而不是 200 + needs_setup。所以引导块由前端在捕获到 501 时自己渲染 ——
// 见 `isSetupNeeded`。

const BASE = "/api/v1/ai/reports"

/** 未配置模型时引导用户去的页面。 */
export const SETUP_URL = "/settings/models"

/** 后端未配置模型时的状态码（功能未启用，区别于 503「依赖挂了」）。 */
const STATUS_NOT_CONFIGURED = 501

/** 该错误是不是「还没配模型」——是的话应展示引导块而不是红色报错。 */
export function isSetupNeeded(error: unknown): boolean {
  return error instanceof ApiError && error.status === STATUS_NOT_CONFIGURED
}

// ── I4 个股研报 ──────────────────────────────────────────────────────────────

export interface StockReportRequest {
  symbol: string
  market: string
  lookback_days?: number
}

export interface SectionTitle {
  key: string
  title: string
}

/** 实际喂给模型的一条新闻 —— 用户据此核对模型有没有瞎编。 */
export interface NewsSourceItem {
  title: string
  published_at: string | null
  publisher: string | null
  url: string | null
}

export interface StockReport {
  symbol: string
  market: string
  sections: Record<string, string>
  /** 分节的键与中文标题，顺序即展示顺序 */
  section_titles: SectionTitle[]
  sources: NewsSourceItem[]
  /** 本次缺了哪些数据 —— 与提示词里给模型看的是同一句话 */
  data_notes: string[]
  technicals: Record<string, number | string | null>
  generated_at: string
  model: string
  provider_id: string
  disclaimer: string
}

export function useStockReport() {
  return useMutation<StockReport, Error, StockReportRequest>({
    mutationFn: (req) => api.post<StockReport>(`${BASE}/stock`, req),
  })
}

// ── I5 回测 AI 诊断 ──────────────────────────────────────────────────────────

export interface DiagnosisFinding {
  title: string
  detail: string
  /** high | medium | low */
  severity: string
  rule: string | null
}

/** 一处「AI 解读」与「规则判据」冲突 —— 非空即表示这份诊断不可照单全收。 */
export interface DiagnosisContradiction {
  rule: string
  claim: string
  detail: string
}

export interface BacktestDiagnosis {
  run_id: string | null
  grade_level: string
  grade_score: number
  /** grade.based_on，形如「基于 3/5 步」 */
  coverage: string
  is_complete: boolean
  summary: string
  findings: DiagnosisFinding[]
  next_steps: string[]
  contradictions: DiagnosisContradiction[]
  has_contradiction: boolean
  generated_at: string
  model: string
  provider_id: string
  disclaimer: string
}

/**
 * 诊断请求带的是完整验证的**结果体本身**，不是 run_id。
 *
 * `full-validation` 的 run_id 每次请求现生成且从不落库，拿 id 反查是查不到东西的。
 * 前端手里就有那份结果，直接回传是唯一不撒谎的做法。
 */
export interface BacktestDiagnosisRequest {
  run_id?: string | null
  grade: Record<string, unknown>
  steps?: Record<string, Record<string, unknown>>
}

export function useBacktestDiagnosis() {
  return useMutation<BacktestDiagnosis, Error, BacktestDiagnosisRequest>({
    mutationFn: (req) => api.post<BacktestDiagnosis>(`${BASE}/backtest`, req),
  })
}

// ── 展示辅助 ─────────────────────────────────────────────────────────────────

export const SEVERITY_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
}

export const SEVERITY_TONES: Record<string, string> = {
  high: "text-[#f85149] border-[#f85149]/40",
  medium: "text-[#d29922] border-[#d29922]/40",
  low: "text-[#58a6ff] border-[#58a6ff]/40",
}

/** 把 ISO 时间压成 `YYYY-MM-DD HH:mm`；解析不了就原样返回。 */
export function formatStamp(value: string | null): string {
  if (!value) return "时间未知"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ` +
    `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  )
}
