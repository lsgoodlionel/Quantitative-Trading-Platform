import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

import { formatBytes, KIND_LABELS, type ArtifactMeta } from "@/hooks/useLabArtifacts"
import { ArtifactRow } from "@/pages/lab/ArtifactRow"

const ITEM: ArtifactMeta = {
  artifact_id: "0123456789abcdef0123",
  kind: "model",
  name: "lgbm_alpha_v1",
  created_at: "2026-08-11T10:00:00Z",
  size_bytes: 5_242_880,
  tags: { strategy: "momentum", window: "20" },
  checksum: "a".repeat(64),
}

describe("formatBytes", () => {
  it("keeps raw bytes under 1 KB", () => {
    expect(formatBytes(512)).toBe("512 B")
  })

  it("scales through KB / MB / GB", () => {
    expect(formatBytes(2048)).toBe("2.0 KB")
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB")
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB")
  })

  it("drops the decimal once the number is large enough to not need it", () => {
    // 100+ 的小数位是噪音，占位还挤掉列宽
    expect(formatBytes(150 * 1024)).toBe("150 KB")
  })
})

describe("ArtifactRow", () => {
  const onSelect = vi.fn()
  const onDelete = vi.fn()

  beforeEach(() => {
    onSelect.mockReset()
    onDelete.mockReset()
  })

  function renderRow() {
    return render(
      <table><tbody>
        <ArtifactRow item={ITEM} selected={false} onSelect={onSelect} onDelete={onDelete} />
      </tbody></table>,
    )
  }

  it("shows name, kind label and human-readable size", () => {
    renderRow()

    expect(screen.getByText("lgbm_alpha_v1")).toBeTruthy()
    expect(screen.getByText(KIND_LABELS.model)).toBeTruthy()
    expect(screen.getByText("5.0 MB")).toBeTruthy()
  })

  it("truncates the artifact id instead of showing the full uuid", () => {
    renderRow()

    expect(screen.getByText("0123456789ab")).toBeTruthy()
    expect(screen.queryByText(ITEM.artifact_id)).toBeNull()
  })

  it("selects the row on click", () => {
    renderRow()

    fireEvent.click(screen.getByText("lgbm_alpha_v1"))

    expect(onSelect).toHaveBeenCalledWith(ITEM)
  })

  it("does not select the row when the delete button is clicked", () => {
    // 删除按钮嵌在行内，事件冒泡会让「删除」顺带触发「选中」
    renderRow()

    fireEvent.click(screen.getByText("删除"))

    expect(onDelete).toHaveBeenCalledWith(ITEM)
    expect(onSelect).not.toHaveBeenCalled()
  })
})
