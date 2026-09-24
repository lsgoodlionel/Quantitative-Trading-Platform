import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useToast } from "@/components/ui/Toast"
import { ArtifactRow } from "@/pages/lab/ArtifactRow"
import { ArtifactDetailPanel } from "@/pages/lab/ArtifactDetailPanel"
import { AutoLoopPanel } from "@/pages/lab/AutoLoopPanel"
import {
  KIND_LABELS,
  useDeleteArtifact,
  useLabArtifacts,
  type ArtifactKind,
  type ArtifactMeta,
} from "@/hooks/useLabArtifacts"

const KINDS: ArtifactKind[] = ["dataset", "model", "signal"]
const PAGE_LIMIT = 50

type LabTab = "artifacts" | "auto-loop"

const TAB_LABELS: Record<LabTab, string> = {
  artifacts: "产物",
  "auto-loop": "自动循环",
}

/**
 * 投研产物库（V4 M4）
 *
 * 数据集 / 模型 / 信号三类产物的浏览、预览与删除。
 *
 * 与「实验记录」的区别：实验记录存的是**指标**且有 500 条滚动淘汰；
 * 产物是独立生命周期的实体 —— 实验记录被淘汰后，产物照样加载得到。
 */
export function Lab() {
  const { toast } = useToast()
  const [tab, setTab] = useState<LabTab>("artifacts")
  const [kind, setKind] = useState<ArtifactKind | undefined>(undefined)
  const [keyword, setKeyword] = useState("")
  const [selected, setSelected] = useState<ArtifactMeta | null>(null)

  const listQ = useLabArtifacts({
    kind,
    nameContains: keyword.trim() || undefined,
    limit: PAGE_LIMIT,
  })
  const deleteM = useDeleteArtifact()

  const items = listQ.data?.items ?? []

  const handleDelete = (item: ArtifactMeta) => {
    deleteM.mutate(item.artifact_id, {
      onSuccess: () => {
        toast(`已删除「${item.name}」`, "success")
        if (selected?.artifact_id === item.artifact_id) setSelected(null)
      },
      onError: (e) => toast(`删除失败: ${e.message}`, "error"),
    })
  }

  return (
    <AppShell title="投研产物库">
      <div className="mb-4 flex gap-1" role="tablist" aria-label="产物库视图">
        {(Object.keys(TAB_LABELS) as LabTab[]).map((t) => (
          <FilterChip key={t} active={tab === t} onClick={() => setTab(t)}>
            {TAB_LABELS[t]}
          </FilterChip>
        ))}
      </div>

      {tab === "auto-loop" && <AutoLoopPanel />}

      {tab === "artifacts" && (
        <>
      {/* 筛选 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          <FilterChip active={kind === undefined} onClick={() => setKind(undefined)}>
            全部
          </FilterChip>
          {KINDS.map((k) => (
            <FilterChip key={k} active={kind === k} onClick={() => setKind(k)}>
              {KIND_LABELS[k]}
            </FilterChip>
          ))}
        </div>

        <input
          className="input w-56 text-sm"
          placeholder="按名称筛选…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />

        <span className="ml-auto text-xs text-[#6e7681]">
          共 {listQ.data?.total ?? 0} 件
        </span>
      </div>

      {/* 容量策略：如实展示后端给的说明，不要前端自己编一套措辞 */}
      {listQ.data?.capacity_note && (
        <p className="mb-4 text-[11px] text-[#6e7681]">{listQ.data.capacity_note}</p>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          {listQ.isLoading && (
            <div className="card flex h-48 items-center justify-center">
              <Spinner />
            </div>
          )}

          {listQ.error && (
            <div className="card text-sm text-[#f85149]">
              加载失败：{listQ.error.message}
            </div>
          )}

          {!listQ.isLoading && !listQ.error && items.length === 0 && (
            <EmptyState
              title="还没有产物"
              description="训练模型、保存数据集或信号后，它们会出现在这里"
            />
          )}

          {items.length > 0 && (
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#21262d] text-[#8b949e]">
                    <th className="py-2 pl-4 pr-3 text-left">名称</th>
                    <th className="py-2 pr-3 text-left">类别</th>
                    <th className="py-2 pr-3 text-right">大小</th>
                    <th className="py-2 pr-3 text-left">创建时间</th>
                    <th className="py-2 pr-4" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <ArtifactRow
                      key={item.artifact_id}
                      item={item}
                      selected={selected?.artifact_id === item.artifact_id}
                      onSelect={setSelected}
                      onDelete={handleDelete}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="xl:col-span-1">
          <ArtifactDetailPanel item={selected} />
        </div>
      </div>
        </>
      )}
    </AppShell>
  )
}

interface FilterChipProps {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}

function FilterChip({ active, onClick, children }: FilterChipProps) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
        active
          ? "border-[#58a6ff] bg-[#1f6feb]/20 text-[#58a6ff]"
          : "border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]"
      }`}
    >
      {children}
    </button>
  )
}
