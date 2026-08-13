// 各内置策略的可调参数定义：启动表单据此渲染输入控件，实例卡据此回显参数。
export interface ParamDef {
  key: string
  label: string
  type: "int" | "float" | "select"
  default: number | string
  min?: number
  max?: number
  step?: number
  options?: { value: string; label: string }[]
  hint?: string
}

export const STRATEGY_PARAM_DEFS: Record<string, ParamDef[]> = {
  double_ma: [
    { key: "fast_period", label: "快线周期", type: "int",    default: 10,   min: 3,  max: 60,  hint: "短期均线，越小越灵敏" },
    { key: "slow_period", label: "慢线周期", type: "int",    default: 30,   min: 10, max: 200, hint: "长期均线，需 > 快线" },
    { key: "ma_type",     label: "均线类型", type: "select", default: "sma", options: [{ value: "sma", label: "SMA 简单均线" }, { value: "ema", label: "EMA 指数均线" }] },
  ],
  triple_ma: [
    { key: "fast_period", label: "快线周期", type: "int", default: 5,  min: 2,  max: 30  },
    { key: "mid_period",  label: "中线周期", type: "int", default: 15, min: 5,  max: 60  },
    { key: "slow_period", label: "慢线周期", type: "int", default: 30, min: 10, max: 200 },
    { key: "ma_type",     label: "均线类型", type: "select", default: "sma", options: [{ value: "sma", label: "SMA" }, { value: "ema", label: "EMA" }] },
  ],
  macd: [
    { key: "fast",   label: "快线 EMA", type: "int", default: 12, min: 3,  max: 50  },
    { key: "slow",   label: "慢线 EMA", type: "int", default: 26, min: 10, max: 100 },
    { key: "signal", label: "信号线",   type: "int", default: 9,  min: 3,  max: 30  },
  ],
  supertrend: [
    { key: "period",     label: "ATR 周期",   type: "int",   default: 10,  min: 5,  max: 50               },
    { key: "multiplier", label: "ATR 倍数",   type: "float", default: 3.0, min: 1.0, max: 6.0, step: 0.5 },
  ],
  adx_trend: [
    { key: "adx_period",    label: "ADX 周期",   type: "int", default: 14, min: 5, max: 50              },
    { key: "adx_threshold", label: "ADX 阈值",   type: "int", default: 25, min: 15, max: 40, hint: "高于此值才交易" },
    { key: "fast_period",   label: "快线周期",   type: "int", default: 10, min: 3,  max: 50              },
    { key: "slow_period",   label: "慢线周期",   type: "int", default: 30, min: 10, max: 200             },
  ],
  bollinger: [
    { key: "period",  label: "布林周期", type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "std_dev", label: "标准差倍数", type: "float", default: 2.0, min: 1.0, max: 4.0, step: 0.5 },
  ],
  rsi_mean_reversion: [
    { key: "period",     label: "RSI 周期", type: "int", default: 14, min: 5,  max: 50             },
    { key: "oversold",   label: "超卖线",   type: "int", default: 30, min: 10, max: 45, hint: "低于此值买入" },
    { key: "overbought", label: "超买线",   type: "int", default: 70, min: 55, max: 90, hint: "高于此值卖出" },
  ],
  stochastic: [
    { key: "k_period",   label: "K 周期",  type: "int", default: 14, min: 3,  max: 50 },
    { key: "d_period",   label: "D 周期",  type: "int", default: 3,  min: 1,  max: 10 },
    { key: "oversold",   label: "超卖线",  type: "int", default: 20, min: 5,  max: 35 },
    { key: "overbought", label: "超买线",  type: "int", default: 80, min: 65, max: 95 },
  ],
  vwap_reversion: [
    { key: "period",    label: "VWAP 周期", type: "int",   default: 20,   min: 5,  max: 60  },
    { key: "threshold", label: "偏离阈值",  type: "float", default: 0.02, min: 0.005, max: 0.1, step: 0.005, hint: "偏离 VWAP 多少触发" },
  ],
  donchian_breakout: [
    { key: "period", label: "唐奇安通道周期", type: "int", default: 20, min: 5, max: 100 },
  ],
  keltner_breakout: [
    { key: "ema_period",  label: "EMA 周期",  type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "atr_period",  label: "ATR 周期",  type: "int",   default: 10,  min: 3,  max: 50              },
    { key: "multiplier",  label: "ATR 倍数",  type: "float", default: 2.0, min: 0.5, max: 5.0, step: 0.5 },
  ],
  atr_breakout: [
    { key: "channel_period", label: "通道周期", type: "int",   default: 20,  min: 5,  max: 100             },
    { key: "atr_period",     label: "ATR 周期", type: "int",   default: 14,  min: 3,  max: 50              },
    { key: "multiplier",     label: "ATR 倍数", type: "float", default: 0.5, min: 0.1, max: 3.0, step: 0.1 },
  ],
  momentum: [
    { key: "lookback",  label: "动量周期", type: "int",   default: 20,   min: 5,  max: 100             },
    { key: "threshold", label: "动量阈值", type: "float", default: 0.03, min: 0.005, max: 0.2, step: 0.005 },
  ],
}

export const DEFAULT_PARAMS: Record<string, Record<string, number | string>> = Object.fromEntries(
  Object.entries(STRATEGY_PARAM_DEFS).map(([k, defs]) => [
    k,
    Object.fromEntries(defs.map((d) => [d.key, d.default])),
  ])
)
