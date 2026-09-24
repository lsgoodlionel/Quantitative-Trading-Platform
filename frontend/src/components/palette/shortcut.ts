// ── 命令面板快捷键判定（V3 · H3）────────────────────────────────
//
// 拆成纯函数而非写死在 hook 里：这两条判断是面板最容易踩坑的地方，
// 单独可测比整页渲染再模拟按键便宜得多。

/** 原生输入元素：这些标签一律视为输入态 */
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"])

/** contenteditable 容器（`contenteditable="false"` 表示显式关闭，不算） */
const EDITABLE_SELECTOR = '[contenteditable]:not([contenteditable="false"])'

/**
 * 事件目标是否处于「用户正在输入」状态。
 *
 * 用户在搜索框里敲字时被全局快捷键劫持是最烦人的交互之一，
 * 所以 input / textarea / contenteditable 内一律不触发面板。
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (TYPING_TAGS.has(target.tagName)) return true
  // jsdom 不实现 isContentEditable，故同时回退到属性/祖先查询
  if (target.isContentEditable) return true
  return target.closest(EDITABLE_SELECTOR) !== null
}

/** 是否为唤起命令面板的组合键：⌘K（macOS）/ Ctrl+K（其它平台） */
export function isPaletteShortcut(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey">): boolean {
  if (event.key?.toLowerCase() !== "k") return false
  return Boolean(event.metaKey || event.ctrlKey)
}
