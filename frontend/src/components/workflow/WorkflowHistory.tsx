// ── 工作流历史记录列表 ─────────────────────────────────────────
// 在仪表盘「智能交易引导」空闲状态下展示，支持跳转到实盘策略页。

import { useState } from "react"
import { Link } from "react-router-dom"
import type { WorkflowHistoryEntry } from "@/hooks/useWorkflowStorage"

const VERDICT_CFG = {
  pass: { label: "合格",   color: "#3fb950" },
  warn: { label: "注意",   color: "#e3b341" },
  fail: { label: "不合格", color: "#f85149" },
}

const MARKET_LABEL: Record<string, string> = { US: "美股", HK: "港股", A: "A股" }

function relTime(ts: number): string {
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60_000)
  const h = Math.floor(diff / 3_600_000)
  const d = Math.floor(diff / 86_400_000)
  if (m < 1)  return "刚刚"
  if (h < 1)  return `${m} 分钟前`
  if (d < 1)  return `${h} 小时前`
  return `${d} 天前`
}

function HistoryRow({ e }: { e: WorkflowHistoryEntry }) {
  const vcfg = e.verdict ? VERDICT_CFG[e.verdict] : null
  return (
    <div className="flex items-center gap-3 py-2 px-3 rounded-lg bg-[#0d1117] hover:bg-[#161b22] transition-colors text-[10px]">
      {/* Symbol */}
      <div className="shrink-0 w-24">
        <p className="font-mono font-bold text-[#e6edf3] text-xs">{e.symbol}</p>
        <p className="text-[#6e7681]">{MARKET_LABEL[e.market] ?? e.market}</p>
      </div>

      {/* Strategy + verdict */}
      <div className="flex-1 min-w-0">
        <p className="text-[#8b949e] truncate">{e.strategyName}</p>
        {vcfg && (
          <span className="inline-block mt-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium"
                style={{ color: vcfg.color, background: vcfg.color + "20" }}>
            {vcfg.label}
          </span>
        )}
      </div>

      {/* Metrics */}
      {e.sharpe !== null && (
        <div className="shrink-0 text-right hidden sm:block">
          <p className="text-[#6e7681]">Sharpe <span className="text-[#e6edf3] font-mono">{e.sharpe.toFixed(2)}</span></p>
          {e.drawdown !== null && (
            <p className="text-[#6e7681]">回撤 <span className="text-[#f85149] font-mono">{e.drawdown.toFixed(1)}%</span></p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="shrink-0 flex flex-col items-end gap-1">
        <p className="text-[#6e7681]">{relTime(e.timestamp)}</p>
        {e.instanceId ? (
          <Link to="/live-strategy"
                className="text-[#3fb950] hover:underline text-[9px]">
            查看模拟盘 →
          </Link>
        ) : (
          <span className="text-[9px] text-[#6e7681]">
            {e.phase === "completed" ? "已切实盘" : "未启动"}
          </span>
        )}
      </div>
    </div>
  )
}

interface Props {
  entries: WorkflowHistoryEntry[]
  onClear: () => void
}

export function WorkflowHistory({ entries, onClear }: Props) {
  const [open, setOpen] = useState(false)
  if (entries.length === 0) return null

  return (
    <div className="mt-4 border-t border-[#21262d] pt-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-[10px] text-[#6e7681] hover:text-[#8b949e] transition-colors w-full"
      >
        <span>◷</span>
        <span>历史分析记录（{entries.length} 条）</span>
        <span className="ml-auto">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-1.5">
          {entries.map(e => <HistoryRow key={e.id} e={e} />)}
          <div className="flex justify-end pt-1">
            <button
              onClick={onClear}
              className="text-[9px] text-[#6e7681] hover:text-[#f85149] transition-colors"
            >
              清除全部记录
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
