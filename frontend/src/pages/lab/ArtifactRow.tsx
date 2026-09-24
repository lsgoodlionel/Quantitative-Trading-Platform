import {
  KIND_LABELS,
  formatBytes,
  type ArtifactMeta,
} from "@/hooks/useLabArtifacts"

const KIND_COLORS: Record<string, string> = {
  dataset: "#3fb950",
  model: "#bc8cff",
  signal: "#58a6ff",
}

interface ArtifactRowProps {
  item: ArtifactMeta
  selected: boolean
  onSelect: (item: ArtifactMeta) => void
  onDelete: (item: ArtifactMeta) => void
}

export function ArtifactRow({ item, selected, onSelect, onDelete }: ArtifactRowProps) {
  return (
    <tr
      onClick={() => onSelect(item)}
      className={`cursor-pointer border-b border-[#21262d]/40 last:border-0 transition-colors ${
        selected ? "bg-[#1f6feb]/10" : "hover:bg-[#21262d]/30"
      }`}
    >
      <td className="py-2 pl-4 pr-3">
        <span className="text-[#e6edf3]">{item.name}</span>
        {/* artifact_id 是服务端生成的 UUID，截断显示够用于对账 */}
        <span className="block font-mono text-[9px] text-[#6e7681]">
          {item.artifact_id.slice(0, 12)}
        </span>
      </td>
      <td className="py-2 pr-3">
        <span
          className="rounded px-1.5 py-0.5 text-[10px]"
          style={{
            color: KIND_COLORS[item.kind] ?? "#8b949e",
            background: `${KIND_COLORS[item.kind] ?? "#8b949e"}18`,
          }}
        >
          {KIND_LABELS[item.kind] ?? item.kind}
        </span>
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
        {formatBytes(item.size_bytes)}
      </td>
      <td className="py-2 pr-3 font-mono text-[10px] text-[#8b949e]">
        {new Date(item.created_at).toLocaleString("zh-CN")}
      </td>
      <td className="py-2 pr-4 text-right">
        <button
          onClick={(e) => {
            // 行本身是「选中」，删除按钮不能连带触发它
            e.stopPropagation()
            onDelete(item)
          }}
          className="rounded border border-[#30363d] px-2 py-1 text-[10px] text-[#8b949e] transition-colors hover:border-[#f85149]/40 hover:text-[#f85149]"
        >
          删除
        </button>
      </td>
    </tr>
  )
}
