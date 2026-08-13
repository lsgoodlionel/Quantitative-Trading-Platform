// ── 通知展示元数据 ──────────────────────────────────────────────
// 纯函数，无 React 依赖：事件类型 → emoji / 中文标签 / 语义色，以及相对时间。
// 与后端 app/notify/events.py 的 _EVENT_LABELS 保持一致。

export interface NotificationMeta {
  emoji: string
  label: string
  /** 语义色：告警类走红/黄，完成类走绿/蓝 */
  tone: "info" | "success" | "warning" | "danger"
}

const META: Record<string, NotificationMeta> = {
  trade_fill:           { emoji: "✅", label: "成交",         tone: "success" },
  order_reject:         { emoji: "⛔", label: "订单拒绝",     tone: "danger"  },
  pnl_update:           { emoji: "💰", label: "盈亏更新",     tone: "info"    },
  position:             { emoji: "📊", label: "持仓变动",     tone: "info"    },
  daily_summary:        { emoji: "📅", label: "每日汇总",     tone: "info"    },
  risk_alert:           { emoji: "⚠️", label: "风控告警",     tone: "warning" },
  protection:           { emoji: "🛡️", label: "防护熔断",     tone: "danger"  },
  backtest_done:        { emoji: "🔬", label: "回测完成",     tone: "success" },
  hyperopt_done:        { emoji: "🎛️", label: "参数寻优完成", tone: "success" },
  mining_done:          { emoji: "⛏️", label: "因子挖掘完成", tone: "success" },
  data_source_degraded: { emoji: "📡", label: "数据源降级",   tone: "warning" },
  reconcile_diff:       { emoji: "🧾", label: "对账差异",     tone: "warning" },
}

const FALLBACK: NotificationMeta = { emoji: "🔔", label: "通知", tone: "info" }

/** 未知事件类型回退到通用「通知」，避免后端新增类型时前端崩掉 */
export function notificationMeta(type: string): NotificationMeta {
  return META[type] ?? FALLBACK
}

/** 已知事件类型全集（供筛选下拉使用） */
export function knownNotificationTypes(): string[] {
  return Object.keys(META)
}

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期 */
export function formatRelativeTime(createdAtSec: number, nowMs: number = Date.now()): string {
  const diff = Math.floor(nowMs / 1000 - createdAtSec)
  if (!Number.isFinite(diff) || diff < 0) return "刚刚"
  if (diff < MINUTE) return "刚刚"
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)} 天前`
  return new Date(createdAtSec * 1000).toLocaleDateString("zh-CN")
}

/** payload 值渲染为一行文本（对象/数组走 JSON，避免 [object Object]） */
export function formatPayloadValue(value: unknown): string {
  if (value == null) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
