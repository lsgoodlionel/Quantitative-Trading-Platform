// 登录相关夹具。
//
// 形状来源（逐字段核对过，勿照 TS 类型手写）：
//   Token    → backend/app/api/v1/endpoints/auth.py:73（access_token / token_type / expires_in）
//   UserInfo → backend/app/api/v1/endpoints/auth.py:79（id / email / role）
//
// 注意 `Token` 比前端 `Login.tsx` 的 `LoginResponse` 多一个 `expires_in`：
// 前端只取 access_token，但夹具按后端真实结构给全，避免哪天前端开始读它时
// 测试还是绿的。

/** 内置管理员账户，与 backend auth.py 的 `_BUILTIN_USERS["admin"]` 一致。 */
export const ADMIN_CREDENTIALS = {
  username: "admin",
  password: "admin123",
} as const

const ADMIN_USER_ID = "00000000-0000-0000-0000-000000000001"
const ADMIN_EMAIL = "admin@quantbot.local"
const ADMIN_ROLE = "admin"

// 用 btoa 而非 Node 的 Buffer：仓库没装 @types/node，tsc 认不出 Buffer；
// btoa 在 lib.dom 里有声明、Node 18+ 也是全局可用，两边都过得去。
// 只处理 ASCII 的 JSON，不涉及 btoa 的 latin1 限制。
function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

/**
 * 构造一个结构合法、签名是假的 JWT。
 *
 * 前端只用 `atob` 读 payload 取 role（src/lib/jwt.ts），从不验签 —— 验签是后端的活。
 * 所以这里不需要真密钥，但**三段式结构和 payload 字段必须真**，
 * 否则 `getRoleFromToken` 返回 null，角色徽章会静默降级成 viewer。
 */
export function makeFakeJwt(role: string = ADMIN_ROLE): string {
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }))
  const payload = base64Url(
    JSON.stringify({
      sub: ADMIN_USER_ID,
      role,
      email: ADMIN_EMAIL,
      // 远期过期时间：前端不校验 exp，但保持字段语义正确
      exp: 4102444800,
      iat: 1700000000,
    }),
  )
  return `${header}.${payload}.e2e-not-a-real-signature`
}

export const ACCESS_TOKEN = makeFakeJwt()

/** POST /api/v1/auth/token 的响应（后端 `Token`）。 */
export const LOGIN_TOKEN = {
  access_token: ACCESS_TOKEN,
  token_type: "bearer",
  expires_in: 1800,
}

/** GET /api/v1/auth/me 的响应（后端 `UserInfo`）。 */
export const CURRENT_USER = {
  id: ADMIN_USER_ID,
  email: ADMIN_EMAIL,
  role: ADMIN_ROLE,
}

/** 401 响应体，与 FastAPI 的 `HTTPException(detail=...)` 一致。 */
export const LOGIN_FAILURE = {
  detail: "Incorrect username or password",
}
