// 轻量 JWT 解析：仅读取 payload（不校验签名，签名校验由后端负责）。
// 用于登录后从 access_token 提取 role，避免额外往返请求。

export interface JwtPayload {
  sub?: string
  role?: string
  email?: string
  exp?: number
  iat?: number
}

function base64UrlDecode(segment: string): string {
  const base64 = segment.replace(/-/g, "+").replace(/_/g, "/")
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=")
  return atob(padded)
}

/** 解析 JWT payload；格式非法时返回 null（绝不抛异常）。 */
export function decodeJwtPayload(token: string | null | undefined): JwtPayload | null {
  if (!token) return null
  const parts = token.split(".")
  if (parts.length !== 3) return null
  try {
    return JSON.parse(base64UrlDecode(parts[1])) as JwtPayload
  } catch {
    return null
  }
}

/** 从 token 中提取 role 字符串；无法解析时返回 null。 */
export function getRoleFromToken(token: string | null | undefined): string | null {
  return decodeJwtPayload(token)?.role ?? null
}
