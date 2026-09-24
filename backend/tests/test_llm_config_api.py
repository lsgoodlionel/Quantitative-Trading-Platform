"""LLM 网关 API 端点测试（V3 Wave B-a / I0）

对应契约 docs/contracts/waveBa-llm-gateway.md §五 验收 2（501/503）与 3（配置 API）。

这里自建 FastAPI 应用挂载路由，而不是用 `app.main.app`：`api/v1/router.py`
是主循环负责合并的共享文件，本 Wave 不改它。挂载方式与交付报告里给出的集成片段
完全一致，等于顺带把那段片段也测了。

HTTP 出站一律走 `httpx.MockTransport`，**不连任何外部服务**。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import llm
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.core.llm import transport
from app.core.llm.registry import get_preset
from app.core.redis import get_redis
from tests.fake_redis import FakeRedis

OLLAMA_URL = "http://localhost:11434/v1"
FULL_KEY = "sk-proj-supersecret-abcdefgh-1234"

OPENAI_OK = {
    "model": "qwen2.5:14b",
    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


def _trader() -> UserInfo:
    return UserInfo(id="test-trader", email="trader@test.local", role="trader")


def _viewer() -> UserInfo:
    return UserInfo(id="test-viewer", email="viewer@test.local", role="viewer")


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(redis: FakeRedis) -> FastAPI:
    """与交付报告中给出的 router.py 集成片段一致的挂载方式。"""
    test_app = FastAPI()
    test_app.include_router(llm.router, prefix="/api/v1/llm", tags=["LLM Gateway"])
    test_app.dependency_overrides[get_redis] = lambda: redis
    test_app.dependency_overrides[get_current_user] = _trader
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def mock_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """替换 build_client，返回捕获到的出站请求列表。"""
    captured: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    monkeypatch.setattr(
        transport,
        "build_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(_handle), timeout=timeout
        ),
    )
    return captured


def ok_json(body: dict[str, Any], status_code: int = 200):
    return lambda _request: httpx.Response(status_code, json=body)


async def save_ollama(client: AsyncClient, model: str = "qwen2.5:14b") -> None:
    resp = await client.put(
        "/api/v1/llm/providers/ollama",
        json={"base_url": OLLAMA_URL, "default_model": model},
    )
    assert resp.status_code == 200


# ── GET /providers ───────────────────────────────────────────────────────────

class TestListProviders:
    async def test_lists_all_presets_with_ollama_first(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/llm/providers")).json()

        ids = [p["id"] for p in body["providers"]]
        assert ids[0] == "ollama"
        assert set(ids) >= {"ollama", "openai", "deepseek", "moonshot", "dashscope", "anthropic"}
        assert body["providers"][0]["requires_key"] is False
        assert body["providers"][0]["suggested_models"]

    async def test_nothing_configured_reports_inactive_not_error(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/llm/providers")).json()

        assert all(p["configured"] is False for p in body["providers"])
        assert body["active"] == {
            "configured": False, "provider_id": None,
            "provider_label": None, "model": None, "explicit": False,
        }

    async def test_ollama_becomes_ready_without_any_key(self, client: AsyncClient) -> None:
        await save_ollama(client)

        body = (await client.get("/api/v1/llm/providers")).json()
        ollama = next(p for p in body["providers"] if p["id"] == "ollama")

        assert (ollama["configured"], ollama["ready"]) == (True, True)
        assert ollama["key_hint"] is None
        assert body["active"]["provider_id"] == "ollama"

    async def test_key_requiring_provider_without_key_is_configured_but_not_ready(
        self, client: AsyncClient
    ) -> None:
        await client.put(
            "/api/v1/llm/providers/openai",
            json={"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o"},
        )

        body = (await client.get("/api/v1/llm/providers")).json()
        openai = next(p for p in body["providers"] if p["id"] == "openai")

        assert (openai["configured"], openai["ready"]) == (True, False)


# ── 密钥掩码 ─────────────────────────────────────────────────────────────────

class TestKeyMasking:
    async def test_full_key_never_appears_in_any_response(
        self, client: AsyncClient
    ) -> None:
        save = await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": FULL_KEY,
            },
        )
        listing = await client.get("/api/v1/llm/providers")

        for response in (save, listing):
            assert FULL_KEY not in response.text

        hint = save.json()["key_hint"]
        assert hint is not None
        assert hint.startswith("sk")
        assert hint.endswith("1234")
        assert "•" in hint

    async def test_short_key_is_fully_masked(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": "abc123",
            },
        )

        # 太短的串保留头尾几乎等于明文，整体打码
        assert resp.json()["key_hint"] == "••••••"
        assert "abc123" not in resp.text


# ── 留空 = 保持原值 ───────────────────────────────────────────────────────────

class TestApiKeyPreservation:
    async def test_empty_api_key_keeps_existing_secret(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """用户改 base_url 时不该被迫重新粘一遍密钥。"""
        await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": FULL_KEY,
            },
        )

        resp = await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://proxy.internal/v1",
                "default_model": "gpt-4o-mini",
                "api_key": "",
            },
        )

        assert resp.json()["base_url"] == "https://proxy.internal/v1"
        assert resp.json()["default_model"] == "gpt-4o-mini"
        assert resp.json()["ready"] is True
        # 存储里仍是原来的完整 key
        assert await redis.hget("llm_config:openai", "api_key") == FULL_KEY

    async def test_omitted_api_key_keeps_existing_secret(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": FULL_KEY,
            },
        )

        await client.put(
            "/api/v1/llm/providers/openai",
            json={"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o"},
        )

        assert await redis.hget("llm_config:openai", "api_key") == FULL_KEY

    async def test_new_key_replaces_old_one(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        for key in (FULL_KEY, "sk-rotated-99998888"):
            await client.put(
                "/api/v1/llm/providers/openai",
                json={
                    "base_url": "https://api.openai.com/v1",
                    "default_model": "gpt-4o",
                    "api_key": key,
                },
            )

        assert await redis.hget("llm_config:openai", "api_key") == "sk-rotated-99998888"

    async def test_delete_removes_the_secret(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """清空密钥是独立的 DELETE 动作，不是「保存空串」。"""
        await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": FULL_KEY,
            },
        )

        resp = await client.delete("/api/v1/llm/providers/openai")

        assert resp.status_code == 204
        assert await redis.hgetall("llm_config:openai") == {}


# ── 模型名不受 suggested_models 限制 ──────────────────────────────────────────

class TestArbitraryModelNames:
    async def test_model_outside_suggestions_is_accepted(
        self, client: AsyncClient
    ) -> None:
        exotic = "my-private-finetune:local-2026"
        await save_ollama(client, model=exotic)

        body = (await client.get("/api/v1/llm/providers")).json()
        ollama = next(p for p in body["providers"] if p["id"] == "ollama")

        assert ollama["default_model"] == exotic
        assert exotic not in ollama["suggested_models"]

    async def test_active_accepts_arbitrary_model(self, client: AsyncClient) -> None:
        await save_ollama(client)

        resp = await client.put(
            "/api/v1/llm/active",
            json={"provider_id": "ollama", "model": "whatever-i-pulled:latest"},
        )

        assert resp.status_code == 200
        assert resp.json()["model"] == "whatever-i-pulled:latest"


# ── RBAC ─────────────────────────────────────────────────────────────────────

class TestRbac:
    @pytest.fixture(autouse=True)
    def as_viewer(self, app: FastAPI) -> None:
        app.dependency_overrides[get_current_user] = _viewer

    async def test_viewer_cannot_save(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/llm/providers/ollama",
            json={"base_url": OLLAMA_URL, "default_model": "qwen2.5:14b"},
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_delete(self, client: AsyncClient) -> None:
        assert (await client.delete("/api/v1/llm/providers/ollama")).status_code == 403

    async def test_viewer_cannot_test_connection(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/llm/providers/ollama/test")
        assert resp.status_code == 403

    async def test_viewer_cannot_switch_active(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/llm/active", json={"provider_id": "ollama", "model": "x"}
        )
        assert resp.status_code == 403

    async def test_viewer_can_still_read_provider_list(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/llm/providers")).status_code == 200


class TestUnknownProvider:
    async def test_save_unknown_provider_is_404(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/llm/providers/not-a-vendor",
            json={"base_url": "http://x/v1", "default_model": "m"},
        )
        assert resp.status_code == 404


# ── POST /providers/{id}/test —— 真实发一次请求 ───────────────────────────────

class TestConnectionTest:
    async def test_success_really_hits_chat_completions(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = mock_http(monkeypatch, ok_json(OPENAI_OK))
        await save_ollama(client)

        body = (await client.post("/api/v1/llm/providers/ollama/test")).json()

        # 不是 ping base_url —— 真的发了一次最小对话
        assert len(captured) == 1
        assert str(captured[0].url).endswith("/chat/completions")
        assert body["ok"] is True
        assert body["model"] == "qwen2.5:14b"
        assert body["latency_ms"] is not None

    async def test_bad_key_reports_vendor_message(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_http(
            monkeypatch,
            ok_json({"error": {"message": "Incorrect API key provided"}}, status_code=401),
        )
        await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": "sk-wrong-key-0000",
            },
        )

        body = (await client.post("/api/v1/llm/providers/openai/test")).json()

        assert body["ok"] is False
        assert "Incorrect API key provided" in body["error"]

    async def test_unreachable_endpoint_reports_connection_error(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        mock_http(monkeypatch, _boom)
        await save_ollama(client)

        body = (await client.post("/api/v1/llm/providers/ollama/test")).json()

        assert body["ok"] is False
        assert "无法连接" in body["error"]

    async def test_unconfigured_provider_reports_not_configured(
        self, client: AsyncClient
    ) -> None:
        body = (await client.post("/api/v1/llm/providers/ollama/test")).json()

        assert body["ok"] is False
        assert "尚未配置" in body["error"]

    async def test_model_override_is_tested_without_saving(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = mock_http(monkeypatch, ok_json(OPENAI_OK))
        await save_ollama(client)

        body = (
            await client.post("/api/v1/llm/providers/ollama/test?model=llama3.1:8b")
        ).json()

        assert json.loads(captured[0].content)["model"] == "llama3.1:8b"
        assert body["model"] == "llama3.1:8b"


# ── GET /providers/{id}/models ───────────────────────────────────────────────

class TestModelListing:
    async def test_pulls_available_models(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = mock_http(
            monkeypatch, ok_json({"data": [{"id": "qwen2.5:14b"}, {"id": "llama3.1:8b"}]})
        )
        await save_ollama(client)

        body = (await client.get("/api/v1/llm/providers/ollama/models")).json()

        assert str(captured[0].url).endswith("/models")
        assert body["models"] == ["qwen2.5:14b", "llama3.1:8b"]

    async def test_unconfigured_is_501(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/llm/providers/ollama/models")).status_code == 501

    async def test_unreachable_is_503(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        mock_http(monkeypatch, _boom)
        await save_ollama(client)

        assert (await client.get("/api/v1/llm/providers/ollama/models")).status_code == 503

    async def test_provider_without_listing_support_is_501(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """当前所有预设都支持列模型；这里模拟一个不支持的，锁住这条分支的行为。"""
        preset = replace(get_preset("ollama"), supports_model_listing=False)
        monkeypatch.setattr(llm, "get_preset", lambda pid: preset if pid == "ollama" else None)
        await save_ollama(client)

        resp = await client.get("/api/v1/llm/providers/ollama/models")

        assert resp.status_code == 501
        assert "不支持列举模型" in resp.json()["detail"]


# ── /active ──────────────────────────────────────────────────────────────────

class TestActiveSelection:
    async def test_unset_active_is_not_an_error(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/llm/active")

        # 状态查询不该报 501 —— 模型管理页要靠它渲染引导文案
        assert resp.status_code == 200
        assert resp.json()["configured"] is False

    async def test_auto_selection_is_marked_non_explicit(
        self, client: AsyncClient
    ) -> None:
        await save_ollama(client)

        body = (await client.get("/api/v1/llm/active")).json()

        assert body["provider_id"] == "ollama"
        assert body["explicit"] is False
        assert body["provider_label"] == "Ollama（本地）"

    async def test_switching_marks_explicit(self, client: AsyncClient) -> None:
        await save_ollama(client)

        await client.put(
            "/api/v1/llm/active", json={"provider_id": "ollama", "model": "llama3.1:8b"}
        )
        body = (await client.get("/api/v1/llm/active")).json()

        assert (body["model"], body["explicit"]) == ("llama3.1:8b", True)

    async def test_deleting_active_provider_clears_the_selection(
        self, client: AsyncClient
    ) -> None:
        await save_ollama(client)
        await client.put(
            "/api/v1/llm/active", json={"provider_id": "ollama", "model": "llama3.1:8b"}
        )

        await client.delete("/api/v1/llm/providers/ollama")
        body = (await client.get("/api/v1/llm/active")).json()

        assert body["configured"] is False


# ── POST /chat —— 501 与 503 的分界 ──────────────────────────────────────────

class TestChatEndpoint:
    async def test_nothing_configured_is_501(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/llm/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        )

        assert resp.status_code == 501
        assert "Ollama" in resp.json()["detail"]

    async def test_configured_but_unreachable_is_503_not_501(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """「Ollama 没启动」不能看起来像「平台不支持 AI」。"""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        mock_http(monkeypatch, _boom)
        await save_ollama(client)

        resp = await client.post(
            "/api/v1/llm/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        )

        assert resp.status_code == 503

    async def test_invalid_key_is_503(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_http(monkeypatch, ok_json({"error": {"message": "bad key"}}, status_code=401))
        await client.put(
            "/api/v1/llm/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-4o",
                "api_key": FULL_KEY,
            },
        )

        resp = await client.post(
            "/api/v1/llm/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        )

        assert resp.status_code == 503

    async def test_successful_chat_returns_normalized_payload(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = mock_http(monkeypatch, ok_json(OPENAI_OK))
        await save_ollama(client)

        body = (
            await client.post(
                "/api/v1/llm/chat",
                json={
                    "messages": [
                        {"role": "system", "content": "你是量化助手"},
                        {"role": "user", "content": "hi"},
                    ],
                    "temperature": 0.1,
                },
            )
        ).json()

        assert json.loads(captured[0].content)["temperature"] == 0.1
        assert body["content"] == "pong"
        assert body["provider_id"] == "ollama"
        assert body["model"] == "qwen2.5:14b"
        assert body["usage"]["total_tokens"] == 6
        assert body["tool_calls"] == []

    async def test_tools_are_forwarded_and_calls_returned(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response_body = {
            "model": "qwen2.5:14b",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "get_positions", "arguments": '{"market":"US"}'},
                    }],
                }
            }],
        }
        captured = mock_http(monkeypatch, ok_json(response_body))
        await save_ollama(client)

        body = (
            await client.post(
                "/api/v1/llm/chat",
                json={
                    "messages": [{"role": "user", "content": "查持仓"}],
                    "tools": [{
                        "name": "get_positions",
                        "description": "读取当前持仓",
                        "parameters": {"type": "object", "properties": {}},
                    }],
                },
            )
        ).json()

        assert json.loads(captured[0].content)["tools"][0]["function"]["name"] == "get_positions"
        assert body["tool_calls"] == [
            {"id": "call_1", "name": "get_positions", "arguments": {"market": "US"}}
        ]

    async def test_model_override_does_not_change_stored_active(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = mock_http(monkeypatch, ok_json(OPENAI_OK))
        await save_ollama(client)

        await client.post(
            "/api/v1/llm/chat",
            json={"messages": [{"role": "user", "content": "hi"}], "model": "llama3.1:8b"},
        )

        assert json.loads(captured[0].content)["model"] == "llama3.1:8b"
        assert (await client.get("/api/v1/llm/active")).json()["model"] == "qwen2.5:14b"

    async def test_empty_message_list_is_422(self, client: AsyncClient) -> None:
        assert (await client.post("/api/v1/llm/chat", json={"messages": []})).status_code == 422

    async def test_viewer_may_chat(
        self, app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Copilot 面向所有已登录角色；写配置才需要 Trader。"""
        mock_http(monkeypatch, ok_json(OPENAI_OK))
        await save_ollama(client)
        app.dependency_overrides[get_current_user] = _viewer

        resp = await client.post(
            "/api/v1/llm/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        )

        assert resp.status_code == 200
