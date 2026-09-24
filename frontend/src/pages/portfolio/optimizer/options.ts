// 组合优化的方法/估计器选项表：配置表单要渲染它们，结果面板要回显它们的中文名，
// 两边都读同一份，避免出现「选的是 A、显示成 B」这种漂移。
import { format, subYears } from "date-fns"
import type {
  AdvancedOptMethod, AdvancedOptResult, AdvancedRiskModel,
  AdvancedReturnsMethod, HrpLinkage,
} from "@/hooks/usePortfolioAdvanced"
import type { AllocationMethod } from "@/types"

// 类型别名：沿用页面内既有命名，底层改用高级 hook 的超集类型
export type PortfolioOptMethod = AdvancedOptMethod
export type PortfolioOptResult = AdvancedOptResult
export type RiskModel = AdvancedRiskModel
export type ExpectedReturnsMethod = AdvancedReturnsMethod

// ── 常量 ──────────────────────────────────────────────────────

export const METHOD_OPTIONS: { value: PortfolioOptMethod; label: string; desc: string }[] = [
  { value: "max_sharpe",    label: "最大夏普",     desc: "最大化风险调整后收益" },
  { value: "min_volatility",label: "最小波动",     desc: "最小化组合波动率" },
  { value: "risk_parity",   label: "风险平价",     desc: "均等化各资产风险贡献" },
  { value: "hrp",           label: "层次风险平价 HRP", desc: "相关性聚类 + 递归二分，小样本更稳，无需求逆" },
  { value: "black_litterman", label: "Black-Litterman", desc: "市场均衡先验 + 你的观点，Idzorek 置信度加权" },
  { value: "min_cvar",      label: "最小 CVaR",    desc: "线性规划最小化尾部损失（条件风险价值）" },
  { value: "min_cdar",      label: "最小 CDaR",    desc: "线性规划最小化条件回撤风险" },
  { value: "equal_weight",  label: "等权重基准",   desc: "等权对照组" },
]

export const HRP_LINKAGE_OPTIONS: { value: HrpLinkage; label: string }[] = [
  { value: "single",   label: "single（最近邻）" },
  { value: "complete", label: "complete（最远邻）" },
  { value: "average",  label: "average（平均）" },
  { value: "ward",     label: "ward（方差最小）" },
]

/** 需要 BL 观点输入的方法 */
export function isBlackLitterman(m: PortfolioOptMethod): boolean {
  return m === "black_litterman"
}
/** 需要 HRP 连接方式选项的方法 */
export function isHrp(m: PortfolioOptMethod): boolean {
  return m === "hrp"
}
/** 需要 CVaR/CDaR 置信水平选项的方法 */
export function isTailRisk(m: PortfolioOptMethod): boolean {
  return m === "min_cvar" || m === "min_cdar"
}

export const RISK_MODEL_OPTIONS: { value: RiskModel; label: string; desc: string }[] = [
  { value: "sample_cov",     label: "样本协方差",     desc: "历史样本协方差（基准）" },
  { value: "ledoit_wolf",    label: "Ledoit-Wolf 收缩", desc: "收缩估计，降噪、抗病态（推荐）" },
  { value: "exp_cov",        label: "指数加权",       desc: "近期数据权重更高，体制感知" },
  { value: "semicovariance", label: "下行半协方差",   desc: "只统计下行波动，偏重尾部风险" },
]

export const RETURNS_OPTIONS: { value: ExpectedReturnsMethod; label: string; desc: string }[] = [
  { value: "mean_historical", label: "历史均值",   desc: "历史收益均值（基准）" },
  { value: "ema_historical",  label: "指数加权均值", desc: "趋势倾斜，近期表现权重更高" },
  { value: "capm",            label: "CAPM 隐含",   desc: "市场 β 隐含收益，抗噪声均值" },
]

export const ALLOCATION_OPTIONS: { value: AllocationMethod; label: string; desc: string }[] = [
  { value: "greedy", label: "贪心", desc: "快速、无求解器" },
  { value: "lp",     label: "整数规划", desc: "L1 最优，略慢" },
]

export const MARKET_DEFAULTS: Record<string, string[]> = {
  US: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "V"],
  HK: ["00700", "02318", "09988", "01299", "02020"],
  A:  ["000001", "600519", "300750", "000858", "601318"],
}

export const PALETTE = [
  "#58a6ff", "#3fb950", "#f85149", "#e3b341", "#bc8cff",
  "#ff9f43", "#54a0ff", "#00d2d3", "#ff6b81", "#5f27cd",
]

export function today() { return format(new Date(), "yyyy-MM-dd") }
export function yearsAgo(n: number) { return format(subYears(new Date(), n), "yyyy-MM-dd") }

