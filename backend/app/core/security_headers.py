"""安全响应头中间件。

写成纯 ASGI 中间件而不是 `BaseHTTPMiddleware` 的子类：后者会把响应包进
一个 `StreamingResponse` 中转，本项目有 SSE 端点（`/api/v1/stream/*`），
经它中转后事件会被缓冲、且客户端断开的传播行为会变。纯 ASGI 只改
`http.response.start` 这一条消息的头部，对响应体零介入。

本期**不加 CSP**：前端用了 Monaco 编辑器，它需要 worker 与 blob: URL，
一个没在真浏览器里逐条验证过的 CSP 会静默打碎编辑器（页面不报错，只是编辑器空白）。
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 与协议、环境都无关，恒定下发。
BASE_SECURITY_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}

HSTS_HEADER = "strict-transport-security"
HSTS_VALUE = "max-age=31536000; includeSubDomains"


def _is_https(scope: Scope) -> bool:
    """判断这次请求最终是不是走 HTTPS 到达用户的。

    生产里 TLS 由反向代理终止（见 D-c 的 Nginx 配置），到达应用时 `scope["scheme"]`
    是 http，所以必须同时看 `X-Forwarded-Proto` —— 只看 scheme 的话，
    生产环境永远不会下发 HSTS。
    """
    if scope.get("scheme") == "https":
        return True
    forwarded = Headers(scope=scope).get("x-forwarded-proto", "")
    # 多级代理会串成 "https, http"，第一段才是最靠近用户的那一跳。
    return forwarded.split(",")[0].strip().lower() == "https"


class SecurityHeadersMiddleware:
    """给所有 HTTP 响应补上安全头。

    `enable_hsts` 只应在 production 打开。在 `http://localhost` 上下发 HSTS 会把
    开发者浏览器对该 host 锁死在 https，之后本地开发全部打不开，
    而且要进 `chrome://net-internals/#hsts` 才能清掉。
    """

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 在请求阶段就定下来：send_wrapper 里再算会对每个响应重复解析头部。
        send_hsts = self.enable_hsts and _is_https(scope)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in BASE_SECURITY_HEADERS.items():
                    # setdefault：端点若已显式设置某个头（例如某处需要放开
                    # X-Frame-Options 做嵌入），以端点的决定为准。
                    headers.setdefault(name, value)
                if send_hsts:
                    headers.setdefault(HSTS_HEADER, HSTS_VALUE)
            await send(message)

        await self.app(scope, receive, send_with_headers)
