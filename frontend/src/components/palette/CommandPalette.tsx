// ── 命令面板（V3 · H3）──────────────────────────────────────────
//
// 边界：面板只做**导航与预置状态**，不产生订单、不调写接口。
// 需要确认的写操作属于 Copilot（B-c）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useSymbolContext } from "@/contexts/SymbolContext"
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock"
import { ALL_COMMANDS } from "./commandRegistry"
import { filterCommands, groupCommands, resolveCommandTarget } from "./commandSearch"
import type { CommandItem, CommandKind } from "./commandTypes"

const KIND_ACCENT: Record<CommandKind, string> = {
  page: "#58a6ff",
  symbol: "#3fb950",
  action: "#e3b341",
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate()
  const { symbol, market } = useSymbolContext()
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时锁滚动，关闭时恢复 —— 否则滚轮穿透到背景页面
  useBodyScrollLock(open)

  // 每次重新打开都是一次全新的检索，不带上次的残留查询
  useEffect(() => {
    if (!open) return
    setQuery("")
    setActiveIndex(0)
    inputRef.current?.focus()
  }, [open])

  const matches = useMemo(() => filterCommands(ALL_COMMANDS, query), [query])
  const groups = useMemo(() => groupCommands(matches), [matches])
  // 分组后的扁平顺序才是方向键走的顺序，不能直接用 matches
  const ordered = useMemo(() => groups.flatMap((g) => g.items), [groups])

  const run = useCallback(
    (item: CommandItem) => {
      const target = resolveCommandTarget(item, { symbol, market })
      onClose()
      navigate(target)
    },
    [navigate, onClose, symbol, market],
  )

  const onQueryChange = useCallback((value: string) => {
    setQuery(value)
    setActiveIndex(0)
  }, [])

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        // 阻断冒泡：底层页面可能也监听 Escape（关抽屉、退出编辑），
        // 关面板这一下不该顺带把它们一起关掉
        event.preventDefault()
        event.stopPropagation()
        onClose()
        return
      }
      // 面板已经开着时再按一次 ⌘K 直接收起（全局监听在输入态里不触发）
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key === "ArrowDown") {
        event.preventDefault()
        setActiveIndex((i) => (ordered.length ? (i + 1) % ordered.length : 0))
        return
      }
      if (event.key === "ArrowUp") {
        event.preventDefault()
        setActiveIndex((i) => (ordered.length ? (i - 1 + ordered.length) % ordered.length : 0))
        return
      }
      if (event.key === "Enter") {
        event.preventDefault()
        const item = ordered[activeIndex]
        if (item) run(item)
      }
    },
    [activeIndex, onClose, ordered, run],
  )

  if (!open) return null

  let flatIndex = -1

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 backdrop-blur-sm pt-[12vh] px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      onKeyDown={onKeyDown}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        className="w-full max-w-xl bg-[#161b22] border border-[#30363d] rounded-xl shadow-2xl overflow-hidden"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <span className="text-[#6e7681] text-sm">⌘K</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="搜索页面、标的或动作…"
            aria-label="搜索命令"
            className="flex-1 bg-transparent text-[#e6edf3] text-sm outline-none placeholder:text-[#6e7681]"
          />
          <span className="text-[10px] text-[#6e7681] shrink-0">
            当前标的 {symbol} · {market}
          </span>
        </div>

        <div role="listbox" aria-label="命令列表" className="max-h-[52vh] overflow-auto py-2">
          {ordered.length === 0 && (
            <p className="px-4 py-6 text-center text-xs text-[#6e7681]">
              没有匹配的条目。面板只做子串匹配，暂不支持拼音/首字母。
            </p>
          )}

          {groups.map((group) => (
            <div key={group.kind} role="group" aria-label={group.label} className="mb-1">
              <p className="px-4 py-1 text-[10px] uppercase tracking-wider text-[#6e7681]">
                {group.label}
              </p>
              {group.items.map((item) => {
                flatIndex += 1
                const isActive = flatIndex === activeIndex
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    onClick={() => run(item)}
                    onMouseEnter={() => setActiveIndex(ordered.indexOf(item))}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                      isActive ? "bg-[#1f2b3d]" : "hover:bg-[#1c2128]"
                    }`}
                  >
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ backgroundColor: KIND_ACCENT[item.kind] }}
                    />
                    <span className="text-sm text-[#e6edf3] shrink-0">{item.label}</span>
                    {item.hint && (
                      <span className="text-[11px] text-[#6e7681] truncate">{item.hint}</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        <div className="px-4 py-2 border-t border-[#21262d] text-[10px] text-[#6e7681] flex gap-4">
          <span>↑↓ 选择</span>
          <span>↵ 跳转</span>
          <span>Esc 关闭</span>
          <span className="ml-auto">面板只做导航，不会下单</span>
        </div>
      </div>
    </div>
  )
}
