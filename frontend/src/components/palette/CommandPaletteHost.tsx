// ── 命令面板宿主：全局快捷键 + 挂载点（V3 · H3）────────────────
import { useCallback, useEffect, useState } from "react"
import { CommandPalette } from "./CommandPalette"
import { isPaletteShortcut, isTypingTarget } from "./shortcut"

interface CommandPaletteHostProps {
  /** 未登录时关掉：登录页按 ⌘K 只会跳到被守卫的路由再被弹回来 */
  enabled: boolean
}

export function CommandPaletteHost({ enabled }: CommandPaletteHostProps) {
  const [open, setOpen] = useState(false)

  const close = useCallback(() => setOpen(false), [])

  useEffect(() => {
    if (!enabled) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isPaletteShortcut(event)) return
      // 用户正在输入框里敲字时不劫持快捷键
      if (isTypingTarget(event.target)) return
      event.preventDefault()
      setOpen((prev) => !prev)
    }

    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [enabled])

  // 从开启状态被禁用（登出）时收起面板，避免残留在登录页之上
  useEffect(() => {
    if (!enabled) setOpen(false)
  }, [enabled])

  if (!enabled) return null

  return <CommandPalette open={open} onClose={close} />
}
