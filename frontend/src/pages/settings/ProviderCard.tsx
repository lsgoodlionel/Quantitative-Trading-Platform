import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import {
  describeTestResult,
  providerStateLabel,
  providerStateTone,
  type ProviderStatus,
  type TestResult,
} from "@/hooks/useLlmConfig"

const TONE_STYLES = {
  ok: "text-[#3fb950] bg-[#132218] border-[#3fb950]/30",
  warn: "text-[#e3b341] bg-[#272111] border-[#e3b341]/30",
  idle: "text-[#8b949e] bg-[#161b22] border-[#30363d]",
} as const

export interface ProviderCardProps {
  provider: ProviderStatus
  /** 是否是当前生效的那个 */
  isActive: boolean
  defaultExpanded: boolean
  canEdit: boolean
  busy: boolean
  testResult: TestResult | null
  /** 「拉取可用模型」的结果；null = 没拉过 */
  fetchedModels: string[] | null
  onSave: (body: { base_url: string; default_model: string; api_key?: string }) => void
  onDelete: () => void
  onTest: (model: string) => void
  onFetchModels: () => void
}

export function ProviderCard({
  provider,
  isActive,
  defaultExpanded,
  canEdit,
  busy,
  testResult,
  fetchedModels,
  onSave,
  onDelete,
  onTest,
  onFetchModels,
}: ProviderCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? provider.default_base_url)
  const [model, setModel] = useState(provider.default_model ?? provider.suggested_models[0] ?? "")
  // 密钥输入框永远从空开始 —— 留空即保持原值，不回显掩码以外的任何东西
  const [apiKey, setApiKey] = useState("")

  const tone = providerStateTone(provider)
  const modelOptions = dedupe([
    ...(fetchedModels ?? []),
    ...provider.suggested_models,
    ...(provider.default_model ? [provider.default_model] : []),
  ])
  const canSubmit = baseUrl.trim().length > 0 && model.trim().length > 0

  return (
    <section
      className={`card p-0 overflow-hidden border ${
        isActive ? "border-[#388bfd]/50" : "border-[#30363d]"
      }`}
      aria-labelledby={`llm-provider-${provider.id}`}
    >
      {/* ── 卡片头 ── */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[#21262d]/40 transition-colors"
      >
        <span className="text-[#6e7681] text-xs w-3">{expanded ? "▾" : "▸"}</span>
        <span id={`llm-provider-${provider.id}`} className="text-sm text-[#e6edf3] font-medium">
          {provider.label}
        </span>
        {!provider.requires_key && (
          <span className="text-[10px] px-1.5 py-0.5 rounded border text-[#58a6ff] bg-[#1c2a3a] border-[#388bfd]/30">
            无需密钥
          </span>
        )}
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${TONE_STYLES[tone]}`}>
          {providerStateLabel(provider)}
        </span>
        {isActive && (
          <span className="text-[10px] px-1.5 py-0.5 rounded border text-[#e3b341] bg-[#2a2415] border-[#e3b341]/40">
            当前生效
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-[#6e7681] truncate max-w-[45%]">
          {provider.default_model ?? provider.default_base_url}
        </span>
      </button>

      {/* ── 卡片体 ── */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-[#21262d]">
          <div className="grid gap-3 sm:grid-cols-2 pt-3">
            <label className="block">
              <span className="label">访问地址</span>
              <input
                className="input w-full mt-1 font-mono text-xs"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={provider.default_base_url}
                spellCheck={false}
                aria-label={`${provider.label} 访问地址`}
              />
            </label>

            <label className="block">
              <span className="label">API 密钥</span>
              <input
                type="password"
                className="input w-full mt-1 font-mono text-xs"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="new-password"
                placeholder={
                  provider.key_hint
                    ? `${provider.key_hint}（留空则不修改）`
                    : provider.requires_key
                      ? "必填"
                      : "本地模型无需填写"
                }
                aria-label={`${provider.label} API 密钥`}
              />
              <span className="block text-[10px] text-[#6e7681] mt-1">
                留空 = 保持原密钥不变；要清除请点「删除配置」。
              </span>
            </label>
          </div>

          {/* 默认模型：下拉是建议值，输入框允许任意模型名 */}
          <div>
            <span className="label">默认模型</span>
            <div className="flex gap-2 mt-1">
              <input
                className="input flex-1 font-mono text-xs"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                list={`llm-models-${provider.id}`}
                spellCheck={false}
                placeholder="可直接输入任意模型名"
                aria-label={`${provider.label} 默认模型`}
              />
              <datalist id={`llm-models-${provider.id}`}>
                {modelOptions.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              {provider.supports_model_listing && (
                <button
                  type="button"
                  className="btn btn-ghost text-xs py-1 px-3 whitespace-nowrap"
                  onClick={onFetchModels}
                  disabled={busy || !provider.configured}
                  title={provider.configured ? "从该服务拉取已安装/可用的模型" : "请先保存配置"}
                >
                  拉取模型
                </button>
              )}
            </div>
            <p className="text-[10px] text-[#6e7681] mt-1">
              下拉里的是常见候选，不是白名单 —— 本地拉了什么模型只有你知道，直接填即可。
            </p>
          </div>

          {testResult && (
            <p
              role="status"
              className={`text-xs px-3 py-2 rounded border ${
                testResult.ok
                  ? "text-[#3fb950] bg-[#132218] border-[#3fb950]/30"
                  : "text-[#f85149] bg-[#2a1b1b] border-[#f85149]/30"
              }`}
            >
              {testResult.ok ? "✓ " : "✗ "}
              {describeTestResult(testResult)}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              type="button"
              className="btn btn-primary text-xs py-1 px-3 disabled:opacity-40"
              onClick={() => {
                onSave({
                  base_url: baseUrl.trim(),
                  default_model: model.trim(),
                  // 空串不发出去，让后端「留空=保持原值」的语义只有一条路径
                  ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
                })
                setApiKey("")
              }}
              disabled={!canEdit || busy || !canSubmit}
              title={canEdit ? undefined : "需交易员及以上角色"}
            >
              {busy ? <Spinner size="sm" /> : "保存"}
            </button>
            <button
              type="button"
              className="btn btn-ghost text-xs py-1 px-3 disabled:opacity-40"
              onClick={() => onTest(model.trim())}
              disabled={!canEdit || busy || !provider.configured}
              title={provider.configured ? "会真实发一次最小请求" : "请先保存配置"}
            >
              测试连接
            </button>
            {provider.configured && (
              <button
                type="button"
                className="btn btn-danger text-xs py-1 px-3 disabled:opacity-40"
                onClick={onDelete}
                disabled={!canEdit || busy}
              >
                删除配置
              </button>
            )}
            {provider.doc_url && (
              <a
                href={provider.doc_url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto text-[11px] text-[#58a6ff] hover:underline"
              >
                {provider.requires_key ? "获取密钥 ↗" : "安装指引 ↗"}
              </a>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

function dedupe(values: string[]): string[] {
  return [...new Set(values.filter((v) => v.length > 0))]
}
