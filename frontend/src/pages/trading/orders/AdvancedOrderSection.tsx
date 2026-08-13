import { useState } from "react"
import {
  useAlgoOrders,
  useCreateAlgoOrder,
  useCancelAlgoOrder,
  type AlgoOrder,
  type AlgoType,
  type AlgoStatus,
} from "@/hooks/useOrderAlgos"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import type { Market, OrderSide, OrderType } from "@/types"

// ── 常量 ──────────────────────────────────────────────────────
const MARKET_OPTS: { value: Market; label: string }[] = [
  { value: "US", label: "美股" },
  { value: "HK", label: "港股" },
  { value: "A", label: "A股" },
]

const ALGO_META: Record<AlgoType, { label: string; desc: string; accent: string }> = {
  TWAP: {
    label: "TWAP 时间加权",
    desc: "等量、等间隔拆分，摊平时间轴上的冲击成本",
    accent: "text-[#58a6ff] border-[#388bfd]/40 bg-[#132032]",
  },
  VWAP: {
    label: "VWAP 成交量加权",
    desc: "按日内 U 型成交量曲线加权，贴近市场量价节奏",
    accent: "text-[#3fb950] border-[#3fb950]/40 bg-[#122019]",
  },
  ICEBERG: {
    label: "冰山单",
    desc: "每次仅露出固定显示量，隐藏真实委托规模",
    accent: "text-[#bc8cff] border-[#8957e5]/40 bg-[#1c1630]",
  },
}

const ALGO_STATUS_META: Record<AlgoStatus, { label: string; color: string }> = {
  pending: { label: "待启动", color: "text-[#8b949e] bg-[#161b22] border-[#30363d]" },
  running: { label: "执行中", color: "text-[#e3b341] bg-[#1a1400] border-[#e3b341]/40" },
  completed: { label: "已完成", color: "text-[#3fb950] bg-[#122019] border-[#3fb950]/40" },
  cancelled: { label: "已撤销", color: "text-[#8b949e] bg-[#161b22] border-[#30363d]" },
  failed: { label: "失败", color: "text-[#f85149] bg-[#2a1515] border-[#f85149]/40" },
}

const CANCELLABLE: AlgoStatus[] = ["pending", "running"]

// ── 表单类型 ──────────────────────────────────────────────────
interface AlgoForm {
  symbol: string
  market: Market
  side: OrderSide
  algo_type: AlgoType
  total_qty: string
  order_type: OrderType
  limit_price: string
  duration_seconds: string
  slice_count: string
  display_qty: string
}

const DEFAULT_ALGO_FORM: AlgoForm = {
  symbol: "AAPL",
  market: "US",
  side: "BUY",
  algo_type: "TWAP",
  total_qty: "1000",
  order_type: "MARKET",
  limit_price: "",
  duration_seconds: "300",
  slice_count: "6",
  display_qty: "200",
}

// ── 提交面板 ──────────────────────────────────────────────────
function AlgoEntryPanel({
  form,
  onChange,
  onSubmit,
  isSubmitting,
}: {
  form: AlgoForm
  onChange: (key: keyof AlgoForm, val: string) => void
  onSubmit: (e: React.FormEvent) => void
  isSubmitting: boolean
}) {
  const isBuy = form.side === "BUY"
  const isIceberg = form.algo_type === "ICEBERG"
  const meta = ALGO_META[form.algo_type]

  return (
    <form onSubmit={onSubmit} className="card h-fit">
      <h2 className="text-sm font-semibold text-[#e6edf3] mb-4">🧊 高级算法拆单</h2>

      {/* 算法类型选择 */}
      <div className="grid grid-cols-3 gap-1.5 mb-3">
        {(Object.keys(ALGO_META) as AlgoType[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => onChange("algo_type", t)}
            className={`py-1.5 text-[11px] font-semibold rounded border transition-colors ${
              form.algo_type === t
                ? ALGO_META[t].accent
                : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <p className="text-[10px] text-[#6e7681] leading-relaxed mb-4">{meta.desc}</p>

      {/* 买卖方向 */}
      <div className="flex rounded-md overflow-hidden border border-[#30363d] mb-4">
        <button
          type="button"
          onClick={() => onChange("side", "BUY")}
          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
            isBuy
              ? "bg-[#1a3a24] text-[#3fb950] border-r border-[#3fb950]/30"
              : "text-[#6e7681] hover:text-[#e6edf3] border-r border-[#30363d]"
          }`}
        >
          买入 / Buy
        </button>
        <button
          type="button"
          onClick={() => onChange("side", "SELL")}
          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
            !isBuy ? "bg-[#2a1b1b] text-[#f85149]" : "text-[#6e7681] hover:text-[#e6edf3]"
          }`}
        >
          卖出 / Sell
        </button>
      </div>

      <div className="space-y-3">
        {/* 市场 + 标的 */}
        <div className="grid grid-cols-5 gap-2">
          <div className="col-span-2">
            <label className="label">市场</label>
            <select
              className="select w-full mt-1"
              value={form.market}
              onChange={(e) => onChange("market", e.target.value)}
            >
              {MARKET_OPTS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
          <div className="col-span-3">
            <label className="label">标的代码</label>
            <input
              className="input w-full mt-1 font-mono uppercase"
              value={form.symbol}
              onChange={(e) => onChange("symbol", e.target.value.toUpperCase())}
              placeholder="AAPL"
            />
          </div>
        </div>

        {/* 总量 + 子单类型 */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">总股数</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              min={1}
              value={form.total_qty}
              onChange={(e) => onChange("total_qty", e.target.value)}
            />
          </div>
          <div>
            <label className="label">子单类型</label>
            <select
              className="select w-full mt-1"
              value={form.order_type}
              onChange={(e) => onChange("order_type", e.target.value)}
            >
              <option value="MARKET">市价单</option>
              <option value="LIMIT">限价单</option>
            </select>
          </div>
        </div>

        {/* 限价 */}
        {form.order_type === "LIMIT" && (
          <div>
            <label className="label">限价</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              step="0.01"
              value={form.limit_price}
              onChange={(e) => onChange("limit_price", e.target.value)}
              placeholder="0.00"
            />
          </div>
        )}

        {/* 时长 + 切片数/显示量 */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">执行时长 (秒)</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              min={1}
              value={form.duration_seconds}
              onChange={(e) => onChange("duration_seconds", e.target.value)}
            />
          </div>
          {isIceberg ? (
            <div>
              <label className="label">每片显示量</label>
              <input
                className="input w-full mt-1 font-mono"
                type="number"
                min={1}
                value={form.display_qty}
                onChange={(e) => onChange("display_qty", e.target.value)}
              />
            </div>
          ) : (
            <div>
              <label className="label">切片数</label>
              <input
                className="input w-full mt-1 font-mono"
                type="number"
                min={1}
                max={100}
                value={form.slice_count}
                onChange={(e) => onChange("slice_count", e.target.value)}
              />
            </div>
          )}
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className={`w-full py-2.5 rounded-md text-sm font-semibold mt-2 transition-all disabled:opacity-50 ${
            isBuy
              ? "bg-[#1a3a24] text-[#3fb950] border border-[#3fb950]/40 hover:bg-[#1e4a2c]"
              : "bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/40 hover:bg-[#3a1e1e]"
          }`}
        >
          {isSubmitting ? <Spinner size="sm" className="mx-auto" /> : `提交${meta.label}`}
        </button>

        <p className="text-[10px] text-[#6e7681] text-center">
          父单将拆为多个子单，通过统一 OMS 路径逐片提交
        </p>
      </div>
    </form>
  )
}

// ── 进度条 ────────────────────────────────────────────────────
function AlgoProgressBar({ algo }: { algo: AlgoOrder }) {
  const pct = Math.min(algo.progress_pct, 100)
  const done = algo.status === "completed"
  const barColor = algo.status === "failed"
    ? "bg-[#f85149]"
    : done
      ? "bg-[#3fb950]"
      : "bg-[#e3b341]"
  return (
    <div className="w-full">
      <div className="h-1.5 rounded-full bg-[#21262d] overflow-hidden">
        <div className={`h-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between mt-1 text-[10px] text-[#6e7681] font-mono">
        <span>{algo.filled_qty.toLocaleString()}/{algo.total_qty.toLocaleString()} 股</span>
        <span>{pct.toFixed(1)}%</span>
      </div>
    </div>
  )
}

// ── 单个算法单卡片 ────────────────────────────────────────────
function AlgoCard({ algo, onCancel }: { algo: AlgoOrder; onCancel: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const isBuy = algo.side === "BUY"
  const meta = ALGO_META[algo.algo_type]
  const st = ALGO_STATUS_META[algo.status]
  const cancellable = CANCELLABLE.includes(algo.status)

  return (
    <div className="border-b border-[#21262d]/60 last:border-0 py-3 px-4">
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${meta.accent}`}>
          {algo.algo_type}
        </span>
        <span className="font-mono font-medium text-[#e6edf3]">{algo.symbol}</span>
        <span className={`text-xs font-semibold ${isBuy ? "text-[#3fb950]" : "text-[#f85149]"}`}>
          {isBuy ? "▲ 买入" : "▼ 卖出"}
        </span>
        <span className="text-xs text-[#8b949e] font-mono">
          {algo.total_qty.toLocaleString()} 股 · {algo.slice_count} 片
        </span>
        <span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${st.color}`}>
          {st.label}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[11px] text-[#58a6ff] hover:text-[#79b8ff]"
          >
            {expanded ? "收起" : "子单明细"}
          </button>
          {cancellable && (
            <button
              onClick={() => onCancel(algo.algo_id)}
              className="text-[11px] text-[#f85149] hover:text-[#ff7b72] border border-[#f85149]/30 hover:border-[#f85149]/60 rounded px-2 py-0.5"
            >
              撤销
            </button>
          )}
        </div>
      </div>

      <div className="mt-2.5">
        <AlgoProgressBar algo={algo} />
      </div>

      {algo.avg_fill_price != null && (
        <p className="mt-1.5 text-[10px] text-[#6e7681]">
          均价 <span className="font-mono text-[#8b949e]">{algo.avg_fill_price}</span>
        </p>
      )}

      {expanded && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs min-w-[520px]">
            <thead>
              <tr className="text-[#6e7681] border-b border-[#21262d]">
                <th className="text-left py-1.5 pr-3">#</th>
                <th className="text-right py-1.5 pr-3">股数</th>
                <th className="text-right py-1.5 pr-3">延迟(s)</th>
                <th className="text-right py-1.5 pr-3">已成交</th>
                <th className="text-right py-1.5 pr-3">成交价</th>
                <th className="text-left py-1.5 pr-3">状态</th>
              </tr>
            </thead>
            <tbody>
              {algo.slices.map((s) => (
                <tr key={s.index} className="border-b border-[#21262d]/40 last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-[#8b949e]">{s.index + 1}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{s.qty}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#6e7681]">
                    {s.delay_seconds.toFixed(0)}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#3fb950]">{s.filled_qty}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">
                    {s.avg_fill_price ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-[#8b949e]">
                    {s.error ? (
                      <span className="text-[#f85149]" title={s.error}>被拒</span>
                    ) : (
                      s.status
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── 主区块 ────────────────────────────────────────────────────
export function AdvancedOrderSection() {
  const { data: algos, isLoading } = useAlgoOrders()
  const { mutate: createAlgo, isPending } = useCreateAlgoOrder()
  const { mutate: cancelAlgo } = useCancelAlgoOrder()
  const { toast } = useToast()

  const [form, setForm] = useState<AlgoForm>(DEFAULT_ALGO_FORM)

  function handleChange(key: keyof AlgoForm, val: string) {
    setForm((prev) => ({ ...prev, [key]: val }))
  }

  function handleCancel(id: string) {
    cancelAlgo(id, {
      onSuccess: () => toast("算法单已撤销", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const totalQty = parseInt(form.total_qty, 10)
    if (!totalQty || totalQty <= 0) { toast("请输入有效总股数", "warning"); return }
    if (!form.symbol.trim()) { toast("请输入标的代码", "warning"); return }
    if (form.order_type === "LIMIT" && !form.limit_price) {
      toast("限价算法单需填写价格", "warning"); return
    }
    const isIceberg = form.algo_type === "ICEBERG"
    if (isIceberg && (!parseInt(form.display_qty, 10) || parseInt(form.display_qty, 10) <= 0)) {
      toast("冰山单需填写每片显示量", "warning"); return
    }

    createAlgo(
      {
        symbol: form.symbol.trim().toUpperCase(),
        market: form.market,
        side: form.side,
        algo_type: form.algo_type,
        total_qty: totalQty,
        order_type: form.order_type,
        limit_price: form.order_type === "LIMIT" ? parseFloat(form.limit_price) : null,
        duration_seconds: parseFloat(form.duration_seconds) || 300,
        slice_count: parseInt(form.slice_count, 10) || 6,
        display_qty: isIceberg ? parseInt(form.display_qty, 10) : null,
      },
      {
        onSuccess: () => toast(`${form.algo_type} 算法单已提交`, "success"),
        onError: (err) => toast(err.message, "error"),
      },
    )
  }

  const list = algos ?? []

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      <div className="xl:col-span-1">
        <AlgoEntryPanel
          form={form}
          onChange={handleChange}
          onSubmit={handleSubmit}
          isSubmitting={isPending}
        />
      </div>

      <div className="xl:col-span-3">
        <div className="flex items-center gap-2 mb-4 border-b border-[#21262d] pb-3">
          <h3 className="text-sm font-semibold text-[#e6edf3]">算法单执行监控</h3>
          <span className="ml-auto text-xs text-[#6e7681]">每 3 秒自动刷新</span>
        </div>

        <div className="card p-0">
          {isLoading && (
            <div className="flex justify-center py-12"><Spinner size="lg" /></div>
          )}
          {!isLoading && list.length === 0 && (
            <EmptyState
              title="暂无算法单"
              description="使用左侧面板提交 TWAP / VWAP / 冰山拆单，父单将自动拆为多个子单执行"
            />
          )}
          {list.map((a) => (
            <AlgoCard key={a.algo_id} algo={a} onCancel={handleCancel} />
          ))}
        </div>
      </div>
    </div>
  )
}
