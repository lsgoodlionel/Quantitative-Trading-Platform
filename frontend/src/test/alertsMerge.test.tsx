import { describe, expect, it } from "vitest"
import { ALL_COMMANDS } from "@/components/palette/commandRegistry"

/**
 * 「价格预警」与「通知中心」合并为 /alerts 两个 Tab（V3 · 页面收敛）。
 *
 * 这里只钉住导航契约 —— 渲染行为由各 Tab 自己的用例覆盖。
 */
describe("预警与通知页合并", () => {
  it("命令面板的两条都直达具体 Tab，而不是页面默认 Tab", () => {
    const rules = ALL_COMMANDS.find((c) => c.id === "page-alerts")
    const inbox = ALL_COMMANDS.find((c) => c.id === "page-notifications")

    expect(rules?.to).toBe("/alerts?tab=rules")
    expect(inbox?.to).toBe("/alerts?tab=inbox")
  })

  it("命令面板不再把用户送到独立的 /notifications 页", () => {
    // 那条路由仍然有效（重定向），但面板应当直接给最终地址，
    // 否则用户会看到地址栏跳两下
    const stale = ALL_COMMANDS.filter((c) => c.to.split("?")[0] === "/notifications")

    expect(stale).toEqual([])
  })

  it("「通知」「消息」「收件箱」都能搜到收件箱条目", () => {
    const inbox = ALL_COMMANDS.find((c) => c.id === "page-notifications")

    for (const word of ["通知", "消息", "收件箱"]) {
      expect(inbox?.keywords).toContain(word)
    }
  })
})
