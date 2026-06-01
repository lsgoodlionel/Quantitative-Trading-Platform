import { useState } from "react"
import { Link } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useAlerts, useCreateAlert, useDeleteAlert, useToggleAlert,
  useCheckAlerts, useResetAlert,
  type AlertCondition, type PriceAlert,
} from "@/hooks/useAlerts"

// ── Constants ─────────────────────────────────────────────────────

const CONDITION_CFG: Record<AlertCondition, { label: string; color: string; desc: string }> = {
  above:      { label: "高于",   color: "#3fb950", desc: "价格高于阈值时触发" },
  below:      { label: "低于",   color: "#f85149", desc: "价格低于阈值时触发" },
  pct_change: { label: "变动%",  color: "#e3b341", desc: "价格偏离基准超过阈值%时触发" },
}

const MARKET_LABELS: Record<string, string> = { US: "🇺🇸 美股", HK: "🇭🇰 港股", A: "🇨🇳 A股" }

// ── Sub-components ────────────────────────────────────────────────

function AlertBadge({ condition, is_triggered, is_active }: Pick<PriceAlert, "condition" | "is_triggered" | "is_active">) {
  if (is_triggered) {
    return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-[#f85149]/15 text-[#f85149] border border-[#f85149]/30">🔔 已触发</span>
  }
  if (!is_active) {
    return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-[#21262d] text-[#6e7681] border border-[#30363d]">暂停</span>
  }
  const cfg = CONDITION_CFG[condition]
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border"
      style={{ color: cfg.color, borderColor: `${cfg.color}40`, background: `${cfg.color}15` }}>
      监控中
    </span>
  )
}

function AlertRow({ alert }: { alert: PriceAlert }) {
  const { mutate: del, isPending: deleting } = useDeleteAlert()
  const { mutate: toggle, isPending: toggling } = useToggleAlert()
  const { mutate: reset, isPending: resetting } = useResetAlert()
  const { toast } = useToast()

  const condCfg = CONDITION_CFG[alert.condition]
  const thresholdLabel = alert.condition === "pct_change"
    ? `${alert.threshold}%`
    : `$${alert.threshold.toLocaleString()}`

  function handleDelete() {
    if (!confirm(`删除 ${alert.symbol} 预警？`)) return
    del(alert.id, { onError: (e) => toast(e.message, "error") })
  }

  return (
    <div className={`rounded-xl border p-4 transition-colors ${
      alert.is_triggered
        ? "bg-[#2a1b1b] border-[#f85149]/30"
        : alert.is_active
          ? "bg-[#161b22] border-[#21262d] hover:border-[#30363d]"
          : "bg-[#161b22] border-[#21262d] opacity-60"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Header row */}
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            <span className="font-mono font-bold text-[#e6edf3]">{alert.symbol}</span>
            <span className="text-xs text-[#6e7681]">{MARKET_LABELS[alert.market] ?? alert.market}</span>
            <AlertBadge condition={alert.condition} is_triggered={alert.is_triggered} is_active={alert.is_active} />
          </div>

          {/* Condition */}
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium" style={{ color: condCfg.color }}>
              {condCfg.label}
            </span>
            <span className="font-mono text-sm font-semibold text-[#e6edf3]">{thresholdLabel}</span>
            {alert.condition === "pct_change" && alert.base_price != null && (
              <span className="text-xs text-[#6e7681]">基准 ${alert.base_price.toLocaleString()}</span>
            )}
          </div>

          {/* Note */}
          {alert.note && (
            <p className="text-xs text-[#8b949e] truncate">{alert.note}</p>
          )}

          {/* Timestamps */}
          <div className="flex gap-3 mt-1.5">
            <span className="text-[10px] text-[#6e7681]">
              创建: {new Date(alert.created_at).toLocaleString("zh-CN")}
            </span>
            {alert.triggered_at && (
              <span className="text-[10px] text-[#f85149]">
                触发: {new Date(alert.triggered_at).toLocaleString("zh-CN")}
              </span>
            )}
          </div>

          {/* 触发后操作引导 */}
          {alert.is_triggered && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                to={`/orders`}
                className="px-3 py-1.5 rounded text-[10px] font-medium border border-[#3fb950]/30 text-[#3fb950] bg-[#162a1e] hover:bg-[#3fb950]/15 transition-colors">
                📋 立即下单 {alert.symbol}
              </Link>
              <Link
                to={`/market?symbol=${alert.symbol}&market=${alert.market}`}
                className="px-3 py-1.5 rounded text-[10px] border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
                📈 查看行情
              </Link>
              <Link
                to={`/backtest?symbol=${alert.symbol}&market=${alert.market}`}
                className="px-3 py-1.5 rounded text-[10px] border border-[#6e7681]/30 text-[#8b949e] hover:bg-[#21262d] transition-colors">
                🔬 快速回测
              </Link>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5 shrink-0">
          {alert.is_triggered && (
            <button
              onClick={() => reset(alert.id)}
              disabled={resetting}
              className="px-2.5 py-1 rounded text-xs border border-[#58a6ff]/40 text-[#58a6ff] hover:bg-[#1f6feb]/20 transition-colors"
            >
              重置
            </button>
          )}
          {!alert.is_triggered && (
            <button
              onClick={() => toggle({ id: alert.id, is_active: !alert.is_active })}
              disabled={toggling}
              className="px-2.5 py-1 rounded text-xs border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#6e7681] transition-colors"
            >
              {alert.is_active ? "暂停" : "启用"}
            </button>
          )}
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="px-2.5 py-1 rounded text-xs border border-[#f85149]/30 text-[#f85149] hover:bg-[#f85149]/10 transition-colors"
          >
            删除
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Create Alert Form ─────────────────────────────────────────────

interface CreateFormProps {
  onClose: () => void
}

function CreateAlertForm({ onClose }: CreateFormProps) {
  const { mutate: create, isPending } = useCreateAlert()
  const { toast } = useToast()

  const [symbol,    setSymbol]    = useState("")
  const [market,    setMarket]    = useState("US")
  const [condition, setCondition] = useState<AlertCondition>("above")
  const [threshold, setThreshold] = useState("")
  const [basePrice, setBasePrice] = useState("")
  const [note,      setNote]      = useState("")

  function handleSubmit() {
    if (!symbol.trim()) { toast("请输入标的代码", "warning"); return }
    const th = parseFloat(threshold)
    if (isNaN(th) || th <= 0) { toast("请输入有效的阈值", "warning"); return }

    create({
      symbol: symbol.trim().toUpperCase(),
      market,
      condition,
      threshold: th,
      base_price: condition === "pct_change" && basePrice ? parseFloat(basePrice) : null,
      note: note.trim(),
    }, {
      onSuccess: () => { toast("预警创建成功", "success"); onClose() },
      onError:   (e) => toast(e.message, "error"),
    })
  }

  return (
    <div className="card border border-[#58a6ff]/20 bg-[#0d1117]">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">创建价格预警</h3>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        {/* Symbol */}
        <div>
          <label className="label block mb-1">标的代码</label>
          <input
            className="input w-full font-mono uppercase"
            placeholder="AAPL / 00700 / 600519"
            value={symbol}
            onChange={e => setSymbol(e.target.value)}
          />
        </div>

        {/* Market */}
        <div>
          <label className="label block mb-1">市场</label>
          <div className="flex gap-2">
            {["US","HK","A"].map(m => (
              <button key={m} onClick={() => setMarket(m)}
                className={`flex-1 py-1.5 rounded text-sm font-medium border transition-colors ${
                  market === m
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                    : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                }`}>{m}</button>
            ))}
          </div>
        </div>

        {/* Condition */}
        <div>
          <label className="label block mb-1">触发条件</label>
          <select className="select w-full" value={condition}
            onChange={e => setCondition(e.target.value as AlertCondition)}>
            {(Object.entries(CONDITION_CFG) as [AlertCondition, typeof CONDITION_CFG[AlertCondition]][]).map(
              ([k, v]) => <option key={k} value={k}>{v.label} — {v.desc}</option>
            )}
          </select>
        </div>

        {/* Threshold */}
        <div>
          <label className="label block mb-1">
            {condition === "pct_change" ? "变动阈值 (%)" : "价格阈值"}
          </label>
          <input
            className="input w-full font-mono"
            placeholder={condition === "pct_change" ? "5.0 (即5%)" : "150.00"}
            value={threshold}
            onChange={e => setThreshold(e.target.value)}
          />
        </div>

        {/* Base price (pct_change only) */}
        {condition === "pct_change" && (
          <div>
            <label className="label block mb-1">基准价格（可选）</label>
            <input
              className="input w-full font-mono"
              placeholder="留空则以当前价格为基准"
              value={basePrice}
              onChange={e => setBasePrice(e.target.value)}
            />
          </div>
        )}

        {/* Note */}
        <div className={condition === "pct_change" ? "" : "sm:col-span-2"}>
          <label className="label block mb-1">备注（可选）</label>
          <input
            className="input w-full"
            placeholder="例: 突破阻力位"
            value={note}
            onChange={e => setNote(e.target.value)}
            maxLength={200}
          />
        </div>
      </div>

      <div className="flex gap-2">
        <button className="btn btn-primary" onClick={handleSubmit} disabled={isPending}>
          {isPending ? <Spinner size="sm" className="mx-auto" /> : "创建预警"}
        </button>
        <button className="btn btn-secondary" onClick={onClose}>取消</button>
      </div>
    </div>
  )
}

// ── Check Prices Panel ────────────────────────────────────────────

function CheckPanel({ alertCount }: { alertCount: number }) {
  const { mutate: check, isPending, data: result } = useCheckAlerts()
  const { toast } = useToast()

  const [pricesRaw, setPricesRaw] = useState(
    "AAPL US 182.50\nTSLA US 248.00\n00700 HK 326.40"
  )

  function handleCheck() {
    const prices = pricesRaw
      .split("\n")
      .map(line => line.trim().split(/\s+/))
      .filter(parts => parts.length === 3)
      .map(([symbol, market, priceStr]) => ({
        symbol: symbol.toUpperCase(),
        market: market.toUpperCase(),
        price:  parseFloat(priceStr),
      }))
      .filter(p => !isNaN(p.price))

    if (prices.length === 0) {
      toast("请输入至少一个有效价格（格式: 代码 市场 价格）", "warning")
      return
    }
    check({ prices }, {
      onError: (e) => toast(e.message, "error"),
    })
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[#e6edf3]">价格检查</h3>
        <span className="text-xs text-[#6e7681]">{alertCount} 个预警监控中</span>
      </div>

      <p className="text-xs text-[#8b949e] mb-2">每行格式: <code className="text-[#79c0ff]">代码 市场 价格</code></p>
      <textarea
        className="input w-full font-mono text-xs mb-3 resize-none"
        rows={5}
        value={pricesRaw}
        onChange={e => setPricesRaw(e.target.value)}
        placeholder={"AAPL US 182.50\nTSLA US 248.00\n00700 HK 326.40"}
      />

      <button className="btn btn-primary w-full" onClick={handleCheck} disabled={isPending || alertCount === 0}>
        {isPending ? <Spinner size="sm" className="mx-auto" /> : "检查所有预警"}
      </button>

      {result && (
        <div className={`mt-3 p-3 rounded-lg border text-sm ${
          result.count > 0
            ? "bg-[#2a1b1b] border-[#f85149]/30 text-[#f85149]"
            : "bg-[#162a1e] border-[#3fb950]/30 text-[#3fb950]"
        }`}>
          {result.count > 0 ? (
            <>
              <p className="font-semibold mb-1">🔔 {result.count} 个预警已触发！</p>
              {result.triggered.map(a => (
                <p key={a.id} className="text-xs">
                  {a.symbol} {CONDITION_CFG[a.condition].label} {a.threshold}
                </p>
              ))}
            </>
          ) : (
            <p>✓ 所有预警均未触发</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Alerts Page ───────────────────────────────────────────────────

export function AlertsPage() {
  const { data: alerts = [], isLoading } = useAlerts()
  const [showCreate, setShowCreate] = useState(false)

  const active   = alerts.filter(a => a.is_active && !a.is_triggered)
  const triggered = alerts.filter(a => a.is_triggered)
  const paused   = alerts.filter(a => !a.is_active && !a.is_triggered)

  return (
    <AppShell title="价格预警" help={PAGE_HELP.alerts}>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Left: Create + Check */}
        <div className="xl:col-span-1 space-y-4">
          {/* Summary stats */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "监控中", value: active.length,    color: "#3fb950" },
              { label: "已触发", value: triggered.length, color: "#f85149" },
              { label: "已暂停", value: paused.length,    color: "#6e7681" },
            ].map(({ label, value, color }) => (
              <div key={label} className="card text-center py-3">
                <p className="font-mono text-xl font-bold" style={{ color }}>{value}</p>
                <p className="text-xs text-[#6e7681] mt-1">{label}</p>
              </div>
            ))}
          </div>

          {/* Create button / form */}
          {!showCreate ? (
            <button
              className="btn btn-primary w-full"
              onClick={() => setShowCreate(true)}
            >
              + 新建预警
            </button>
          ) : (
            <CreateAlertForm onClose={() => setShowCreate(false)} />
          )}

          {/* Price check */}
          <CheckPanel alertCount={active.length} />
        </div>

        {/* Right: Alert list */}
        <div className="xl:col-span-2">
          {isLoading ? (
            <div className="card flex items-center justify-center h-32">
              <Spinner size="lg" />
            </div>
          ) : alerts.length === 0 ? (
            <div className="card flex flex-col items-center justify-center h-48 text-[#6e7681] gap-3">
              <span className="text-4xl">🔕</span>
              <p className="text-sm">暂无预警，点击"新建预警"开始监控</p>
            </div>
          ) : (
            <div className="space-y-3">
              {/* Triggered first */}
              {triggered.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[#f85149] uppercase tracking-wider mb-2 px-1">
                    已触发 ({triggered.length})
                  </p>
                  <div className="space-y-2">
                    {triggered.map(a => <AlertRow key={a.id} alert={a} />)}
                  </div>
                </div>
              )}

              {/* Active */}
              {active.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[#3fb950] uppercase tracking-wider mb-2 px-1">
                    监控中 ({active.length})
                  </p>
                  <div className="space-y-2">
                    {active.map(a => <AlertRow key={a.id} alert={a} />)}
                  </div>
                </div>
              )}

              {/* Paused */}
              {paused.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[#6e7681] uppercase tracking-wider mb-2 px-1">
                    已暂停 ({paused.length})
                  </p>
                  <div className="space-y-2">
                    {paused.map(a => <AlertRow key={a.id} alert={a} />)}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  )
}
