import { useEffect } from "react"

/**
 * 打开浮层期间锁住页面滚动，关闭时恢复**原值**（而不是硬写回 ""）。
 *
 * 浮层不锁滚动的话，滚轮会穿透到背景页面 —— 命令面板/弹窗最常被吐槽的细节。
 * 恢复原值而非清空：页面本身可能已经设过 overflow，直接清空会把它的设置吃掉。
 */
export function useBodyScrollLock(locked: boolean): void {
  useEffect(() => {
    if (!locked) return

    const previous = document.body.style.overflow
    document.body.style.overflow = "hidden"

    return () => {
      document.body.style.overflow = previous
    }
  }, [locked])
}
