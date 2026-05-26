import { AppShell } from "@/components/layout/AppShell"
import { useAuthStore } from "@/stores/auth"

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
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

const VERSION_INFO = [
  { label: "平台版本", value: "QuantBot v0.1.0" },
  { label: "后端框架", value: "FastAPI + Python 3.11" },
  { label: "数据库", value: "TimescaleDB + PostgreSQL + Redis" },
  { label: "前端框架", value: "React 18 + TypeScript + Vite" },
  { label: "支持市场", value: "US (Alpaca) · HK (Futu)" },
]

const BROKER_INFO = [
  { label: "美股通道", value: "Alpaca Markets" },
  { label: "港股通道", value: "Futu OpenAPI" },
  { label: "A股通道", value: "计划中 (akshare)" },
  { label: "美股佣金", value: "零佣金 + SEC/FINRA 费" },
  { label: "港股佣金", value: "0.03% + 印花税" },
]

export function Settings() {
  const user = useAuthStore((s) => s.user)
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

        {/* Platform */}
        <Section title="平台信息">
          {VERSION_INFO.map(({ label, value }) => (
            <InfoRow key={label} label={label} value={value} />
          ))}
        </Section>

        {/* Broker */}
        <Section title="交易通道">
          {BROKER_INFO.map(({ label, value }) => (
            <InfoRow key={label} label={label} value={value} />
          ))}
        </Section>

        {/* Data */}
        <Section title="数据策略">
          {[
            { label: "行情刷新", value: "10 秒" },
            { label: "订单轮询", value: "5 秒" },
            { label: "风控汇总", value: "15 秒" },
            { label: "数据存储", value: "TimescaleDB 超表" },
            { label: "缓存层", value: "Redis 流 + Hash" },
          ].map(({ label, value }) => (
            <InfoRow key={label} label={label} value={value} />
          ))}
        </Section>
      </div>
    </AppShell>
  )
}
