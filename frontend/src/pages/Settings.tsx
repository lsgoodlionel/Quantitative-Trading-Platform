import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { useAuthStore } from "@/stores/auth"
import { Spinner } from "@/components/ui/Spinner"
import {
  useBrokerConfig,
  useSaveAlpacaConfig,
  useDeleteAlpacaConfig,
  useTestAlpacaConnection,
} from "@/hooks/useBrokerConfig"

// ── Layout helpers ────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold text-[#e6edf3] mb-4">{title}</h2>
      {children}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-[#21262d]/50 last:border-0 text-sm">
      <span className="text-[#8b949e]">{label}</span>
      <span className="font-mono text-[#e6edf3]">{value}</span>
    </div>
  )
}

// ── Alpaca config form ────────────────────────────────────────────────────────

const ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
const ALPACA_LIVE_URL  = "https://api.alpaca.markets"

function AlpacaConfigSection() {
  const { data: config, isLoading } = useBrokerConfig()
  const save   = useSaveAlpacaConfig()
  const del    = useDeleteAlpacaConfig()
  const test   = useTestAlpacaConnection()

  const alpaca = config?.alpaca

  const [editing, setEditing]     = useState(false)
  const [apiKey, setApiKey]       = useState("")
  const [apiSecret, setApiSecret] = useState("")
  const [paperMode, setPaperMode] = useState(true)
  const [testResult, setTestResult] = useState<string | null>(null)

  function startEdit() {
    setApiKey("")
    setApiSecret("")
    setPaperMode(alpaca?.paper_mode ?? true)
    setTestResult(null)
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setTestResult(null)
  }

  async function handleSave() {
    if (!apiKey || !apiSecret) return
    await save.mutateAsync({
      api_key: apiKey,
      api_secret: apiSecret,
      base_url: paperMode ? ALPACA_PAPER_URL : ALPACA_LIVE_URL,
      paper_mode: paperMode,
    })
    setEditing(false)
    setTestResult(null)
  }

  async function handleDelete() {
    if (!confirm("确认清除 Alpaca 配置？")) return
    await del.mutateAsync()
    setTestResult(null)
  }

  async function handleTest() {
    setTestResult(null)
    const r = await test.mutateAsync()
    if (r.ok) {
      setTestResult(`✓ 连接成功 · 账户 ${r.account_id} · 购买力 $${r.buying_power?.toLocaleString()}`)
    } else {
      setTestResult(`✗ ${r.error ?? "连接失败"}`)
    }
  }

  if (isLoading) return <div className="py-4 flex justify-center"><Spinner /></div>

  const isConfigured = alpaca?.configured ?? false
  const isSaving = save.isPending
  const isDeleting = del.isPending
  const isTesting = test.isPending

  return (
    <div className="space-y-4">
      {/* Status row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`w-2 h-2 rounded-full ${isConfigured ? "bg-[#3fb950]" : "bg-[#6e7681]"}`} />
          <span className="text-sm text-[#e6edf3]">
            {isConfigured ? "已配置" : "未配置（使用演示数据）"}
          </span>
          {isConfigured && alpaca?.key_hint && (
            <span className="font-mono text-xs text-[#6e7681] bg-[#1c2128] px-2 py-0.5 rounded">
              {alpaca.key_hint}
            </span>
          )}
          {isConfigured && (
            <span className={`text-xs px-2 py-0.5 rounded border ${alpaca?.paper_mode ? "border-[#388bfd]/40 text-[#58a6ff]" : "border-[#f85149]/40 text-[#f85149]"}`}>
              {alpaca?.paper_mode ? "Paper" : "Live"}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          {isConfigured && !editing && (
            <button
              className="btn btn-ghost text-xs py-1 px-3"
              onClick={handleTest}
              disabled={isTesting}
            >
              {isTesting ? <Spinner size="sm" /> : "测试连接"}
            </button>
          )}
          {!editing && (
            <button className="btn btn-ghost text-xs py-1 px-3" onClick={startEdit}>
              {isConfigured ? "修改密钥" : "配置密钥"}
            </button>
          )}
          {isConfigured && !editing && (
            <button
              className="btn btn-danger text-xs py-1 px-3"
              onClick={handleDelete}
              disabled={isDeleting}
            >
              {isDeleting ? <Spinner size="sm" /> : "清除"}
            </button>
          )}
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <p className={`text-xs px-3 py-2 rounded border ${testResult.startsWith("✓") ? "text-[#3fb950] bg-[#1a2a1a] border-[#3fb950]/30" : "text-[#f85149] bg-[#2a1b1b] border-[#f85149]/30"}`}>
          {testResult}
        </p>
      )}

      {/* Edit form */}
      {editing && (
        <div className="border border-[#30363d] rounded-lg p-4 space-y-4 bg-[#0d1117]">
          <div className="grid grid-cols-1 gap-4">
            {/* API Key */}
            <div>
              <label className="label">API Key</label>
              <input
                type="text"
                className="input w-full mt-1 font-mono text-sm"
                placeholder="PKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value.trim())}
                autoComplete="off"
                spellCheck={false}
              />
              <p className="text-xs text-[#6e7681] mt-1">
                在{" "}
                <a
                  href="https://app.alpaca.markets/paper-trading"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#58a6ff] hover:underline"
                >
                  Alpaca 控制台
                </a>{" "}
                → Paper Trading → API Keys 中生成
              </p>
            </div>

            {/* Secret Key */}
            <div>
              <label className="label">Secret Key</label>
              <input
                type="password"
                className="input w-full mt-1 font-mono text-sm"
                placeholder="••••••••••••••••••••••••••••••••"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value.trim())}
                autoComplete="new-password"
              />
            </div>

            {/* Paper / Live toggle */}
            <div>
              <label className="label mb-2">交易模式</label>
              <div className="flex gap-3 mt-1">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="paper_mode"
                    checked={paperMode}
                    onChange={() => setPaperMode(true)}
                    className="accent-[#58a6ff]"
                  />
                  <span className="text-sm text-[#e6edf3]">
                    模拟盘 <span className="text-[#6e7681] text-xs">(Paper Trading)</span>
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="paper_mode"
                    checked={!paperMode}
                    onChange={() => setPaperMode(false)}
                    className="accent-[#58a6ff]"
                  />
                  <span className="text-sm text-[#e6edf3]">
                    实盘 <span className="text-[#f85149] text-xs">(Live Trading — 真实资金)</span>
                  </span>
                </label>
              </div>
            </div>
          </div>

          {/* Error */}
          {save.error && (
            <p className="text-xs text-[#f85149] bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
              {save.error.message}
            </p>
          )}

          {/* Actions */}
          <div className="flex gap-3 justify-end pt-2">
            <button className="btn btn-ghost text-sm" onClick={cancelEdit} disabled={isSaving}>
              取消
            </button>
            <button
              className="btn btn-primary text-sm"
              onClick={handleSave}
              disabled={isSaving || !apiKey || !apiSecret}
            >
              {isSaving ? <Spinner size="sm" className="mx-auto" /> : "保存"}
            </button>
          </div>
        </div>
      )}

      {/* Guide when not configured */}
      {!isConfigured && !editing && (
        <div className="text-xs text-[#6e7681] leading-relaxed space-y-1 border-t border-[#21262d]/50 pt-3">
          <p>· 未配置时，行情和回测使用<span className="text-[#e6edf3]">合成演示数据</span>（GBM 模拟）</p>
          <p>· 配置 Alpaca Paper Trading 后，自动切换为真实美股行情</p>
          <p>· 注册地址（免费）：<a href="https://alpaca.markets" target="_blank" rel="noopener noreferrer" className="text-[#58a6ff] hover:underline">alpaca.markets</a></p>
        </div>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

const VERSION_INFO = [
  { label: "平台版本", value: "QuantBot v0.1.0" },
  { label: "后端框架", value: "FastAPI + Python 3.11" },
  { label: "数据库", value: "TimescaleDB + PostgreSQL + Redis" },
  { label: "前端框架", value: "React 18 + TypeScript + Vite" },
  { label: "支持市场", value: "US · HK · A (演示)" },
]

export function Settings() {
  const user   = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  return (
    <AppShell title="系统设置">
      <div className="max-w-2xl space-y-6">
        {/* Account */}
        <Section title="账号信息">
          <InfoRow label="当前用户" value={user ?? "—"} />
          <div className="pt-3">
            <button className="btn btn-danger text-sm" onClick={logout}>退出登录</button>
          </div>
        </Section>

        {/* Alpaca broker config */}
        <Section title="美股通道 — Alpaca Markets">
          <AlpacaConfigSection />
        </Section>

        {/* Platform */}
        <Section title="平台信息">
          {VERSION_INFO.map(({ label, value }) => (
            <InfoRow key={label} label={label} value={value} />
          ))}
        </Section>

        {/* Data strategy */}
        <Section title="数据策略">
          {[
            { label: "行情刷新", value: "10 秒" },
            { label: "订单轮询", value: "5 秒" },
            { label: "风控汇总", value: "15 秒" },
            { label: "数据存储", value: "TimescaleDB 超表" },
            { label: "缓存层",   value: "Redis 流 + Hash" },
            { label: "行情兜底", value: "GBM 合成数据（无 API Key 时）" },
          ].map(({ label, value }) => (
            <InfoRow key={label} label={label} value={value} />
          ))}
        </Section>
      </div>
    </AppShell>
  )
}
