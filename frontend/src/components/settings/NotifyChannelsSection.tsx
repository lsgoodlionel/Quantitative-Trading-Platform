import { useEffect, useMemo, useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  useNotifyConfig,
  useUpdateNotifyConfig,
  useTestChannel,
} from "@/hooks/useNotifyConfig"
import type {
  ChannelConfig,
  ChannelStatus,
  ChannelType,
  NotifyConfig,
  NotifyConfigStatus,
  NotifyEventType,
  WebhookFormat,
} from "@/types"

const EVENT_LABELS: Record<NotifyEventType, string> = {
  trade_fill: "成交",
  order_reject: "订单拒绝",
  pnl_update: "盈亏更新",
  position: "持仓变动",
  daily_summary: "每日汇总",
  risk_alert: "风控告警",
  protection: "防护熔断",
}

const ALL_EVENTS = Object.keys(EVENT_LABELS) as NotifyEventType[]
const WEBHOOK_FORMATS: WebhookFormat[] = ["json", "form", "raw"]

/** 将脱敏状态映射为可编辑的请求模型（密钥留空 = 保持原值）。 */
function statusToConfig(status: ChannelStatus): ChannelConfig {
  return {
    id: status.id,
    type: status.type,
    name: status.name,
    enabled: status.enabled,
    events: status.events,
    telegram: status.telegram
      ? { bot_token: "", chat_id: status.telegram.chat_id, parse_mode: status.telegram.parse_mode }
      : null,
    webhook: status.webhook
      ? {
          url: status.webhook.url,
          format: status.webhook.format,
          timeout_seconds: status.webhook.timeout_seconds,
          retries: status.webhook.retries,
          retry_delay_seconds: status.webhook.retry_delay_seconds,
          secret_header: null,
          secret_value: "",
        }
      : null,
  }
}

function statusToModel(status: NotifyConfigStatus): NotifyConfig {
  return {
    is_active: status.is_active,
    channels: status.channels.map(statusToConfig),
    min_pnl_notify_abs: status.min_pnl_notify_abs,
    daily_summary_time: status.daily_summary_time,
  }
}

function newChannel(type: ChannelType): ChannelConfig {
  const id = crypto.randomUUID()
  return {
    id,
    type,
    name: type === "telegram" ? "Telegram 渠道" : "Webhook 渠道",
    enabled: true,
    events: [],
    telegram: type === "telegram" ? { bot_token: "", chat_id: "", parse_mode: "HTML" } : null,
    webhook:
      type === "webhook"
        ? {
            url: "",
            format: "json",
            timeout_seconds: 10,
            retries: 2,
            retry_delay_seconds: 1.0,
            secret_header: null,
            secret_value: "",
          }
        : null,
  }
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${on ? "bg-[#3fb950]" : "bg-[#30363d]"}`}
      aria-label={on ? "禁用" : "启用"}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${on ? "translate-x-4" : "translate-x-0"}`}
      />
    </button>
  )
}

interface ChannelCardProps {
  channel: ChannelConfig
  persisted: boolean
  onChange: (updated: ChannelConfig) => void
  onRemove: () => void
  onTest: () => void
  testing: boolean
  testResult?: string
}

function ChannelCard({ channel, persisted, onChange, onRemove, onTest, testing, testResult }: ChannelCardProps) {
  function toggleEvent(evt: NotifyEventType) {
    const has = channel.events.includes(evt)
    const events = has ? channel.events.filter((e) => e !== evt) : [...channel.events, evt]
    onChange({ ...channel, events })
  }

  return (
    <div className="border border-[#30363d] rounded-lg p-4 space-y-3 bg-[#0d1117]">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Toggle on={channel.enabled} onClick={() => onChange({ ...channel, enabled: !channel.enabled })} />
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${channel.type === "telegram" ? "border-[#388bfd]/40 text-[#58a6ff]" : "border-[#e3b341]/40 text-[#e3b341]"}`}>
          {channel.type === "telegram" ? "Telegram" : "Webhook"}
        </span>
        <input
          className="input flex-1 text-sm"
          value={channel.name}
          placeholder="渠道名称"
          onChange={(e) => onChange({ ...channel, name: e.target.value })}
        />
        <button className="btn btn-danger text-xs py-1 px-2" onClick={onRemove}>删除</button>
      </div>

      {/* Type-specific fields */}
      {channel.type === "telegram" && channel.telegram && (
        <div className="grid grid-cols-1 gap-2">
          <div>
            <label className="label text-xs">Bot Token</label>
            <input
              type="password"
              className="input w-full mt-1 font-mono text-xs"
              placeholder={persisted ? "留空 = 保持原值" : "123456:ABC-DEF..."}
              autoComplete="new-password"
              value={channel.telegram.bot_token}
              onChange={(e) => onChange({ ...channel, telegram: { ...channel.telegram!, bot_token: e.target.value.trim() } })}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label text-xs">Chat ID</label>
              <input
                className="input w-full mt-1 font-mono text-xs"
                value={channel.telegram.chat_id}
                onChange={(e) => onChange({ ...channel, telegram: { ...channel.telegram!, chat_id: e.target.value.trim() } })}
              />
            </div>
            <div>
              <label className="label text-xs">格式</label>
              <select
                className="input w-full mt-1 text-xs"
                value={channel.telegram.parse_mode}
                onChange={(e) => onChange({ ...channel, telegram: { ...channel.telegram!, parse_mode: e.target.value as "HTML" | "Markdown" } })}
              >
                <option value="HTML">HTML</option>
                <option value="Markdown">Markdown</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {channel.type === "webhook" && channel.webhook && (
        <div className="space-y-2">
          <div>
            <label className="label text-xs">URL</label>
            <input
              className="input w-full mt-1 font-mono text-xs"
              placeholder="https://..."
              value={channel.webhook.url}
              onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, url: e.target.value.trim() } })}
            />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="label text-xs">格式</label>
              <select
                className="input w-full mt-1 text-xs"
                value={channel.webhook.format}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, format: e.target.value as WebhookFormat } })}
              >
                {WEBHOOK_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>
            <div>
              <label className="label text-xs">超时(秒)</label>
              <input type="number" className="input w-full mt-1 text-xs font-mono" value={channel.webhook.timeout_seconds}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, timeout_seconds: parseInt(e.target.value) || 10 } })} />
            </div>
            <div>
              <label className="label text-xs">重试次数</label>
              <input type="number" className="input w-full mt-1 text-xs font-mono" value={channel.webhook.retries}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, retries: parseInt(e.target.value) || 0 } })} />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="label text-xs">退避基数(秒)</label>
              <input type="number" step={0.5} className="input w-full mt-1 text-xs font-mono" value={channel.webhook.retry_delay_seconds}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, retry_delay_seconds: parseFloat(e.target.value) || 0 } })} />
            </div>
            <div>
              <label className="label text-xs">密钥头 (可选)</label>
              <input className="input w-full mt-1 text-xs font-mono" placeholder="X-Signature"
                value={channel.webhook.secret_header ?? ""}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, secret_header: e.target.value || null } })} />
            </div>
            <div>
              <label className="label text-xs">密钥值</label>
              <input type="password" className="input w-full mt-1 text-xs font-mono" autoComplete="new-password"
                placeholder={persisted ? "留空 = 保持原值" : "shared secret"}
                value={channel.webhook.secret_value ?? ""}
                onChange={(e) => onChange({ ...channel, webhook: { ...channel.webhook!, secret_value: e.target.value } })} />
            </div>
          </div>
        </div>
      )}

      {/* Event subscriptions */}
      <div>
        <label className="label text-xs mb-1">订阅事件</label>
        <div className="flex flex-wrap gap-1.5 mt-1">
          {ALL_EVENTS.map((evt) => {
            const on = channel.events.includes(evt)
            return (
              <button
                key={evt}
                onClick={() => toggleEvent(evt)}
                className={`text-[11px] px-2 py-0.5 rounded border transition-colors ${
                  on ? "border-[#3fb950]/40 text-[#3fb950] bg-[#162a1e]/40" : "border-[#30363d] text-[#6e7681] hover:text-[#e6edf3]"
                }`}
              >
                {EVENT_LABELS[evt]}
              </button>
            )
          })}
        </div>
      </div>

      {/* Test */}
      <div className="flex items-center gap-3 pt-1">
        <button className="btn btn-ghost text-xs py-1 px-3" onClick={onTest} disabled={testing || !persisted}>
          {testing ? <Spinner size="sm" /> : "测试"}
        </button>
        {!persisted && <span className="text-[10px] text-[#6e7681]">保存后可测试</span>}
        {testResult && (
          <span className={`text-xs ${testResult.startsWith("✓") ? "text-[#3fb950]" : "text-[#f85149]"}`}>{testResult}</span>
        )}
      </div>
    </div>
  )
}

export function NotifyChannelsSection() {
  const { data: status, isLoading } = useNotifyConfig()
  const update = useUpdateNotifyConfig()
  const test = useTestChannel()
  const { toast } = useToast()

  const [model, setModel] = useState<NotifyConfig | null>(null)
  const [testResults, setTestResults] = useState<Record<string, string>>({})
  const [testingId, setTestingId] = useState<string | null>(null)

  useEffect(() => {
    if (status && !model) setModel(statusToModel(status))
  }, [status, model])

  const persistedIds = useMemo(
    () => new Set((status?.channels ?? []).map((c) => c.id)),
    [status],
  )

  if (isLoading || !model) return <div className="py-4 flex justify-center"><Spinner /></div>

  function updateChannel(idx: number, updated: ChannelConfig) {
    setModel((m) => (m ? { ...m, channels: m.channels.map((c, i) => (i === idx ? updated : c)) } : m))
  }

  function addChannel(type: ChannelType) {
    setModel((m) => (m ? { ...m, channels: [...m.channels, newChannel(type)] } : m))
  }

  function removeChannel(idx: number) {
    setModel((m) => (m ? { ...m, channels: m.channels.filter((_, i) => i !== idx) } : m))
  }

  async function handleSave() {
    if (!model) return
    try {
      await update.mutateAsync(model)
      toast("通知配置已保存", "success")
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error")
    }
  }

  async function handleTest(channel: ChannelConfig) {
    setTestingId(channel.id)
    setTestResults((r) => ({ ...r, [channel.id]: "" }))
    try {
      const res = await test.mutateAsync({ channel_id: channel.id })
      setTestResults((r) => ({
        ...r,
        [channel.id]: res.ok ? `✓ ${res.detail ?? "发送成功"}` : `✗ ${res.error ?? "发送失败"}`,
      }))
    } catch (e) {
      setTestResults((r) => ({ ...r, [channel.id]: `✗ ${e instanceof Error ? e.message : "请求失败"}` }))
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="space-y-4">
      {/* Master controls */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <Toggle on={model.is_active} onClick={() => setModel({ ...model, is_active: !model.is_active })} />
          <span className="text-sm text-[#e6edf3]">{model.is_active ? "通知已启用" : "通知已停用"}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#8b949e]">盈亏通知阈值</span>
          <input
            type="number"
            className="input w-24 text-xs font-mono"
            value={model.min_pnl_notify_abs}
            onChange={(e) => setModel({ ...model, min_pnl_notify_abs: parseFloat(e.target.value) || 0 })}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#8b949e]">每日汇总时间</span>
          <input
            className="input w-20 text-xs font-mono"
            placeholder="16:30"
            value={model.daily_summary_time}
            onChange={(e) => setModel({ ...model, daily_summary_time: e.target.value })}
          />
        </div>
      </div>

      {/* Channels */}
      {model.channels.length === 0 && (
        <p className="text-xs text-[#6e7681] py-2">尚未配置通知渠道，点击下方按钮添加。</p>
      )}
      <div className="space-y-3">
        {model.channels.map((channel, idx) => (
          <ChannelCard
            key={channel.id}
            channel={channel}
            persisted={persistedIds.has(channel.id)}
            onChange={(u) => updateChannel(idx, u)}
            onRemove={() => removeChannel(idx)}
            onTest={() => handleTest(channel)}
            testing={testingId === channel.id}
            testResult={testResults[channel.id]}
          />
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-1">
        <div className="flex gap-2">
          <button className="btn btn-ghost text-xs" onClick={() => addChannel("telegram")}>+ Telegram</button>
          <button className="btn btn-ghost text-xs" onClick={() => addChannel("webhook")}>+ Webhook</button>
        </div>
        <button className="btn btn-primary text-sm" onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? <Spinner size="sm" /> : "保存通知配置"}
        </button>
      </div>
    </div>
  )
}
