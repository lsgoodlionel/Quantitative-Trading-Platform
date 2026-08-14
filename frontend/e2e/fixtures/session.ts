// 登录态（契约 §3）。
//
// 三条 Playbook 都要求已登录，但**都不点登录表单** —— 那会让登录一坏就三条一起红，
// 根因反而看不出来。这里直接构造 storageState，登录本身由 auth.spec.ts 单独覆盖。
//
// ⚠️ 下面这份 localStorage 是 `src/stores/auth.ts` 写出来的东西的镜像：
//   - `access_token`   ← login() 里的 localStorage.setItem（api.ts 取它拼 Authorization 头）
//   - `quantbot-auth`  ← zustand persist 的 { state, version } 信封，
//                        partialize 后只留 token / user / role / isAuthenticated
// 存储格式如果哪天改了，auth.spec.ts 里那条「登录后写入的 storage 与夹具同构」
// 的断言会先红 —— 那正是这份镜像失效的唯一预警，别把它删了。

import { ACCESS_TOKEN, ADMIN_CREDENTIALS, CURRENT_USER } from "./auth"

/** zustand persist 的信封版本号；未在 store 里显式声明 version 时为 0。 */
const PERSIST_VERSION = 0

export const AUTH_STORE_KEY = "quantbot-auth"
export const ACCESS_TOKEN_KEY = "access_token"

export const persistedAuthState = {
  state: {
    token: ACCESS_TOKEN,
    user: ADMIN_CREDENTIALS.username,
    role: CURRENT_USER.role,
    isAuthenticated: true,
  },
  version: PERSIST_VERSION,
}

/**
 * 生成 Playwright 的 storageState。
 *
 * origin 必须与 baseURL 完全一致（含端口），否则浏览器不会把这些键注入进去，
 * 应用会当成未登录、被路由守卫弹回 /login。
 */
export function authStorageState(origin: string) {
  return {
    cookies: [],
    origins: [
      {
        origin,
        localStorage: [
          { name: ACCESS_TOKEN_KEY, value: ACCESS_TOKEN },
          { name: AUTH_STORE_KEY, value: JSON.stringify(persistedAuthState) },
        ],
      },
    ],
  }
}
