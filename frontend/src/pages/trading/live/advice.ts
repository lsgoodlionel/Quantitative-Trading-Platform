// 模拟盘结果 → 决策建议：把净值指标翻译成「推进/调参/观察」和具体的调参方向。
import type { PaperSimResult } from "@/types"

export function computeAdvice(paper: PaperSimResult) {
  const { total_return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, profit_factor } = paper
  const dd = Math.abs(max_drawdown_pct)
  const issues: string[] = []
  const goods: string[] = []
  const paramHints: string[] = []

  if (total_return_pct > 0) goods.push(`模拟收益 +${total_return_pct.toFixed(1)}%`)
  if (sharpe_ratio > 1.0)   goods.push(`Sharpe ${sharpe_ratio.toFixed(2)} 优秀`)
  if (win_rate_pct > 50)    goods.push(`胜率 ${win_rate_pct.toFixed(0)}% 良好`)
  if (profit_factor > 1.5)  goods.push(`盈亏比 ${profit_factor.toFixed(2)} 优异`)

  if (dd > 25) {
    issues.push(`最大回撤 ${dd.toFixed(1)}% 偏高（建议 <20%）`)
    paramHints.push("尝试缩小仓位比例，或增大均线周期来减少频繁交易")
  }
  if (sharpe_ratio < 0.5) {
    issues.push(`Sharpe ${sharpe_ratio.toFixed(2)} 偏低（建议 >1.0）`)
    paramHints.push("收益波动过大，可适当放宽入场条件参数（如 RSI 超卖线降至 25）")
  }
  if (total_return_pct < 0) {
    issues.push(`模拟亏损 ${total_return_pct.toFixed(1)}%`)
    paramHints.push("策略方向可能与当前市场不符，可尝试调整快慢线比例或换用均值回归策略")
  }
  if (profit_factor < 1.0 && profit_factor > 0) {
    issues.push(`盈亏比 ${profit_factor.toFixed(2)} < 1（亏大于盈）`)
    paramHints.push("止盈条件过早触发，或止损太宽，可尝试收紧超买线至 65 或扩大超卖线至 35")
  }
  if (paper.total_trades < 3 && paper.sim_days >= 60) {
    issues.push(`模拟期间仅 ${paper.total_trades} 笔交易，信号过少`)
    paramHints.push("参数过于保守，可尝试缩短均线周期或放宽阈值，增加信号频率")
  }

  const action = issues.length === 0 && goods.length >= 2
    ? "proceed"
    : issues.length >= 2
      ? "adjust"
      : "watch"

  const label = action === "proceed"
    ? "建议推进实盘"
    : action === "adjust"
      ? "建议调整参数"
      : "继续观察一段时间"

  const color = action === "proceed" ? "#3fb950" : action === "adjust" ? "#f85149" : "#e3b341"
  return { action, label, color, goods, issues, paramHints }
}
