"""AI 个股研报测试（V3 Wave C-a / I4）

对应契约 docs/contracts/waveCa-ai-reports.md §五 验收 2：

- `sources` 包含实际喂给模型的新闻标题（不是空列表）
- 无新闻时该节写明「无可用新闻」，且提示词含禁止臆测的指令
- 未配置 provider → 501 且 detail 指向 /settings/models

这里自建 FastAPI 应用挂载路由，挂载方式与 `api/v1/router.py` 里的注册一致。
**不连任何模型、不碰任何数据源**：provider 是脚本回放的假货，快照要么直接构造，
要么用假服务喂进 `build_stock_snapshot`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai_reports import snapshot as snapshot_mod
from app.ai_reports.common import extract_json_object
from app.ai_reports.snapshot import (
    MIN_BARS,
    NO_NEWS_TEXT,
    NewsSource,
    SnapshotError,
    StockSnapshot,
    build_stock_snapshot,
    compute_technicals,
    summarize_atm_iv,
)
from app.ai_reports.stock_prompt import NO_NEWS_INSTRUCTION, SECTION_KEYS, build_user_prompt
from app.api.v1.endpoints import ai_reports as endpoint
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.core.database import get_db
from app.core.llm import LLMNotConfiguredError, LLMUnavailableError
from app.core.redis import get_redis
from app.data.models import Bar, Frequency, Market
from app.data.providers.news_calendar_models import (
    CompanyNewsItem,
    CompanyNewsResponse,
    EarningsCalendarResponse,
    EarningsEvent,
)
from tests.ai_report_fakes import (
    fail_resolve,
    fenced_turn,
    json_turn,
    junk_turn,
    prompt_text,
    use_provider,
)
from tests.copilot_fakes import FakeProvider
from tests.fake_redis import FakeRedis

SECTIONS = {
    "overview": "苹果近半年区间震荡。",
    "technical": "RSI 位于中性区间。",
    "news": "近期消息以产品迭代为主。",
    "risks": "波动率抬升。",
    "watchpoints": "关注下一次财报。",
}

NEWS_A = CompanyNewsItem(
    published_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    title="Apple 发布季度财报",
    publisher="Reuters",
    summary="营收超预期",
    url="https://example.test/a",
)
NEWS_B = CompanyNewsItem(
    published_at=datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
    title="供应链传出扩产计划",
    publisher="Bloomberg",
    url="https://example.test/b",
)


# ── 夹具 ─────────────────────────────────────────────────────────────────────

def _viewer() -> UserInfo:
    return UserInfo(id="v1", email="viewer@test.local", role="viewer")


@pytest.fixture
def app() -> FastAPI:
    """与 router.py 里的注册方式一致：prefix="/ai/reports"。"""
    test_app = FastAPI()
    test_app.include_router(endpoint.router, prefix="/api/v1/ai/reports", tags=["AI Reports"])
    test_app.dependency_overrides[get_redis] = lambda: FakeRedis()
    test_app.dependency_overrides[get_db] = lambda: None
    test_app.dependency_overrides[get_current_user] = _viewer
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def make_snapshot(*, sources: tuple[NewsSource, ...] = (), notes: tuple[str, ...] = ()) -> StockSnapshot:
    return StockSnapshot(
        symbol="AAPL",
        market="US",
        lookback_days=180,
        start_date="2026-02-15",
        end_date="2026-08-14",
        bar_count=120,
        technicals={"last_close": 190.5, "rsi_14": 55.0},
        sources=sources,
        news_summaries=tuple(f"[{s.published_at}] {s.publisher} — {s.title}" for s in sources),
        earnings=(),
        implied_volatility=None,
        data_notes=notes,
    )


def use_snapshot(monkeypatch: pytest.MonkeyPatch, snap: StockSnapshot) -> None:
    async def _build(session: Any, **kwargs: Any) -> StockSnapshot:
        del session, kwargs
        return snap

    monkeypatch.setattr(endpoint, "build_stock_snapshot", _build)


async def _ask(client: AsyncClient, **overrides: Any) -> Any:
    payload = {"symbol": "AAPL", "market": "US", **overrides}
    return await client.post("/api/v1/ai/reports/stock", json=payload)


# ── 验收 2.1：sources 必须列出实际喂给模型的新闻 ───────────────────────────────

async def test_sources_list_the_news_actually_fed_to_the_model(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一篇说不清依据的研报不如不生成 —— sources 要能被逐条核对。"""
    sources = (
        NewsSource(title=NEWS_A.title, published_at="2026-08-10T12:00:00+00:00", publisher="Reuters"),
        NewsSource(title=NEWS_B.title, published_at="2026-08-12T09:30:00+00:00", publisher="Bloomberg"),
    )
    use_snapshot(monkeypatch, make_snapshot(sources=sources))
    provider = use_provider(monkeypatch, endpoint, FakeProvider([json_turn(SECTIONS)]))

    response = await _ask(client)

    assert response.status_code == 200, response.text
    body = response.json()
    titles = [s["title"] for s in body["sources"]]
    assert titles == [NEWS_A.title, NEWS_B.title]
    assert body["sources"][0]["published_at"] == "2026-08-10T12:00:00+00:00"
    # 「列出来的」必须就是「喂进去的」，否则 sources 只是装饰
    sent = prompt_text(provider)
    assert NEWS_A.title in sent
    assert NEWS_B.title in sent


async def test_all_five_sections_are_returned(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_snapshot(monkeypatch, make_snapshot(sources=(NewsSource(title="某条新闻"),)))
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(SECTIONS)]))

    body = (await _ask(client)).json()

    assert set(body["sections"]) == set(SECTION_KEYS)
    assert [item["key"] for item in body["section_titles"]] == list(SECTION_KEYS)


async def test_fenced_json_output_is_still_parsed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地小模型爱裹一层 ```json —— 不能因此判定生成失败。"""
    use_snapshot(monkeypatch, make_snapshot(sources=(NewsSource(title="X"),)))
    use_provider(monkeypatch, endpoint, FakeProvider([fenced_turn(SECTIONS)]))

    body = (await _ask(client)).json()

    assert body["sections"]["overview"] == SECTIONS["overview"]


# ── 验收 2.2：无新闻时明说，且提示词禁止臆测 ─────────────────────────────────

async def test_missing_news_is_stated_not_invented(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拿不到新闻时，消息面唯一诚实的内容就是「无可用新闻」——
    哪怕模型自己编了一段，也不采信。"""
    use_snapshot(monkeypatch, make_snapshot(notes=("本期无可用新闻（数据源未返回任何条目）。",)))
    invented = {**SECTIONS, "news": "据传苹果即将收购某芯片公司。"}
    provider = use_provider(monkeypatch, endpoint, FakeProvider([json_turn(invented)]))

    body = (await _ask(client)).json()

    assert body["sources"] == []
    assert "无可用新闻" in body["sections"]["news"]
    assert body["sections"]["news"] == NO_NEWS_TEXT
    assert "收购" not in body["sections"]["news"]
    # 提示词侧的第一道防线：明确的禁止臆测指令
    sent = prompt_text(provider)
    assert NO_NEWS_INSTRUCTION in sent
    assert "严禁臆测" in sent


def test_prompt_forbids_speculation_when_news_is_empty() -> None:
    """提示词层面单测 —— 不经过 HTTP 也能守住这条。"""
    text = build_user_prompt(make_snapshot())

    assert "严禁臆测" in text
    assert "本期无可用新闻" in text


def test_prompt_does_not_add_the_no_news_instruction_when_news_exists() -> None:
    text = build_user_prompt(make_snapshot(sources=(NewsSource(title="有新闻"),)))

    assert NO_NEWS_INSTRUCTION not in text
    assert "有新闻" in text


def test_prompt_forbids_trade_recommendations() -> None:
    """与 A-d 的验证评级同一立场：这是启发式汇总，不是投资建议。"""
    from app.ai_reports.stock_prompt import SYSTEM_PROMPT

    assert "严禁输出任何操作建议" in SYSTEM_PROMPT
    assert "买入" in SYSTEM_PROMPT
    assert "卖出" in SYSTEM_PROMPT


async def test_disclaimer_is_a_structured_field(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """免责声明是返回体里的字段，不是藏在正文里的一句话（契约 §1.1 第 2 点）。"""
    use_snapshot(monkeypatch, make_snapshot(sources=(NewsSource(title="X"),)))
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(SECTIONS)]))

    body = (await _ask(client)).json()

    assert "不构成任何投资建议" in body["disclaimer"]


async def test_data_notes_are_surfaced_to_the_caller(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前端看到的缺口说明与提示词里的是同一句话，不存在前端粉饰后端缺数据。"""
    note = "期权隐含波动率获取失败：timeout"
    use_snapshot(monkeypatch, make_snapshot(notes=(note,)))
    provider = use_provider(monkeypatch, endpoint, FakeProvider([json_turn(SECTIONS)]))

    body = (await _ask(client)).json()

    assert note in body["data_notes"]
    assert note in prompt_text(provider)


# ── 验收 2.3：错误分层 ───────────────────────────────────────────────────────

async def test_missing_provider_returns_501_pointing_at_settings_models(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_resolve(monkeypatch, endpoint, LLMNotConfiguredError("尚未配置任何可用的模型服务。"))

    response = await _ask(client)

    assert response.status_code == 501
    assert "/settings/models" in response.json()["detail"]


async def test_unavailable_provider_returns_503_not_501(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「Ollama 没启动」和「平台不支持 AI」是两个排查方向。"""
    fail_resolve(monkeypatch, endpoint, LLMUnavailableError("Connection refused"))

    response = await _ask(client)

    assert response.status_code == 503
    assert "Connection refused" in response.json()["detail"]


async def test_provider_failure_mid_generation_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Broken(FakeProvider):
        async def chat(self, messages, **kwargs):  # type: ignore[override]
            raise LLMUnavailableError("模型不存在")

    use_snapshot(monkeypatch, make_snapshot())
    use_provider(monkeypatch, endpoint, _Broken([junk_turn()]))

    response = await _ask(client)

    assert response.status_code == 503


async def test_unparsable_output_returns_502_instead_of_half_a_report(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约 §三：失败时返回结构化错误，绝不端出半截报告。"""
    use_snapshot(monkeypatch, make_snapshot())
    provider = use_provider(monkeypatch, endpoint, FakeProvider([junk_turn()]))

    response = await _ask(client)

    assert response.status_code == 502
    assert "不生成报告" in response.json()["detail"]
    # 轮次上限：首次 + 一次纠正，不无限重试
    assert provider.calls == 2


async def test_a_repaired_second_attempt_still_produces_a_report(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_snapshot(monkeypatch, make_snapshot())
    provider = use_provider(
        monkeypatch, endpoint, FakeProvider([junk_turn(), json_turn(SECTIONS)])
    )

    response = await _ask(client)

    assert response.status_code == 200
    assert provider.calls == 2


async def test_insufficient_bars_returns_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _build(session: Any, **kwargs: Any) -> StockSnapshot:
        del session, kwargs
        raise SnapshotError("仅有 2 根日线")

    monkeypatch.setattr(endpoint, "build_stock_snapshot", _build)
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(SECTIONS)]))

    response = await _ask(client)

    assert response.status_code == 422
    assert "2 根日线" in response.json()["detail"]


async def test_unknown_market_is_rejected(client: AsyncClient) -> None:
    response = await _ask(client, market="XX")

    assert response.status_code == 400


# ── 快照层 ───────────────────────────────────────────────────────────────────

def _bars(count: int, *, start: float = 100.0) -> list[Bar]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            time=base + timedelta(days=i),
            symbol="AAPL",
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=start + i,
            high=start + i + 1,
            low=start + i - 1,
            close=start + i + 0.5,
            volume=1_000 + i,
        )
        for i in range(count)
    ]


def test_technicals_leave_unavailable_windows_as_none() -> None:
    """窗口不够的指标必须是 None —— 补一个 0 会被模型当成真实读数。"""
    result = compute_technicals(_bars(10))

    assert result["sma_20"] is None
    assert result["sma_60"] is None
    assert result["last_close"] == pytest.approx(109.5)
    assert result["change_pct_period"] is not None


def test_technicals_fill_in_once_the_window_is_long_enough() -> None:
    result = compute_technicals(_bars(80))

    assert result["sma_20"] is not None
    assert result["sma_60"] is not None
    assert result["rsi_14"] is not None
    assert result["atr_14"] is not None
    assert result["annualized_volatility_pct"] is not None


class _FakeNewsService:
    def __init__(self, news: list[CompanyNewsItem], *, fail: bool = False) -> None:
        self._news = news
        self._fail = fail

    async def get_news(self, symbol: str, market: str = "US", limit: int = 20) -> Any:
        del limit
        if self._fail:
            raise RuntimeError("boom")
        return CompanyNewsResponse(symbol=symbol, market=market, count=len(self._news), items=self._news)

    async def get_earnings(self, symbol: str, market: str = "US", limit: int = 12) -> Any:
        del limit
        return EarningsCalendarResponse(
            symbol=symbol,
            market=market,
            count=1,
            events=[EarningsEvent(period="2026Q2", eps_estimate=1.2, is_upcoming=True)],
        )


class _FailingEarningsService(_FakeNewsService):
    async def get_earnings(self, symbol: str, market: str = "US", limit: int = 12) -> Any:
        del symbol, market, limit
        raise RuntimeError("calendar down")


class _NoOptionsService:
    async def get_chain(self, symbol: str, **kwargs: Any) -> Any:
        del symbol, kwargs
        raise AssertionError("非美股不应触发期权请求")


class _FailingOptionsService:
    async def get_chain(self, symbol: str, **kwargs: Any) -> Any:
        del symbol, kwargs
        raise RuntimeError("options down")


def _use_bars(monkeypatch: pytest.MonkeyPatch, bars: list[Bar]) -> None:
    class _Service:
        def __init__(self, session: Any) -> None:
            del session

        async def get_bars(self, **kwargs: Any) -> list[Bar]:
            del kwargs
            return bars

    monkeypatch.setattr(snapshot_mod, "DataService", _Service)


async def test_snapshot_collects_news_and_earnings(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bars(monkeypatch, _bars(60))

    snap = await build_stock_snapshot(
        object(),
        symbol="00700",
        market=Market.HK,
        lookback_days=180,
        news_service=_FakeNewsService([NEWS_A, NEWS_B]),
        options_service=_NoOptionsService(),
    )

    assert [s.title for s in snap.sources] == [NEWS_A.title, NEWS_B.title]
    assert snap.has_news is True
    assert snap.earnings[0]["period"] == "2026Q2"
    # 港股没有期权链数据源 —— 如实标注，而不是空跑一次请求
    assert any("仅覆盖美股" in note for note in snap.data_notes)


async def test_snapshot_degrades_when_news_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """新闻挂了不该让整份研报失败，但必须留痕。"""
    _use_bars(monkeypatch, _bars(60))

    snap = await build_stock_snapshot(
        object(),
        symbol="AAPL",
        market=Market.HK,
        lookback_days=180,
        news_service=_FakeNewsService([], fail=True),
        options_service=_NoOptionsService(),
    )

    assert snap.sources == ()
    assert any("新闻获取失败" in note for note in snap.data_notes)


async def test_snapshot_degrades_when_earnings_and_options_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """尽力而为的三类补充数据全挂，研报照出，但缺口逐条留痕。"""
    _use_bars(monkeypatch, _bars(60))

    snap = await build_stock_snapshot(
        object(),
        symbol="AAPL",
        market=Market.US,
        lookback_days=180,
        news_service=_FailingEarningsService([]),
        options_service=_FailingOptionsService(),
    )

    assert snap.earnings == ()
    assert snap.implied_volatility is None
    assert any("财报日历获取失败" in note for note in snap.data_notes)
    assert any("期权隐含波动率获取失败" in note for note in snap.data_notes)
    assert any("本期无可用新闻" in note for note in snap.data_notes)


async def test_snapshot_carries_atm_iv_for_us_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bars(monkeypatch, _bars(60))

    class _OptionsService:
        async def get_chain(self, symbol: str, **kwargs: Any) -> Any:
            del symbol, kwargs
            return _Chain(100.0, [_Contract(100.0, 0.25)])

    snap = await build_stock_snapshot(
        object(),
        symbol="AAPL",
        market=Market.US,
        lookback_days=180,
        news_service=_FakeNewsService([NEWS_A]),
        options_service=_OptionsService(),
    )

    assert snap.implied_volatility is not None
    assert snap.implied_volatility["atm_implied_volatility_pct"] == pytest.approx(25.0)
    # 有数据的三块都要出现在提示词里
    text = build_user_prompt(snap)
    assert "期权隐含波动率（近月平值）" in text
    assert "2026Q2" in text


async def test_snapshot_rejects_too_few_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bars(monkeypatch, _bars(MIN_BARS - 1))

    with pytest.raises(SnapshotError, match="根日线"):
        await build_stock_snapshot(
            object(),
            symbol="AAPL",
            market=Market.HK,
            lookback_days=180,
            news_service=_FakeNewsService([]),
            options_service=_NoOptionsService(),
        )


async def test_snapshot_requires_a_session() -> None:
    with pytest.raises(SnapshotError, match="数据库会话"):
        await build_stock_snapshot(
            None, symbol="AAPL", market=Market.US, lookback_days=180
        )


class _Chain:
    def __init__(self, underlying: float | None, contracts: list[Any]) -> None:
        self.underlying_price = underlying
        self.expiration = "2026-09-18"
        self.calls = contracts
        self.puts = []


class _Contract:
    def __init__(self, strike: float, iv: float | None) -> None:
        self.strike = strike
        self.implied_volatility = iv


def test_atm_iv_uses_only_near_the_money_contracts() -> None:
    chain = _Chain(100.0, [_Contract(100.0, 0.30), _Contract(180.0, 0.90), _Contract(105.0, 0.40)])

    summary = summarize_atm_iv(chain)

    assert summary is not None
    assert summary["contracts_used"] == 2
    assert summary["atm_implied_volatility_pct"] == pytest.approx(35.0)


def test_atm_iv_is_none_without_an_underlying_price() -> None:
    assert summarize_atm_iv(_Chain(None, [_Contract(100.0, 0.3)])) is None
    assert summarize_atm_iv(_Chain(100.0, [_Contract(100.0, None)])) is None


# ── JSON 剥离 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '好的：\n{"a": 1}\n希望有帮助',
    ],
)
def test_extract_json_object_handles_common_wrappers(raw: str) -> None:
    assert extract_json_object(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["", "没有 JSON", "[1, 2, 3]"])
def test_extract_json_object_returns_none_for_non_objects(raw: str) -> None:
    assert extract_json_object(raw) is None
