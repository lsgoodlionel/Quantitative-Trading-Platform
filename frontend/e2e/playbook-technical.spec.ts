// Playbook ①「单标的技术流」冒烟（src/data/playbooks.ts 的 single-symbol）。
//
// 覆盖的步骤：pick-symbol → read-quote → backtest。
// 后两步（样本外验证 / 模拟盘）不在冒烟范围内：前者要跑一整套完整验证、
// 后者会启动策略实例，都超出「流程能不能走通」的边界。

import { BACKTEST_DISPLAY } from "./fixtures/backtest"
import { expect, test } from "./fixtures/test"

test("单标的技术流：选标的 → 看行情与指标 → 跑回测", async ({ page }) => {
  // ── 步骤 1：选定标的 ──
  // 左栏默认停在美股 Tab；点中 AAPL 后，「当前标的」应写回地址栏
  await page.goto("/market?tab=quote")

  await page.getByRole("button", { name: /AAPL/ }).first().click()

  await expect(page).toHaveURL(/symbol=AAPL/)
  await expect(page).toHaveURL(/market=US/)

  // ── 步骤 2：看行情与指标 ──
  // 点「查询」才真正拉 K 线。exact 不能省：Tab 里还有个「📊 行情查询」，
  // 默认的子串匹配会同时命中两个，直接撞 strict mode
  await page.getByRole("button", { name: "查询", exact: true }).click()

  // 查到数据后才会出现的三样东西：最新价、MA 图例、指标切换按钮
  await expect(page.getByText("MA20", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "RSI", exact: true })).toBeVisible()

  // OHLCV 摘要区（只有拿到 bars 才渲染）
  await expect(page.getByText("成交量", { exact: true })).toBeVisible()
  await expect(page.getByText("K线数量", { exact: true })).toBeVisible()

  // ── 步骤 3：带着标的去回测 ──
  await page.getByRole("button", { name: "🔬 回测此标的" }).click()

  await expect(page).toHaveURL(/\/backtest\?/)
  await expect(page).toHaveURL(/symbol=AAPL/)
  await expect(page.getByRole("heading", { name: "回测", exact: true })).toBeVisible()

  // 共享配置头应当把 URL 里的标的带进表单
  await expect(page.locator("#shared-symbol")).toHaveValue("AAPL")

  await page.getByRole("button", { name: "▶ 运行回测" }).click()

  // Playbook 的验收判据：拿到夏普、最大回撤、胜率三个数
  await expect(page.getByText(BACKTEST_DISPLAY.sharpe)).toBeVisible()
  await expect(page.getByText(BACKTEST_DISPLAY.maxDrawdown)).toBeVisible()
  await expect(page.getByText(BACKTEST_DISPLAY.winRate)).toBeVisible()

  // 指标达标时结果面板给出的下一步指引
  await expect(page.getByText("✅ 回测指标达标，可进入模拟验证")).toBeVisible()
})
