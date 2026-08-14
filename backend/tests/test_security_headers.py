"""安全响应头（V3 Wave D-a / J5）。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security_headers import (
    BASE_SECURITY_HEADERS,
    HSTS_HEADER,
    SecurityHeadersMiddleware,
)

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def build_probe_app(*, enable_hsts: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=enable_hsts)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "1"}

    return app


async def get_headers(app: FastAPI, url: str = "http://test/ping", **kwargs: object) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(url, **kwargs)  # type: ignore[arg-type]
    return dict(response.headers)


class TestBaseHeaders:
    @pytest.mark.parametrize(("name", "value"), sorted(EXPECTED_HEADERS.items()))
    async def test_header_is_always_present(self, name: str, value: str) -> None:
        headers = await get_headers(build_probe_app(enable_hsts=False))

        assert headers[name] == value

    async def test_expected_set_matches_the_module_constant(self) -> None:
        # 防止有人往中间件里加了头却没人知道，或删了头而测试仍然「全绿」。
        assert BASE_SECURITY_HEADERS == EXPECTED_HEADERS

    async def test_real_app_serves_the_headers(self) -> None:
        from app.main import app

        headers = await get_headers(app, url="http://test/health")

        for name, value in EXPECTED_HEADERS.items():
            assert headers[name] == value


class TestHsts:
    """HSTS 是本期最容易伤到自己的一个头。

    在 http://localhost 上下发，会把开发者浏览器对该 host 锁死在 https，
    之后本地开发全部打不开，且要进 chrome://net-internals/#hsts 才能清掉。
    """

    async def test_not_sent_when_disabled(self) -> None:
        headers = await get_headers(build_probe_app(enable_hsts=False))

        assert HSTS_HEADER not in headers

    async def test_not_sent_over_plain_http_even_when_enabled(self) -> None:
        headers = await get_headers(build_probe_app(enable_hsts=True))

        assert HSTS_HEADER not in headers

    async def test_sent_over_https_when_enabled(self) -> None:
        app = build_probe_app(enable_hsts=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as client:
            response = await client.get("/ping")

        assert response.headers[HSTS_HEADER] == "max-age=31536000; includeSubDomains"

    async def test_sent_behind_a_tls_terminating_proxy(self) -> None:
        # 生产里 TLS 由 Nginx 终止，到达应用时 scope["scheme"] 是 http。
        # 只看 scheme 的话生产环境永远不会下发 HSTS。
        headers = await get_headers(
            build_probe_app(enable_hsts=True),
            headers={"x-forwarded-proto": "https"},
        )

        assert HSTS_HEADER in headers

    async def test_real_app_ties_hsts_to_production(self) -> None:
        from app.main import app

        entries = [m for m in app.user_middleware if m.cls is SecurityHeadersMiddleware]

        assert len(entries) == 1
        assert entries[0].kwargs["enable_hsts"] is settings.is_production

    async def test_development_app_never_sends_hsts(self) -> None:
        from app.main import app

        assert settings.is_production is False, "本测试假设测试环境不是 production"
        headers = await get_headers(app, url="http://test/health")

        assert HSTS_HEADER not in headers
