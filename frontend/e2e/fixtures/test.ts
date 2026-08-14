// 扩展后的 test：所有用例自动装上 API 桩。
//
// 用例只从这里 import test/expect，不直接从 @playwright/test 拿 —— 保证没有哪条用例
// 漏装桩之后偷偷打到真实后端上。

import { test as base, expect } from "@playwright/test"

import { stubApi } from "./stub-api"

export const BASE_URL = "http://localhost:5173"

export const test = base.extend({
  page: async ({ page }, use) => {
    await stubApi(page)
    await use(page)
  },
})

export { expect }
