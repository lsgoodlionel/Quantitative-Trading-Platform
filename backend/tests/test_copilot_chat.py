"""Copilot 对话端点测试（V3 Wave B-c / I1）

对应契约 docs/contracts/waveBc-copilot.md §四 验收 3：

- tool_calls 参数非法（`__raw__`）→ 回人话错误，不 500
- 连环调用超过轮次上限 → 停下并说明
- 未配置 provider → 引导语而非裸 501

这里自建 FastAPI 应用挂载路由，而不是用 `app.main.app`：`api/v1/router.py`
是主循环负责合并的共享文件，本 Wave 不改它。挂载方式与交付报告里的集成片段一致。

**不连任何模型**：provider 一律是 `tests/copilot_fakes.py` 的脚本回放假货。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import copilot as copilot_endpoint
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.copilot.engine import ERROR_INVALID_ARGUMENTS, MAX_TOOL_ROUNDS
from app.copilot.execute import reset_consumed_drafts
from app.core.database import get_db
from app.core.llm import LLMNotConfiguredError, LLMUnavailableError
from app.core.redis import get_redis
from tests.copilot_fakes import FakeProvider, call, raw_call, text_turn
from tests.fake_redis import FakeRedis


@dataclass(frozen=True)
class _Preset:
    id: str = "ollama"


@dataclass(frozen=True)
class _Resolved:
    provider: Any
    preset: _Preset = _Preset()
    model: str = "qwen2.5:14b"
    explicit: bool = True


def _viewer() -> UserInfo:
    return UserInfo(id="v1", email="viewer@test.local", role="viewer")


def _trader() -> UserInfo:
    return UserInfo(id="t1", email="trader@test.local", role="trader")


@pytest.fixture(autouse=True)
def _clean_idempotency() -> None:
    reset_consumed_drafts()


@pytest.fixture
def app() -> FastAPI:
    """与交付报告中给出的 router.py 集成片段一致的挂载方式。"""
    test_app = FastAPI()
    test_app.include_router(
        copilot_endpoint.router, prefix="/api/v1/copilot", tags=["Copilot"]
    )
    test_app.dependency_overrides[get_redis] = lambda: FakeRedis()
    test_app.dependency_overrides[get_db] = lambda: None
    test_app.dependency_overrides[get_current_user] = _trader
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    async def _resolve(redis: Any, *, model_override: str | None = None) -> _Resolved:
        del redis, model_override
        return _Resolved(provider=provider)

    monkeypatch.setattr(copilot_endpoint, "resolve_active", _resolve)


def _fail_resolve(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    async def _resolve(redis: Any, *, model_override: str | None = None):
        del redis, model_override
        raise exc

    monkeypatch.setattr(copilot_endpoint, "resolve_active", _resolve)


async def _ask(client: AsyncClient, text: str = "帮我看看") -> dict[str, Any]:
    response = await client.post(
        "/api/v1/copilot/chat", json={"messages": [{"role": "user", "content": text}]}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── 未配置 provider ───────────────────────────────────────────────────────────

async def test_missing_provider_returns_guidance_not_a_bare_501(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「还没配模型」对用户是一句引导语，不是一个错误码。"""
    _fail_resolve(monkeypatch, LLMNotConfiguredError("尚未配置任何可用的模型服务。"))

    body = await _ask(client)

    assert body["needs_setup"] is True
    assert body["setup_url"] == "/settings/models"
    assert "尚未配置" in body["text"]
    assert body["drafts"] == []


async def test_unavailable_provider_is_explained_not_swallowed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「依赖挂了」不能被吞掉，也不能变成一个裸 503 —— 要说清下一步去哪儿看。"""
    _fail_resolve(monkeypatch, LLMUnavailableError("Connection refused"))

    body = await _ask(client, "在吗")

    assert body["error_kind"] == "provider_unavailable"
    assert "Connection refused" in body["text"]
    assert body["setup_url"] == "/settings/models"
    assert body["needs_setup"] is False


async def test_provider_failure_during_chat_returns_a_human_message(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Broken(FakeProvider):
        async def chat(self, messages, **kwargs):  # type: ignore[override]
            raise LLMUnavailableError("模型 qwen2.5:14b 不存在")

    _use_provider(monkeypatch, _Broken([text_turn("never")]))

    body = await _ask(client)

    assert body["error_kind"] == "provider_unavailable"
    assert "不存在" in body["text"]


# ── 非法 tool arguments ───────────────────────────────────────────────────────

async def test_unparsable_tool_arguments_return_a_human_error_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地小模型常吐非法 JSON；网关留在 `__raw__` 里，这里翻成人话。"""
    provider = FakeProvider([raw_call("get_quote", '{"symbol": "AAPL"')])
    _use_provider(monkeypatch, provider)

    body = await _ask(client, "AAPL 多少钱")

    assert body["error_kind"] == ERROR_INVALID_ARGUMENTS
    assert "无法解析" in body["text"]
    assert body["cards"] == []
    assert body["drafts"] == []
    # 识别出非法参数后立刻停手，不再多花一次调用
    assert provider.calls == 1


async def test_invalid_arguments_do_not_produce_a_draft(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数解析不出来时，绝不能「猜」一张下单草稿出来。"""
    _use_provider(monkeypatch, FakeProvider([raw_call("draft_order", "buy some AAPL")]))

    body = await _ask(client, "买点 AAPL")

    assert body["drafts"] == []


# ── 轮次上限 ──────────────────────────────────────────────────────────────────

async def test_tool_call_loop_stops_at_the_round_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型不停要求调工具时停下并如实说明，不无限烧 token。"""
    provider = FakeProvider([call("get_positions", {"market": "US"})])
    _use_provider(monkeypatch, provider)

    body = await _ask(client, "一直查")

    assert body["truncated"] is True
    assert body["rounds"] == MAX_TOOL_ROUNDS
    assert "轮次上限" in body["text"]
    # 5 轮工具 + 1 次禁用工具的收尾调用
    assert provider.calls == MAX_TOOL_ROUNDS + 1
    assert provider.tool_specs_seen[-1] is None


# ── 正常流程 ──────────────────────────────────────────────────────────────────

async def test_read_only_tool_result_becomes_a_card(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(
        monkeypatch,
        FakeProvider([call("get_positions", {"market": "US"}), text_turn("你持有 4 个标的")]),
    )

    body = await _ask(client, "我有哪些持仓")

    assert body["text"] == "你持有 4 个标的"
    assert [c["kind"] for c in body["cards"]] == ["positions"]
    assert body["cards"][0]["data"]["positions"]


async def test_write_tool_yields_a_draft_with_every_parameter(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """草稿卡片必须能看到标的 / 方向 / 数量 / 价格 / 预估金额。"""
    _use_provider(
        monkeypatch,
        FakeProvider(
            [
                call(
                    "draft_order",
                    {
                        "symbol": "AAPL", "market": "US", "side": "BUY", "qty": 100,
                        "order_type": "LIMIT", "limit_price": 190.0,
                    },
                ),
                text_turn("已生成草稿，请确认"),
            ]
        ),
    )

    body = await _ask(client, "买 100 股 AAPL，限价 190")

    assert len(body["drafts"]) == 1
    draft = body["drafts"][0]
    labels = {f["label"]: f["value"] for f in draft["fields"]}
    assert labels["标的"] == "AAPL（US）"
    assert labels["数量"] == "100 股"
    assert labels["预估金额"] == "19,000.00"
    assert draft["endpoint"] == "POST /api/v1/orders"
    assert draft["params"]["limit_price"] == 190.0


async def test_tool_execution_error_is_reported_in_words(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具报错要回灌给模型，让它用人话解释，而不是冒泡成 500。"""
    provider = FakeProvider(
        [call("get_quote", {"symbol": "NOPE", "market": "US"}), text_turn("没查到这个标的")]
    )
    _use_provider(monkeypatch, provider)

    body = await _ask(client, "NOPE 多少钱")

    assert body["text"] == "没查到这个标的"
    assert any("错误" in m.content for m in provider.last_messages)


# ── 工具清单与草稿执行 ────────────────────────────────────────────────────────

async def test_tools_endpoint_exposes_the_confirmation_boundary(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/copilot/tools")

    assert response.status_code == 200
    flags = {t["name"]: t["requires_confirmation"] for t in response.json()}
    assert flags["get_quote"] is False
    assert flags["draft_order"] is True
    assert flags["draft_rebalance"] is True


async def test_draft_execution_requires_trader_role(app: FastAPI) -> None:
    """Copilot 直接调端点函数会绕过它自己的角色依赖，所以这里必须自己把关。"""
    app.dependency_overrides[get_current_user] = _viewer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/api/v1/copilot/drafts/execute",
            json={"draft_id": "d-1", "action": "order", "params": {}},
        )

    assert response.status_code == 403


async def test_draft_execution_rejects_invalid_params(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/copilot/drafts/execute",
        json={"draft_id": "d-2", "action": "order", "params": {"symbol": "AAPL"}},
    )

    assert response.status_code == 422
