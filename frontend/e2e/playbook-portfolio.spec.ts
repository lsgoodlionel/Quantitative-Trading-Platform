// Playbook ②「发现 → 组合流」冒烟（src/data/playbooks.ts 的 discovery-portfolio）。
//
// 覆盖的步骤：screen → multi-select → optimize → preview-rebalance。
// **不覆盖 confirm-exec** —— 那一步会产生真实委托，必须由人确认，
// 桩上对应的 /rebalance/execute 是刻意回 501 的（见 fixtures/stub-api.ts）。

import { SCREENER_CANDIDATES } from "./fixtures/screener"
import { expect, test } from "./fixtures/test"

const PICKED = SCREENER_CANDIDATES.map((c) => c.symbol)

test("发现 → 组合流：筛选 → 多选 → 组合优化 → 预览调仓", async ({ page }) => {
  // ── 步骤 1：条件筛选 ──
  await page.goto("/screener")
  await expect(page.getByRole("heading", { name: "股票筛选器", exact: true })).toBeVisible()

  await page.getByRole("button", { name: "开始筛选" }).click()

  // 结果表出现，且条数与后端返回的 count 一致
  const rows = page.getByTestId("screener-row")
  await expect(rows).toHaveCount(PICKED.length)
  await expect(page.getByText(`匹配 ${PICKED.length} / 33 只`)).toBeVisible()

  // ── 步骤 2：多选候选标的 ──
  for (const symbol of PICKED) {
    await page.getByRole("checkbox", { name: `选择 ${symbol}` }).check()
  }

  const actions = page.getByTestId("selection-actions")
  await expect(actions).toBeVisible()
  await expect(actions.getByText(`已选 ${PICKED.length} 只`)).toBeVisible()

  // ── 步骤 3：送去组合优化 ──
  // 选择走 URL 传递（?symbols=A,B,C），所以这里断言的是地址栏而不是内部状态
  await actions.getByRole("button", { name: "🎯 送组合优化" }).click()

  await expect(page).toHaveURL(/\/portfolio\?tab=optimizer/)
  await expect(page).toHaveURL(new RegExp(`symbols=${PICKED.join("%2C")}`))

  await page.getByRole("button", { name: "▶ 开始优化" }).click()

  // 优化结果：关键指标 + 资产明细里三只标的的权重
  await expect(page.getByText("年化收益率", { exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "资产明细", exact: true })).toBeVisible()
  const detail = page.getByRole("table").filter({ hasText: "风险贡献" })
  for (const symbol of PICKED) {
    await expect(detail.getByRole("cell", { name: symbol, exact: true })).toBeVisible()
  }

  // ── 步骤 4：预览调仓 ──
  await page.getByRole("button", { name: "预览调仓" }).click()

  await expect(page.getByText("账户净值", { exact: true })).toBeVisible()
  await expect(page.getByText("买入合计", { exact: true })).toBeVisible()
  // 后端返回的警告要原样露给用户看，而不是被吞掉
  await expect(page.getByText("TSLA 不在目标权重内，将被清仓")).toBeVisible()

  // 执行按钮出现即止 —— 冒烟不点它：那一步会下单
  await expect(page.getByRole("button", { name: /确认执行 4 笔委托/ })).toBeVisible()

  // ── 顺带确认「预览调仓」的另一半入口：持仓 Tab 能打开 ──
  await page.getByRole("button", { name: "💼 持仓分析" }).click()
  await expect(page).toHaveURL(/tab=holdings/)
  await expect(page.getByRole("heading", { name: "持仓明细", exact: true })).toBeVisible()
})
