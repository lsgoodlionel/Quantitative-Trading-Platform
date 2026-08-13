"""LLM 网关核心测试（V3 Wave B-a / I0）

对应契约 docs/contracts/waveBa-llm-gateway.md §五 验收 2。

所有 HTTP 都走 `httpx.MockTransport`：**不连任何外部服务**。注入点是
`app.core.llm.transport.build_client` —— 生产代码路径不变，只把客户端换掉。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.llm import transport
from app.core.llm.anthropic import AnthropicProvider
from app.core.llm.base import (
    ChatMessage,
    LLMNotConfiguredError,
    LLMUnavailableError,
    ToolSpec,
)
from app.core.llm.config_store import save_active, save_config
from app.core.llm.openai_compat import OpenAICompatProvider
from app.core.llm.registry import PROVIDER_PRESETS, build_provider, get_preset
from app.core.llm.service import resolve_active, resolve_named
from tests.fake_redis import FakeRedis

OLLAMA_URL = "http://localhost:11434/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1"

TOOL = ToolSpec(
    name="get_positions",
    description="读取当前持仓",
    parameters={"type": "object", "properties": {"market": {"type": "string"}}},
)


# ── 测试脚手架 ────────────────────────────────────────────────────────────────

class Recorder:
    """记录最后一次出站请求，供断言请求体用。"""

    def __init__(self) -> None:
        self.url: str = ""
        self.headers: dict[str, str] = {}
        self.payload: dict[str, Any] = {}
        self.calls: int = 0


def install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> Recorder:
    """把 build_client 换成挂了 MockTransport 的客户端，并返回请求记录器。"""
    recorder = Recorder()

    def _handle(request: httpx.Request) -> httpx.Response:
        recorder.calls += 1
        recorder.url = str(request.url)
        recorder.headers = dict(request.headers)
        recorder.payload = json.loads(request.content) if request.content else {}
        return handler(request)

    monkeypatch.setattr(
        transport,
        "build_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(_handle), timeout=timeout),
    )
    return recorder


def json_responder(body: dict[str, Any], status_code: int = 200):
    return lambda _request: httpx.Response(status_code, json=body)


OPENAI_OK = {
    "model": "qwen2.5:14b",
    "choices": [{"message": {"role": "assistant", "content": "你好"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
}

ANTHROPIC_OK = {
    "model": "claude-sonnet-4-5",
    "content": [{"type": "text", "text": "你好"}],
    "usage": {"input_tokens": 11, "output_tokens": 3},
}


def openai_provider(model: str = "qwen2.5:14b", api_key: str | None = None):
    return OpenAICompatProvider(
        provider_id="ollama", base_url=OLLAMA_URL, model=model, api_key=api_key
    )


def anthropic_provider(model: str = "claude-sonnet-4-5"):
    return AnthropicProvider(
        provider_id="anthropic", base_url=ANTHROPIC_URL, model=model, api_key="sk-ant-secret-9999"
    )


# ── OpenAI 兼容协议 ───────────────────────────────────────────────────────────

class TestOpenAICompatChat:
    async def test_request_body_and_response_parsing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        response = await openai_provider().chat(
            [
                ChatMessage(role="system", content="你是量化助手"),
                ChatMessage(role="user", content="今天怎么样"),
            ],
            temperature=0.5,
            max_tokens=256,
        )

        assert rec.url == f"{OLLAMA_URL}/chat/completions"
        assert rec.payload["model"] == "qwen2.5:14b"
        assert rec.payload["temperature"] == 0.5
        assert rec.payload["max_tokens"] == 256
        assert rec.payload["stream"] is False
        # system 在 OpenAI 协议里就是一条普通 message（与 Anthropic 的关键差异）
        assert rec.payload["messages"] == [
            {"role": "system", "content": "你是量化助手"},
            {"role": "user", "content": "今天怎么样"},
        ]
        assert response.content == "你好"
        assert response.model == "qwen2.5:14b"
        assert response.usage == {
            "prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14
        }

    async def test_no_api_key_means_no_authorization_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ollama 本地不需要密钥 —— 这是第一等公民路径，不是降级。"""
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        await openai_provider().chat([ChatMessage(role="user", content="hi")])

        assert "authorization" not in {k.lower() for k in rec.headers}

    async def test_api_key_becomes_bearer_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        await openai_provider(api_key="sk-secret-1234").chat(
            [ChatMessage(role="user", content="hi")]
        )

        assert rec.headers["authorization"] == "Bearer sk-secret-1234"

    async def test_tool_calls_are_parsed_from_json_string_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = {
            "model": "gpt-4o",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_positions",
                            "arguments": '{"market": "US"}',
                        },
                    }],
                }
            }],
        }
        rec = install_transport(monkeypatch, json_responder(body))

        response = await openai_provider().chat(
            [ChatMessage(role="user", content="查持仓")], tools=[TOOL]
        )

        assert rec.payload["tools"][0]["type"] == "function"
        assert rec.payload["tools"][0]["function"]["name"] == "get_positions"
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert (call.id, call.name) == ("call_abc", "get_positions")
        # OpenAI 把参数塞在 JSON 字符串里，网关必须解开
        assert call.arguments == {"market": "US"}
        assert response.content == ""

    async def test_tool_result_message_carries_tool_call_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        await openai_provider().chat([
            ChatMessage(role="user", content="查持仓"),
            ChatMessage(role="tool", content="[]", tool_call_id="call_abc"),
        ])

        assert rec.payload["messages"][1] == {
            "role": "tool", "content": "[]", "tool_call_id": "call_abc"
        }

    async def test_http_error_body_message_is_surfaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            json_responder({"error": {"message": "model 'nope' not found"}}, status_code=404),
        )

        with pytest.raises(LLMUnavailableError) as exc:
            await openai_provider(model="nope").chat([ChatMessage(role="user", content="hi")])

        assert "model 'nope' not found" in str(exc.value)

    async def test_connection_failure_becomes_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        install_transport(monkeypatch, _boom)

        with pytest.raises(LLMUnavailableError, match="无法连接"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_non_openai_shaped_body_is_rejected_clearly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, json_responder({"unexpected": True}))

        with pytest.raises(LLMUnavailableError, match="choices"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])


class TestOpenAICompatHealthAndModels:
    async def test_health_really_sends_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """必须真调一次 —— key 错 / 模型名错 / 额度耗尽只有真调才知道。"""
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        health = await openai_provider().health()

        assert rec.calls == 1
        assert rec.url.endswith("/chat/completions")
        assert health.ok is True
        assert health.latency_ms is not None

    async def test_health_reports_error_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            json_responder({"error": {"message": "invalid api key"}}, status_code=401),
        )

        health = await openai_provider(api_key="wrong").health()

        assert health.ok is False
        assert "invalid api key" in (health.error or "")

    async def test_list_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rec = install_transport(
            monkeypatch,
            json_responder({"data": [{"id": "qwen2.5:14b"}, {"id": "llama3.1:8b"}]}),
        )

        models = await openai_provider().list_models()

        assert rec.url == f"{OLLAMA_URL}/models"
        assert models == ("qwen2.5:14b", "llama3.1:8b")


# ── Anthropic 协议差异 ────────────────────────────────────────────────────────

class TestAnthropicProvider:
    async def test_system_is_top_level_field_not_a_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(ANTHROPIC_OK))

        response = await anthropic_provider().chat(
            [
                ChatMessage(role="system", content="你是量化助手"),
                ChatMessage(role="user", content="今天怎么样"),
            ],
            max_tokens=512,
        )

        assert rec.url == f"{ANTHROPIC_URL}/messages"
        assert rec.payload["system"] == "你是量化助手"
        assert rec.payload["messages"] == [{"role": "user", "content": "今天怎么样"}]
        assert rec.payload["max_tokens"] == 512
        assert response.content == "你好"
        # input/output_tokens 被归一化成 OpenAI 的字段名
        assert response.usage == {
            "prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14
        }

    async def test_uses_x_api_key_and_version_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(ANTHROPIC_OK))

        await anthropic_provider().chat([ChatMessage(role="user", content="hi")])

        assert rec.headers["x-api-key"] == "sk-ant-secret-9999"
        assert rec.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in {k.lower() for k in rec.headers}

    async def test_multiple_system_messages_are_joined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(ANTHROPIC_OK))

        await anthropic_provider().chat([
            ChatMessage(role="system", content="规则一"),
            ChatMessage(role="system", content="规则二"),
            ChatMessage(role="user", content="hi"),
        ])

        assert rec.payload["system"] == "规则一\n\n规则二"

    async def test_tool_calls_parsed_from_content_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = {
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "让我查一下"},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "get_positions",
                    "input": {"market": "US"},
                },
            ],
        }
        rec = install_transport(monkeypatch, json_responder(body))

        response = await anthropic_provider().chat(
            [ChatMessage(role="user", content="查持仓")], tools=[TOOL]
        )

        # Anthropic 的工具声明用 input_schema，不是 function.parameters
        assert rec.payload["tools"] == [{
            "name": "get_positions",
            "description": "读取当前持仓",
            "input_schema": TOOL.parameters,
        }]
        assert response.content == "让我查一下"
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert (call.id, call.name) == ("toolu_01", "get_positions")
        # Anthropic 的 input 本来就是 dict，不需要二次 JSON 解析
        assert call.arguments == {"market": "US"}

    async def test_tool_result_becomes_user_turn_with_tool_result_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(ANTHROPIC_OK))

        await anthropic_provider().chat([
            ChatMessage(role="user", content="查持仓"),
            ChatMessage(role="tool", content="[]", tool_call_id="toolu_01"),
        ])

        assert rec.payload["messages"][1] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "[]"}],
        }

    async def test_non_anthropic_shaped_body_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, json_responder({"choices": []}))

        with pytest.raises(LLMUnavailableError, match="content"):
            await anthropic_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_health_really_sends_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(ANTHROPIC_OK))

        health = await anthropic_provider().health()

        assert rec.calls == 1
        assert rec.url.endswith("/messages")
        assert health.ok is True

    async def test_health_reports_error_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            json_responder({"error": {"message": "invalid x-api-key"}}, status_code=401),
        )

        health = await anthropic_provider().health()

        assert health.ok is False
        assert "invalid x-api-key" in (health.error or "")

    async def test_list_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rec = install_transport(
            monkeypatch, json_responder({"data": [{"id": "claude-sonnet-4-5"}]})
        )

        models = await anthropic_provider().list_models()

        assert rec.url == f"{ANTHROPIC_URL}/models"
        assert models == ("claude-sonnet-4-5",)

    async def test_malformed_model_list_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, json_responder({"models": []}))

        with pytest.raises(LLMUnavailableError, match="data"):
            await anthropic_provider().list_models()

    async def test_missing_usage_yields_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            json_responder({"content": [{"type": "text", "text": "hi"}], "usage": {}}),
        )

        response = await anthropic_provider().chat([ChatMessage(role="user", content="hi")])

        assert response.usage == {}


# ── 防御性解析：厂商返回畸形数据时不能崩，也不能静默给出错误结果 ────────────────

class TestDefensiveParsing:
    async def test_malformed_openai_model_list_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, json_responder({"models": ["a"]}))

        with pytest.raises(LLMUnavailableError, match="data"):
            await openai_provider().list_models()

    async def test_unparseable_tool_arguments_are_preserved_for_debugging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """参数不是合法 JSON 时保留原文 —— 静默丢掉会让排查无从下手。"""
        body = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "f", "arguments": "not json at all"}},
                        {"function": {"name": "g", "arguments": '"a string"'}},
                        {"function": {}},          # 无 name，跳过
                        "not-a-dict",              # 非法条目，跳过
                    ],
                }
            }],
        }
        install_transport(monkeypatch, json_responder(body))

        response = await openai_provider().chat([ChatMessage(role="user", content="hi")])

        assert [c.name for c in response.tool_calls] == ["f", "g"]
        assert response.tool_calls[0].arguments == {"__raw__": "not json at all"}
        assert response.tool_calls[1].arguments == {"__raw__": '"a string"'}
        # 没给 id 时兜底生成，调用方总能拿到一个可引用的标识
        assert response.tool_calls[0].id == "call_0"

    async def test_tool_without_parameters_gets_an_empty_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = install_transport(monkeypatch, json_responder(OPENAI_OK))

        await openai_provider().chat(
            [ChatMessage(role="user", content="hi")],
            tools=[ToolSpec(name="noop", description="")],
        )

        assert rec.payload["tools"][0]["function"]["parameters"] == {
            "type": "object", "properties": {}
        }

    async def test_partial_usage_fields_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            json_responder({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": "many"},
            }),
        )

        response = await openai_provider().chat([ChatMessage(role="user", content="hi")])

        assert response.usage == {"prompt_tokens": 5}

    async def test_non_json_error_body_falls_back_to_raw_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(
            monkeypatch,
            lambda _r: httpx.Response(502, text="<html>Bad Gateway</html>"),
        )

        with pytest.raises(LLMUnavailableError, match="Bad Gateway"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_long_error_body_is_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, lambda _r: httpx.Response(500, text="x" * 5000))

        with pytest.raises(LLMUnavailableError) as exc:
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

        assert "…" in str(exc.value)
        assert len(str(exc.value)) < 500

    async def test_empty_error_body_still_yields_a_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, lambda _r: httpx.Response(500, text=""))

        with pytest.raises(LLMUnavailableError, match="无错误详情"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_plain_string_error_field_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ollama 的错误体是 {"error": "..."}，不是 OpenAI 的嵌套对象。"""
        install_transport(
            monkeypatch, json_responder({"error": "model not found"}, status_code=404)
        )

        with pytest.raises(LLMUnavailableError, match="model not found"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_non_json_success_body_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, lambda _r: httpx.Response(200, text="not json"))

        with pytest.raises(LLMUnavailableError, match="不是合法 JSON"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])

    async def test_json_array_top_level_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_transport(monkeypatch, lambda _r: httpx.Response(200, json=[1, 2, 3]))

        with pytest.raises(LLMUnavailableError, match="顶层不是对象"):
            await openai_provider().chat([ChatMessage(role="user", content="hi")])


# ── 预设清单 ─────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_ollama_is_first_and_needs_no_key(self) -> None:
        """本地优先：Ollama 排第一且 requires_key=False。"""
        first = PROVIDER_PRESETS[0]
        assert first.id == "ollama"
        assert first.requires_key is False
        assert first.default_base_url == "http://localhost:11434/v1"

    def test_only_anthropic_uses_its_own_protocol(self) -> None:
        """除 Anthropic 外全部走 OpenAI 兼容协议 —— 所以只有两个适配器。"""
        by_kind = {p.id: p.kind for p in PROVIDER_PRESETS}
        assert by_kind["anthropic"] == "anthropic"
        assert all(
            kind == "openai_compat" for pid, kind in by_kind.items() if pid != "anthropic"
        )

    def test_build_provider_picks_adapter_by_kind(self) -> None:
        ollama = build_provider(
            get_preset("ollama"), base_url=OLLAMA_URL, model="anything"
        )
        claude = build_provider(
            get_preset("anthropic"), base_url=ANTHROPIC_URL, model="x", api_key="k"
        )
        assert isinstance(ollama, OpenAICompatProvider)
        assert isinstance(claude, AnthropicProvider)

    def test_unknown_provider_id_returns_none(self) -> None:
        assert get_preset("not-a-vendor") is None


# ── 服务层：未配置 / 不可用 的分界 ─────────────────────────────────────────────

class TestResolveActive:
    async def test_nothing_configured_raises_not_configured(self) -> None:
        with pytest.raises(LLMNotConfiguredError):
            await resolve_active(FakeRedis())

    async def test_provider_requiring_key_without_key_is_not_ready(self) -> None:
        redis = FakeRedis()
        await save_config(
            redis, "openai", base_url="https://api.openai.com/v1", default_model="gpt-4o"
        )

        # 保存过，但缺 key —— 不算「可用」，等价于没配
        with pytest.raises(LLMNotConfiguredError):
            await resolve_active(redis)

    async def test_ollama_is_ready_without_any_key(self) -> None:
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")

        resolved = await resolve_active(redis)

        assert resolved.preset.id == "ollama"
        assert resolved.model == "qwen2.5:14b"
        assert resolved.explicit is False

    async def test_explicit_active_wins_over_preset_order(self) -> None:
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")
        await save_config(
            redis, "deepseek", base_url="https://api.deepseek.com/v1",
            default_model="deepseek-chat", api_key="sk-ds",
        )
        await save_active(redis, "deepseek", "deepseek-reasoner")

        resolved = await resolve_active(redis)

        assert resolved.preset.id == "deepseek"
        assert resolved.model == "deepseek-reasoner"
        assert resolved.explicit is True

    async def test_active_pointing_at_unusable_provider_falls_back(self) -> None:
        """指向一个没配好的 provider 时回退到可用的，而不是硬报错。"""
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")
        await save_active(redis, "openai", "gpt-4o")

        resolved = await resolve_active(redis)

        assert resolved.preset.id == "ollama"

    async def test_arbitrary_model_name_outside_suggestions_is_allowed(self) -> None:
        """suggested_models 是建议不是白名单：用户拉了什么模型只有他自己知道。"""
        redis = FakeRedis()
        exotic = "my-private-finetune:local-2026"
        assert exotic not in get_preset("ollama").suggested_models
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model=exotic)

        resolved = await resolve_active(redis)

        assert resolved.model == exotic

    async def test_model_override_wins_for_a_single_call(self) -> None:
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")

        resolved = await resolve_active(redis, model_override="llama3.1:8b")

        assert resolved.model == "llama3.1:8b"

    async def test_configured_but_unreachable_raises_unavailable_not_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """配了但连不上 → 503 语义，绝不能和「一个都没配」的 501 混为一谈。"""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        install_transport(monkeypatch, _boom)
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")

        resolved = await resolve_active(redis)
        with pytest.raises(LLMUnavailableError):
            await resolved.provider.chat([ChatMessage(role="user", content="hi")])


class TestResolveNamed:
    async def test_unknown_provider(self) -> None:
        with pytest.raises(LLMNotConfiguredError, match="未知"):
            await resolve_named(FakeRedis(), "not-a-vendor")

    async def test_unconfigured_provider(self) -> None:
        with pytest.raises(LLMNotConfiguredError, match="尚未配置"):
            await resolve_named(FakeRedis(), "ollama")

    async def test_named_resolution_ignores_active_selection(self) -> None:
        redis = FakeRedis()
        await save_config(redis, "ollama", base_url=OLLAMA_URL, default_model="qwen2.5:14b")
        await save_active(redis, "ollama", "llama3.1:8b")

        resolved = await resolve_named(redis, "ollama")

        # /test 测的是这张卡片自己的默认模型，不是全局 active
        assert resolved.model == "qwen2.5:14b"
