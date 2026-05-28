// ── 策略参数定义 — 参数调整 UI 专用 ─────────────────────────────────

export interface ParamDef {
  key: string
  label: string
  type: "int" | "float"
  min: number
  max: number
  step: number
  default: number
  hint: string
}

/**
 * 每个预设策略的可调参数列表（仅数值型参数）。
 * 调整 UI 按此渲染滑块/输入框，hints 告诉用户每个参数的作用。
 */
export const STRATEGY_PARAMS: Record<string, ParamDef[]> = {
  double_ma: [
    { key: "fast_period", label: "快线周期", type: "int",   min: 3,     max: 30,   step: 1,     default: 10,  hint: "越小反应越灵敏，但假信号越多" },
    { key: "slow_period", label: "慢线周期", type: "int",   min: 10,    max: 100,  step: 1,     default: 30,  hint: "越大趋势确认越稳定，但滞后越明显" },
  ],
  triple_ma: [
    { key: "fast_period", label: "快线周期", type: "int",   min: 3,     max: 15,   step: 1,     default: 5,   hint: "最短均线，捕捉短期动量" },
    { key: "mid_period",  label: "中线周期", type: "int",   min: 8,     max: 30,   step: 1,     default: 13,  hint: "中期确认线（Fibonacci 参数 13）" },
    { key: "slow_period", label: "慢线周期", type: "int",   min: 20,    max: 60,   step: 1,     default: 34,  hint: "长期趋势基准（Fibonacci 参数 34）" },
  ],
  macd: [
    { key: "fast",        label: "快线 EMA", type: "int",   min: 5,     max: 20,   step: 1,     default: 12,  hint: "越小信号越频繁" },
    { key: "slow",        label: "慢线 EMA", type: "int",   min: 15,    max: 50,   step: 1,     default: 26,  hint: "越大趋势确认越稳" },
    { key: "signal",      label: "信号线",   type: "int",   min: 3,     max: 15,   step: 1,     default: 9,   hint: "MACD 柱的平滑周期" },
  ],
  supertrend: [
    { key: "period",      label: "ATR 周期", type: "int",   min: 5,     max: 30,   step: 1,     default: 10,  hint: "越大通道越平滑" },
    { key: "multiplier",  label: "ATR 倍数", type: "float", min: 1.0,   max: 5.0,  step: 0.5,   default: 3.0, hint: "越大通道越宽，翻转信号越少越可靠" },
  ],
  adx_trend: [
    { key: "fast_period",    label: "快线周期",    type: "int",   min: 5,     max: 20,   step: 1,     default: 10,  hint: "快速均线周期" },
    { key: "slow_period",    label: "慢线周期",    type: "int",   min: 15,    max: 60,   step: 1,     default: 30,  hint: "慢速均线周期" },
    { key: "adx_period",     label: "ADX 周期",   type: "int",   min: 7,     max: 21,   step: 1,     default: 14,  hint: "ADX 趋势强度的计算周期" },
    { key: "adx_threshold",  label: "趋势入场门槛", type: "int",   min: 15,    max: 40,   step: 1,     default: 25,  hint: "超过此值才允许入场，25 为行业标准，越高越保守" },
  ],
  bollinger: [
    { key: "period",     label: "均线周期",   type: "int",   min: 10,    max: 50,   step: 1,     default: 20,  hint: "中轨均线周期，越大通道越稳定" },
    { key: "std_dev",    label: "标准差倍数", type: "float", min: 1.0,   max: 3.5,  step: 0.5,   default: 2.0, hint: "通道宽度：2.0σ 覆盖约 95% 的价格" },
  ],
  rsi_mean_reversion: [
    { key: "period",     label: "RSI 周期",  type: "int",   min: 7,     max: 21,   step: 1,     default: 14,  hint: "RSI 计算周期，14 日为行业标准" },
    { key: "oversold",   label: "超卖阈值",  type: "int",   min: 15,    max: 40,   step: 1,     default: 30,  hint: "低于此值买入，越低信号越少但质量越高" },
    { key: "overbought", label: "超买阈值",  type: "int",   min: 60,    max: 85,   step: 1,     default: 70,  hint: "高于此值卖出，越高信号越少但质量越高" },
  ],
  stochastic: [
    { key: "k_period",    label: "K 线周期",  type: "int",   min: 5,     max: 21,   step: 1,     default: 14,  hint: "随机指标 K 值的计算周期" },
    { key: "d_period",    label: "D 线平滑",  type: "int",   min: 2,     max: 7,    step: 1,     default: 3,   hint: "K 值的平滑周期，越大 D 线越稳定" },
    { key: "oversold",    label: "超卖区间",  type: "int",   min: 10,    max: 30,   step: 1,     default: 20,  hint: "K%D 在此区间下方触发买入" },
    { key: "overbought",  label: "超买区间",  type: "int",   min: 70,    max: 90,   step: 1,     default: 80,  hint: "K%D 在此区间上方触发卖出" },
  ],
  vwap_reversion: [
    { key: "period",         label: "VWAP 周期",    type: "int",   min: 5,     max: 50,   step: 1,     default: 20,   hint: "滚动 VWAP 的计算周期数" },
    { key: "dev_threshold",  label: "偏离触发阈值",  type: "float", min: 0.005, max: 0.05, step: 0.005, default: 0.02, hint: "偏离 VWAP 的比例，0.02 = 2% 偏差" },
  ],
  donchian_breakout: [
    { key: "period",       label: "突破参考周期", type: "int",   min: 10,    max: 60,   step: 1,     default: 20,  hint: "N 日新高买入（海龟交易默认 20 日）" },
    { key: "exit_period",  label: "退出参考周期", type: "int",   min: 5,     max: 30,   step: 1,     default: 10,  hint: "跌破 M 日最低时平仓（通常为 N 的一半）" },
  ],
  keltner_breakout: [
    { key: "ema_period",  label: "中轨 EMA",   type: "int",   min: 10,    max: 40,   step: 1,     default: 20,  hint: "中轨 EMA 计算周期" },
    { key: "atr_period",  label: "ATR 周期",   type: "int",   min: 5,     max: 20,   step: 1,     default: 10,  hint: "ATR 波动率计算周期" },
    { key: "multiplier",  label: "通道 ATR 倍数", type: "float", min: 1.0, max: 4.0,  step: 0.5,   default: 2.0, hint: "越大通道越宽，突破质量越高" },
  ],
  atr_breakout: [
    { key: "channel_period", label: "区间参考周期", type: "int",   min: 10,    max: 50,   step: 1,     default: 20,  hint: "N 日高低价区间的参考周期" },
    { key: "atr_period",     label: "ATR 周期",    type: "int",   min: 7,     max: 21,   step: 1,     default: 14,  hint: "ATR 波动率计算周期" },
    { key: "multiplier",     label: "突破加成倍数", type: "float", min: 0.2,   max: 2.0,  step: 0.1,   default: 0.5, hint: "门槛 = N 日高点 + k×ATR，越大信号质量越高" },
  ],
  momentum: [
    { key: "lookback",   label: "动量回望周期", type: "int",   min: 5,     max: 60,   step: 5,     default: 20,   hint: "过去 N 日收益率的累加" },
    { key: "threshold",  label: "入场动量门槛", type: "float", min: 0.01,  max: 0.10, step: 0.01,  default: 0.03, hint: "动量超过此阈值才入场，0.03 = 3%" },
  ],
  multi_factor: [
    { key: "momentum_lookback", label: "动量周期",     type: "int",   min: 5,     max: 60,   step: 5,     default: 20,  hint: "动量因子的回望周期" },
    { key: "rsi_period",        label: "RSI 周期",    type: "int",   min: 7,     max: 21,   step: 1,     default: 14,  hint: "RSI 因子的计算周期" },
    { key: "rsi_low",           label: "RSI 低位线",  type: "int",   min: 25,    max: 45,   step: 1,     default: 40,  hint: "RSI 低于此值时因子得分加权" },
    { key: "threshold",         label: "综合得分门槛", type: "float", min: 0.3,   max: 0.8,  step: 0.05,  default: 0.6, hint: "三因子加权得分超过此值才入场" },
  ],
  grid_trading: [
    { key: "grid_count",    label: "网格数量",   type: "int",   min: 5,     max: 20,   step: 1,     default: 10,   hint: "区间内划分的格子数量，越多交易越频繁" },
    { key: "grid_range",    label: "区间宽度",   type: "float", min: 0.05,  max: 0.20, step: 0.01,  default: 0.10, hint: "以基准价为中心的价格浮动范围" },
    { key: "qty_per_grid",  label: "每格交易量", type: "int",   min: 1,     max: 50,   step: 1,     default: 10,   hint: "每个网格节点的买入 / 卖出数量" },
  ],
  pairs_trading: [
    { key: "entry_z",   label: "开仓 Z 分数", type: "float", min: 1.0,   max: 3.5,  step: 0.5,   default: 2.0,  hint: "价差偏离 Z 值超过此阈值时建立套利仓" },
    { key: "exit_z",    label: "平仓 Z 分数", type: "float", min: 0.1,   max: 1.0,  step: 0.1,   default: 0.5,  hint: "价差收敛到此 Z 值以内时平仓获利" },
    { key: "lookback",  label: "协整回望周期", type: "int",   min: 20,    max: 120,  step: 10,    default: 60,   hint: "计算价差统计特征的历史周期" },
  ],
}
