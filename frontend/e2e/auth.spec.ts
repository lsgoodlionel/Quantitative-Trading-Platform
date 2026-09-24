// 登录冒烟（契约 §3）。
//
// 三条 Playbook 都跳过登录表单，直接复用 storageState。登录本身只在这里覆盖 ——
// 这样登录一旦坏掉，红的是这一条，不是四条一起红成一片、看不出根因。

import { ACCESS_TOKEN_KEY, AUTH_STORE_KEY, persistedAuthState } from "./fixtures/session"
import { ADMIN_CREDENTIALS } from "./fixtures/auth"
import { expect, test } from "./fixtures/test"

// 退掉 playwright.config.ts 里的默认已登录态：这几条用例就是要从未登录开始
test.use({ storageState: { cookies: [], origins: [] } })

// 登录表单的两个输入没有 htmlFor/id 关联，getByLabel 定位不到。
// 退而用 autocomplete —— 它是浏览器和密码管理器真正读的语义属性，
// 不是样式类名，改版式不会把它带走（契约 §2.2）。
const USERNAME_INPUT = 'input[autocomplete="username"]'
const PASSWORD_INPUT = 'input[autocomplete="current-password"]'

test.describe("登录", () => {
  test("未登录访问受保护页面会被送回登录页", async ({ page }) => {
    await page.goto("/portfolio")

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible()
  })

  test("口令错误时就地报错，不跳转", async ({ page }) => {
    await page.goto("/login")

    await page.locator(USERNAME_INPUT).fill(ADMIN_CREDENTIALS.username)
    await page.locator(PASSWORD_INPUT).fill("wrong-password")
    await page.getByRole("button", { name: "登录" }).click()

    // 后端 401 的 detail 会被 Login.tsx 原样显示
    await expect(page.getByText("Incorrect username or password")).toBeVisible()
    await expect(page).toHaveURL(/\/login$/)
  })

  test("登录成功后进入应用，并写入可复用的登录态", async ({ page }) => {
    await page.goto("/login")

    await page.locator(USERNAME_INPUT).fill(ADMIN_CREDENTIALS.username)
    await page.locator(PASSWORD_INPUT).fill(ADMIN_CREDENTIALS.password)
    await page.getByRole("button", { name: "登录" }).click()

    // Login.tsx 成功后 navigate("/")，落在仪表盘
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole("heading", { name: "仪表盘", exact: true })).toBeVisible()
    // 角色徽章显示「管理员」= token 里的 role 被解出来了（不是 fail-safe 的 viewer）
    await expect(page.getByTitle("当前角色：管理员")).toBeVisible()

    // ── 守住 e2e/fixtures/session.ts 那份 storageState 镜像 ──
    // 三条 Playbook 靠手写的 localStorage 免登录。应用真正写出来的东西一旦换了形状
    // （改了 key、改了 zustand persist 的信封），必须在这里先红，
    // 否则 Playbook 会安静地退回未登录、然后以一个毫不相干的错误失败。
    const stored = await page.evaluate((keys) => ({
      token: window.localStorage.getItem(keys.token),
      store: window.localStorage.getItem(keys.store),
    }), { token: ACCESS_TOKEN_KEY, store: AUTH_STORE_KEY })

    expect(stored.token).toBeTruthy()
    expect(JSON.parse(stored.store ?? "{}")).toMatchObject({
      state: {
        user: persistedAuthState.state.user,
        role: persistedAuthState.state.role,
        isAuthenticated: true,
      },
      version: persistedAuthState.version,
    })
  })
})
