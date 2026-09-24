import { Spinner } from "@/components/ui/Spinner"
import {
  KIND_LABELS,
  formatBytes,
  useArtifactPreview,
  useModelDetail,
  type ArtifactMeta,
} from "@/hooks/useLabArtifacts"

interface ArtifactDetailPanelProps {
  item: ArtifactMeta | null
}

export function ArtifactDetailPanel({ item }: ArtifactDetailPanelProps) {
  const previewQ = useArtifactPreview(item?.artifact_id ?? null)
  const detailQ = useModelDetail(item?.artifact_id ?? null, item?.kind ?? null)

  if (!item) {
    return (
      <div className="card flex h-48 items-center justify-center text-sm text-[#6e7681]">
        选择左侧任一产物查看详情
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="card space-y-2">
        <h3 className="text-sm font-semibold text-[#e6edf3]">{item.name}</h3>
        <MetaRow label="类别" value={KIND_LABELS[item.kind] ?? item.kind} />
        <MetaRow label="大小" value={formatBytes(item.size_bytes)} />
        <MetaRow label="创建" value={new Date(item.created_at).toLocaleString("zh-CN")} />
        <MetaRow label="ID" value={item.artifact_id} mono />
        {/* checksum 是加载时的完整性校验依据，展示出来便于对账 */}
        <MetaRow label="校验和" value={`${item.checksum.slice(0, 16)}…`} mono />

        {Object.keys(item.tags).length > 0 && (
          <div className="pt-1">
            <p className="mb-1 text-[10px] text-[#6e7681]">标签</p>
            <div className="flex flex-wrap gap-1">
              {Object.entries(item.tags).map(([k, v]) => (
                <span
                  key={k}
                  className="rounded border border-[#30363d] px-1.5 py-0.5 font-mono text-[9px] text-[#8b949e]"
                >
                  {k}={v}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <JsonCard
        title="内容预览"
        isLoading={previewQ.isLoading}
        error={previewQ.error}
        data={previewQ.data}
      />

      {item.kind === "model" && (
        <JsonCard
          title="模型详情"
          isLoading={detailQ.isLoading}
          error={detailQ.error}
          data={detailQ.data}
        />
      )}
    </div>
  )
}

interface MetaRowProps {
  label: string
  value: string
  mono?: boolean
}

function MetaRow({ label, value, mono }: MetaRowProps) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="shrink-0 text-[#6e7681]">{label}</span>
      <span className={`truncate text-[#e6edf3] ${mono ? "font-mono text-[10px]" : ""}`}>
        {value}
      </span>
    </div>
  )
}

interface JsonCardProps {
  title: string
  isLoading: boolean
  error: Error | null
  data: Record<string, unknown> | undefined
}

function JsonCard({ title, isLoading, error, data }: JsonCardProps) {
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold text-[#e6edf3]">{title}</h3>
      {isLoading && (
        <div className="flex h-20 items-center justify-center">
          <Spinner size="sm" />
        </div>
      )}
      {error && <p className="text-xs text-[#f85149]">{error.message}</p>}
      {!isLoading && !error && (
        // 产物内容形态各异（数据集是形状与列名、模型是超参与重要性），
        // 与其为每种类别写一套渲染，不如如实展示服务端给的结构化摘要
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-[#8b949e]">
          {JSON.stringify(data ?? {}, null, 2)}
        </pre>
      )}
    </div>
  )
}
