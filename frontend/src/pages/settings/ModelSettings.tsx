import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { usePermissions } from "@/hooks/useRbac"
import {
  useDeleteProvider,
  useFetchProviderModels,
  useLlmProviders,
  useSaveProvider,
  useSetActiveModel,
  useTestProvider,
  type SaveProviderRequest,
  type TestResult,
} from "@/hooks/useLlmConfig"
import { ActiveModelBar } from "./ActiveModelBar"
import { ProviderCard } from "./ProviderCard"
import { OllamaOnboarding } from "./OllamaOnboarding"

/**
 * 模型管理页 —— 一个页面配完所有 provider。
 *
 * 产品方向：本地 Ollama 优先。它排第一、默认展开、无需密钥 ——
 * 「无密钥也能用」是第一等公民路径，不是降级路径。
 */
export function ModelSettings() {
  const { data, isLoading, error } = useLlmProviders()
  const { canTrade } = usePermissions()

  const save = useSaveProvider()
  const remove = useDeleteProvider()
  const test = useTestProvider()
  const fetchModels = useFetchProviderModels()
  const setActive = useSetActiveModel()

  // 每张卡片各自的测试结果 / 拉取到的模型列表
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})
  const [models, setModels] = useState<Record<string, string[]>>({})
  const [pendingId, setPendingId] = useState<string | null>(null)

  if (isLoading) {
    return (
      <AppShell title="模型管理">
        <div className="py-20 flex justify-center">
          <Spinner size="lg" />
        </div>
      </AppShell>
    )
  }

  if (error || !data) {
    return (
      <AppShell title="模型管理">
        <p className="text-xs text-[#f85149] bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          无法读取模型配置：{error?.message ?? "未知错误"}
        </p>
      </AppShell>
    )
  }

  const { providers, active } = data
  const readyProviders = providers.filter((p) => p.ready)

  async function runTest(id: string, model: string) {
    setPendingId(id)
    try {
      const result = await test.mutateAsync({ id, model: model || undefined })
      setTestResults((prev) => ({ ...prev, [id]: result }))
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          provider_id: id,
          model: model || null,
          latency_ms: null,
          error: err instanceof Error ? err.message : "请求失败",
        },
      }))
    } finally {
      setPendingId(null)
    }
  }

  async function runSave(id: string, body: SaveProviderRequest) {
    setPendingId(id)
    try {
      await save.mutateAsync({ id, body })
      // 配置变了，旧的测试结论就作废了，别留在屏幕上误导人
      setTestResults((prev) => omit(prev, id))
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          provider_id: id,
          model: null,
          latency_ms: null,
          error: err instanceof Error ? err.message : "保存失败",
        },
      }))
    } finally {
      setPendingId(null)
    }
  }

  async function runDelete(id: string) {
    setPendingId(id)
    try {
      await remove.mutateAsync(id)
      setTestResults((prev) => omit(prev, id))
      setModels((prev) => omit(prev, id))
    } finally {
      setPendingId(null)
    }
  }

  async function runFetchModels(id: string) {
    setPendingId(id)
    try {
      const list = await fetchModels.mutateAsync(id)
      setModels((prev) => ({ ...prev, [id]: list.models }))
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          provider_id: id,
          model: null,
          latency_ms: null,
          error: err instanceof Error ? err.message : "拉取模型失败",
        },
      }))
    } finally {
      setPendingId(null)
    }
  }

  return (
    <AppShell title="模型管理">
      <div className="max-w-3xl space-y-4">
        {!active.configured && <OllamaOnboarding />}

        <ActiveModelBar
          active={active}
          readyProviders={readyProviders}
          canEdit={canTrade}
          busy={setActive.isPending}
          onApply={(provider_id, model) => setActive.mutate({ provider_id, model })}
        />

        {!canTrade && (
          <p className="text-xs text-[#8b949e] bg-[#161b22] border border-[#30363d] rounded px-3 py-2">
            当前角色为只读，模型配置仅供查看。如需修改请联系交易员或管理员。
          </p>
        )}

        <div className="space-y-3">
          {providers.map((provider, index) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              isActive={active.provider_id === provider.id}
              // Ollama 排第一且默认展开：它开箱即用，是新用户最该看到的那张卡
              defaultExpanded={index === 0 || provider.configured}
              canEdit={canTrade}
              busy={pendingId === provider.id}
              testResult={testResults[provider.id] ?? null}
              fetchedModels={models[provider.id] ?? null}
              onSave={(body) => runSave(provider.id, body)}
              onDelete={() => runDelete(provider.id)}
              onTest={(model) => runTest(provider.id, model)}
              onFetchModels={() => runFetchModels(provider.id)}
            />
          ))}
        </div>
      </div>
    </AppShell>
  )
}

function omit<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record }
  delete next[key]
  return next
}
