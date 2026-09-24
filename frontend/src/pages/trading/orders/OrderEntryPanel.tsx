// 手动下单面板：买/卖方向、市场、标的、类型与数量，含 RBAC 下单权限校验。
import { Spinner } from "@/components/ui/Spinner"
import type { TradingMode } from "@/hooks/useBrokerConfig"
import type { Market, OrderSide, OrderType } from "@/types"
import { MARKET_CFGS } from "./config"

export interface NewOrderForm {
  symbol: string
  market: Market
  side: OrderSide
  qty: string
  order_type: OrderType
  limit_price: string
}

interface OrderEntryPanelProps {
  form: NewOrderForm
  onFormChange: (key: keyof NewOrderForm, val: string) => void
  onSubmit: (e: React.FormEvent) => void
  isSubmitting: boolean
  canTrade: boolean
  tradingMode?: TradingMode
}

export function OrderEntryPanel({
  form, onFormChange, onSubmit, isSubmitting, canTrade, tradingMode
}: OrderEntryPanelProps) {
  const isBuy = form.side === "BUY"
  const marketCfg = MARKET_CFGS.find((m) => m.value === form.market) ?? MARKET_CFGS[0]

  const gwType = tradingMode?.gateway_type ?? "local_paper"
  const gwBadge = {
    alpaca_paper: { label: "Alpaca Paper", color: "text-[#58a6ff] bg-[#1c2a3a] border-[#388bfd]/30" },
    alpaca_live:  { label: "Alpaca Live ⚠", color: "text-[#f85149] bg-[#2a1515] border-[#f85149]/30" },
    local_paper:  { label: "本地模拟", color: "text-[#8b949e] bg-[#161b22] border-[#30363d]" },
    unknown:      { label: "未连接", color: "text-[#6e7681] bg-[#0d1117] border-[#21262d]" },
  }[gwType]

  return (
    <form onSubmit={onSubmit} className="card h-fit">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-[#e6edf3]">📋 手动下单</h2>
        <span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${gwBadge.color}`}>
          {gwBadge.label}
        </span>
      </div>

      {/* 买卖方向 */}
      <div className="flex rounded-md overflow-hidden border border-[#30363d] mb-4">
        <button
          type="button"
          onClick={() => onFormChange("side", "BUY")}
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
          onClick={() => onFormChange("side", "SELL")}
          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
            !isBuy
              ? "bg-[#2a1b1b] text-[#f85149]"
              : "text-[#6e7681] hover:text-[#e6edf3]"
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
              onChange={(e) => onFormChange("market", e.target.value)}
            >
              {MARKET_CFGS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
          <div className="col-span-3">
            <label className="label">
              标的代码
              <span className="text-[10px] text-[#6e7681] ml-1">
                {form.market === "A" ? "如 000001" : form.market === "HK" ? "如 00700" : "如 AAPL"}
              </span>
            </label>
            <input
              className="input w-full mt-1 font-mono uppercase"
              value={form.symbol}
              onChange={(e) => onFormChange("symbol", e.target.value.toUpperCase())}
              placeholder={form.market === "A" ? "000001" : form.market === "HK" ? "00700" : "AAPL"}
            />
          </div>
        </div>

        {/* 类型 + 数量 */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">订单类型</label>
            <select
              className="select w-full mt-1"
              value={form.order_type}
              onChange={(e) => onFormChange("order_type", e.target.value)}
            >
              <option value="MARKET">市价单</option>
              <option value="LIMIT">限价单</option>
            </select>
          </div>
          <div>
            <label className="label">数量 (股)</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              value={form.qty}
              onChange={(e) => onFormChange("qty", e.target.value)}
              min={1}
              step={form.market === "A" ? 100 : 1}
            />
          </div>
        </div>

        {/* 限价 */}
        {form.order_type === "LIMIT" && (
          <div>
            <label className="label">限价 ({marketCfg.currency})</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              step="0.01"
              value={form.limit_price}
              onChange={(e) => onFormChange("limit_price", e.target.value)}
              placeholder="0.00"
            />
          </div>
        )}

        {/* 提交按钮 — Viewer 角色无交易权限，置灰 */}
        <button
          type="submit"
          disabled={isSubmitting || !canTrade}
          title={canTrade ? undefined : "当前角色（只读用户）无下单权限，请联系管理员升级为交易员"}
          className={`w-full py-2.5 rounded-md text-sm font-semibold mt-2 transition-all ${
            isBuy
              ? "bg-[#1a3a24] text-[#3fb950] border border-[#3fb950]/40 hover:bg-[#1e4a2c] hover:border-[#3fb950]/60"
              : "bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/40 hover:bg-[#3a1e1e] hover:border-[#f85149]/60"
          } disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          {isSubmitting ? (
            <Spinner size="sm" className="mx-auto" />
          ) : !canTrade ? (
            `无下单权限`
          ) : isBuy ? (
            `确认买入`
          ) : (
            `确认卖出`
          )}
        </button>

        {!canTrade && (
          <p className="text-[10px] text-[#e3b341] text-center bg-[#272111]/60 border border-[#e3b341]/30 rounded px-2 py-1.5">
            只读用户不可下单 · 交易操作需交易员及以上角色
          </p>
        )}

        <p className="text-[10px] text-[#6e7681] text-center">
          {gwType === "local_paper"
            ? "本地纸面交易 · 市价单立即模拟成交"
            : gwType === "alpaca_paper"
              ? "Alpaca Paper Trading · 订单发送至 Alpaca 模拟账户"
              : gwType === "alpaca_live"
                ? "⚠ Alpaca 实盘 · 订单使用真实资金执行"
                : "OMS 未连接"}
        </p>
      </div>
    </form>
  )
}
