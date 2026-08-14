// Playbook ③「因子研究流」冒烟（src/data/playbooks.ts 的 factor-research）。
//
// 覆盖的步骤：build-factor（公式因子）→ fitness（成本感知适应度）→ factor-backtest。
// processors（截面处理流水线）不进冒烟：它要先配一整条 infer/learn 流水线，
// 配置面比其余步骤深一个量级，单独覆盖更合适。

import { FACTOR_DISPLAY } from "./fixtures/factor"
import { BACKTEST_DISPLAY } from "./fixtures/backtest"
import { expect, test } from "./fixtures/test"

test("因子研究流：建公式因子 → 适应度 → 回测", async ({ page }) => {
  // ── 步骤 1：用公式构造一个因子 ──
  await page.goto("/research?tab=factor")
  await expect(page.getByRole("heading", { name: "研究", exact: true })).toBeVisible()

  await page.getByRole("button", { name: "⚡ 公式因子" }).click()

  // 从预设加载一条公式，省得逐个点 token（点选路径由构建器自己的单测覆盖）。
  // 「✓ 公式有效」出现 = RPN 栈校验通过，也就是 token 真的进了公式区
  await page.getByRole("button", { name: /动量\/波动率/ }).click()
  await expect(page.getByText("✓ 公式有效")).toBeVisible()

  await page.getByRole("button", { name: "▶ 运行公式因子分析" }).click()

  // 验收判据：因子能算出值，IC/IR 有结果
  await expect(page.getByRole("heading", { name: "IC 统计汇总", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "因子值时序", exact: true })).toBeVisible()
  // 同一个数在 IC 汇总表和结论文案里各出现一次，取第一个即可
  await expect(page.getByText(FACTOR_DISPLAY.icMean20).first()).toBeVisible()

  // ── 步骤 2：成本感知适应度 ──
  await page.getByRole("button", { name: "🎯 成本感知适应度" }).click()
  await page.getByRole("button", { name: "计算因子适应度" }).click()

  await expect(page.getByText("因子适应度（成本感知）")).toBeVisible()
  await expect(page.getByText("✓ 活跃度门槛通过")).toBeVisible()
  // 验收判据：扣成本后适应度仍为正
  await expect(page.getByText(FACTOR_DISPLAY.fitness, { exact: true }).first()).toBeVisible()

  // ── 步骤 3：因子回测 ──
  // 因子结论区给出的策略推荐是通往回测页的入口，走它而不是直接改地址栏，
  // 才算测到「从因子流到回测」这条链路
  await page.getByRole("button", { name: "⚡ 公式因子" }).click()
  await page.getByRole("link", { name: /前往回测/ }).first().click()

  await expect(page).toHaveURL(/\/backtest\?strategy=/)
  await expect(page.getByRole("heading", { name: "回测", exact: true })).toBeVisible()

  await page.getByRole("button", { name: "▶ 运行回测" }).click()

  await expect(page.getByText(BACKTEST_DISPLAY.sharpe)).toBeVisible()
  await expect(page.getByRole("heading", { name: "净值曲线", exact: true })).toBeVisible()
})
