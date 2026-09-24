// Playwright 配置（V3 Wave D-b · J5）。
//
// 只跑 Chromium：跨浏览器矩阵不在本 Wave 范围内（契约 §5），等这套冒烟稳定后再加。
// 后端全部由 page.route 桩住，所以这里不需要起 Postgres/Redis/Celery，
// webServer 只拉一个前端 dev server。

import { defineConfig, devices } from "@playwright/test"

import { authStorageState } from "./e2e/fixtures/session"

/** 与 e2e/fixtures/test.ts 的 BASE_URL 保持一致；storageState 的 origin 也认这个值。 */
const PORT = 5173
const BASE_URL = `http://localhost:${PORT}`

const isCI = !!process.env.CI

/**
 * 逃生口：用系统装的浏览器代替 Playwright 自带的 Chromium。
 *
 *   PW_CHROMIUM_CHANNEL=chrome npm run test:e2e
 *
 * `npx playwright install` 会从 cdn.playwright.dev 下载 ~150MB 的 Chromium，
 * 部分公司网络/VPN 会把这个下载拦成 400，装不上就一条用例都跑不了。
 * 有这个口子，本地至少还能用系统 Chrome 把用例跑通。
 * CI 不设这个变量 —— 那边始终用 Playwright 自带的版本，保证结果可复现。
 */
const channel = process.env.PW_CHROMIUM_CHANNEL || undefined

export default defineConfig({
  testDir: "./e2e",
  // 冒烟用例之间没有共享状态（登录态走 storageState，后端是桩），可以放心并行
  fullyParallel: true,
  forbidOnly: isCI,
  // CI 上重试一次：新写的 E2E 必然有不稳定期，但本地不重试，
  // 免得把「偶尔失败」在开发阶段就藏起来
  retries: isCI ? 1 : 0,
  workers: isCI ? 2 : undefined,
  reporter: isCI
    ? [["list"], ["html", { open: "never" }], ["github"]]
    : [["list"], ["html", { open: "never" }]],

  use: {
    baseURL: BASE_URL,
    // 默认已登录：三条 Playbook 不重复点登录表单（契约 §3）。
    // 登录流程本身由 e2e/auth.spec.ts 单独覆盖，它用 test.use 把这里退掉。
    storageState: authStorageState(BASE_URL),
    // 失败时才留痕：全量留痕会把 CI artifact 撑爆，而通过的用例没人看 trace
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // 单个断言的默认超时（自动重试上限）。不用 waitForTimeout 硬等，见契约 §2.3
    actionTimeout: 10_000,
  },

  expect: { timeout: 10_000 },
  timeout: 60_000,

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) },
    },
  ],

  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !isCI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
})
