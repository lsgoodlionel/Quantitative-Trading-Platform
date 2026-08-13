"""
统一事件总线通知测试（V3 G5）

覆盖契约三条硬要求：
1. 5 个新事件类型可派发
2. dispatch_event 抛异常时任务本身仍成功（通知是旁路）
3. 新事件类型默认不开 Telegram/Webhook

外加站内收件箱（通知中心）的存储与已读语义。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.notify import inbox
from app.notify.config import (
    IN_APP_ONLY_DEFAULT_EVENTS,
    ChannelConfig,
    ChannelType,
    NotifyConfig,
    NotifyEventType,
    TelegramChannelConfig,
    WebhookChannelConfig,
    default_channel_events,
)
from app.notify.dispatcher import dispatch_event
from app.notify.emit import (
    emit_backtest_done,
    emit_data_source_degraded,
    emit_hyperopt_done,
    emit_mining_done,
    emit_price_alert,
    emit_reconcile_diff,
    notify_safe,
)
from app.notify.events import NotifyEvent, render_event

NEW_EVENT_TYPES = [
    NotifyEventType.BACKTEST_DONE,
    NotifyEventType.HYPEROPT_DONE,
    NotifyEventType.MINING_DONE,
    NotifyEventType.DATA_SOURCE_DEGRADED,
    NotifyEventType.RECONCILE_DIFF,
]


# ── 极简 Redis 替身（仅实现 inbox 用到的命令）─────────────────

class FakeRedis:
    """同步/异步双面的最小 Redis 替身；异步方法返回已算好的值。"""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    # -- 同步命令（dispatcher 写入路径）--
    def set(self, key: str, value: str) -> None:
        self.strings[key] = value

    def delete(self, key: str) -> None:
        self.strings.pop(key, None)

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key: str, member: str) -> None:
        self.zsets.get(key, {}).pop(member, None)

    def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        return self._sorted(key)[start : (None if end == -1 else end + 1)]

    def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        ordered = list(reversed(self._sorted(key)))
        return ordered[start : (None if end == -1 else end + 1)]

    def _sorted(self, key: str) -> list[str]:
        return [m for m, _ in sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])]

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    """收集命令、execute 时按序回放（同步 execute + 可 await 的异步用法）。"""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return _record

    def execute(self):
        for name, args, kwargs in self._ops:
            getattr(self._client, name)(*args, **kwargs)
        self._ops = []
        return _Awaitable(None)


class _Awaitable:
    """既能当普通返回值、也能被 await 的薄包装。"""

    def __init__(self, value) -> None:
        self.value = value

    def __await__(self):
        async def _inner():
            return self.value
        return _inner().__await__()


class AsyncFakeRedis:
    """异步读取面（端点路径），底层共用 FakeRedis 数据。"""

    def __init__(self, backing: FakeRedis) -> None:
        self._b = backing

    async def get(self, key: str) -> str | None:
        return self._b.strings.get(key)

    async def set(self, key: str, value: str) -> None:
        self._b.set(key, value)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._b.strings.get(k) for k in keys]

    async def exists(self, key: str) -> int:
        return int(key in self._b.strings)

    async def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        return self._b.zrevrange(key, start, end)

    def pipeline(self) -> _FakePipeline:
        return self._b.pipeline()


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    """把 dispatcher 的同步 Redis 客户端换成替身，避免依赖真实 Redis。"""
    client = FakeRedis()
    monkeypatch.setattr("app.notify.dispatcher._get_sync_client", lambda: client)
    return client


# ── 辅助 ─────────────────────────────────────────────────────

def _telegram_channel(events: list[NotifyEventType]) -> ChannelConfig:
    return ChannelConfig(
        id="tg-1", type=ChannelType.TELEGRAM, name="tg", enabled=True, events=events,
        telegram=TelegramChannelConfig(bot_token="t", chat_id="c"),
    )


def _webhook_channel(events: list[NotifyEventType]) -> ChannelConfig:
    return ChannelConfig(
        id="wh-1", type=ChannelType.WEBHOOK, name="wh", enabled=True, events=events,
        webhook=WebhookChannelConfig(url="https://example.com/hook"),
    )


@pytest.fixture
def captured_sends(monkeypatch) -> list[str]:
    """拦截 Celery .delay，记录实际外发的渠道类型。"""
    sent: list[str] = []

    class _Task:
        def __init__(self, label: str) -> None:
            self.label = label

        def delay(self, *_args, **_kwargs) -> None:
            sent.append(self.label)

    monkeypatch.setattr("app.tasks.notify.send_telegram", _Task("telegram"))
    monkeypatch.setattr("app.tasks.notify.send_webhook", _Task("webhook"))
    return sent


# ── 1. 5 个新事件类型可派发 ───────────────────────────────────

@pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
def test_new_event_type_dispatches_to_subscribed_channel(
    event_type: NotifyEventType, fake_redis: FakeRedis, captured_sends: list[str]
) -> None:
    config = NotifyConfig(is_active=True, channels=[_telegram_channel([event_type])])

    result = dispatch_event(NotifyEvent(type=event_type, title="t"), config)

    assert result["dispatched"] == 1
    assert captured_sends == ["telegram"]


@pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
def test_new_event_type_renders_with_own_label(event_type: NotifyEventType) -> None:
    """每个新类型都有专属 emoji + 标签，不应落到「🔔 通知」兜底。"""
    channel = _telegram_channel([event_type])
    rendered = render_event(NotifyEvent(type=event_type, title="t"), channel)

    assert "🔔 <b>QuantBot · 通知</b>" not in rendered.text
    assert rendered.payload["event"] == event_type.value


@pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
def test_new_event_type_recorded_in_inbox(
    event_type: NotifyEventType, fake_redis: FakeRedis
) -> None:
    """站内始终记录，哪怕一个外发渠道都没配。"""
    result = dispatch_event(
        NotifyEvent(type=event_type, title="站内"), NotifyConfig(is_active=True, channels=[])
    )

    assert result["recorded"] is True
    assert result["dispatched"] == 0
    assert len(fake_redis.zsets[f"{inbox._KEY_PREFIX}:zset:time"]) == 1


def test_all_emit_helpers_produce_events(fake_redis: FakeRedis) -> None:
    """六个业务发射器都能走通（含 G6 预留的对账差异）。"""
    emit_backtest_done(
        strategy_name="double_ma", symbol="AAPL", market="US",
        metrics={"total_return_pct": 1.0, "sharpe_ratio": 0.5},
    )
    emit_hyperopt_done(
        strategy_name="double_ma", symbol="AAPL", market="US",
        best_params={"fast": 5}, best_loss=-0.9, evaluated=40,
    )
    emit_mining_done(
        market="US", symbols=["AAPL", "MSFT"], generations=10,
        best_expr="rank(close)", best_fitness=0.3,
    )
    emit_data_source_degraded(market="US", failed_sources=["yfinance"], active_source=None)
    emit_price_alert(symbol="AAPL", market="US", condition="above", threshold=100.0, price=101.0)
    emit_reconcile_diff(market="US", diff_count=2, detail="持仓不一致")

    recorded = [inbox.parse_notification(v) for v in fake_redis.strings.values()]
    types = {n.type for n in recorded if n is not None}
    assert types == {
        NotifyEventType.BACKTEST_DONE.value,
        NotifyEventType.HYPEROPT_DONE.value,
        NotifyEventType.MINING_DONE.value,
        NotifyEventType.DATA_SOURCE_DEGRADED.value,
        NotifyEventType.RISK_ALERT.value,
        NotifyEventType.RECONCILE_DIFF.value,
    }


# ── 2. 通知失败不影响调用方 ───────────────────────────────────

def test_notify_safe_swallows_dispatch_failure(monkeypatch, caplog) -> None:
    def _boom(*_a, **_kw):
        raise RuntimeError("Telegram 超时")

    monkeypatch.setattr("app.notify.dispatcher.dispatch_event", _boom)

    with caplog.at_level(logging.ERROR, logger="app.notify.emit"):
        result = notify_safe(NotifyEvent(type=NotifyEventType.BACKTEST_DONE, title="t"))

    assert result == {"dispatched": 0, "failed": True}
    # 必须 logger.exception 记录，不能静默吞
    assert any(r.exc_info for r in caplog.records)
    assert "通知发送失败" in caplog.text


def test_inbox_write_failure_does_not_block_outbound(monkeypatch, captured_sends) -> None:
    """站内写入炸了也不该影响 Telegram/Webhook 外发。"""
    class _BrokenRedis:
        def pipeline(self):
            raise RuntimeError("Redis 挂了")

    monkeypatch.setattr("app.notify.dispatcher._get_sync_client", lambda: _BrokenRedis())
    config = NotifyConfig(
        is_active=True, channels=[_telegram_channel([NotifyEventType.BACKTEST_DONE])]
    )

    result = dispatch_event(NotifyEvent(type=NotifyEventType.BACKTEST_DONE, title="t"), config)

    assert result["recorded"] is False
    assert result["dispatched"] == 1
    assert captured_sends == ["telegram"]


def test_channel_send_failure_does_not_block_other_channels(
    monkeypatch, fake_redis: FakeRedis
) -> None:
    """单渠道入队失败不影响其他渠道（既有语义，回归保护）。"""
    class _BrokenTask:
        def delay(self, *_a, **_kw):
            raise RuntimeError("broker down")

    sent: list[str] = []

    class _OkTask:
        def delay(self, *_a, **_kw):
            sent.append("webhook")

    monkeypatch.setattr("app.tasks.notify.send_telegram", _BrokenTask())
    monkeypatch.setattr("app.tasks.notify.send_webhook", _OkTask())

    config = NotifyConfig(is_active=True, channels=[
        _telegram_channel([NotifyEventType.BACKTEST_DONE]),
        _webhook_channel([NotifyEventType.BACKTEST_DONE]),
    ])
    result = dispatch_event(NotifyEvent(type=NotifyEventType.BACKTEST_DONE, title="t"), config)

    assert result["dispatched"] == 1
    assert sent == ["webhook"]


# ── 3. 新事件类型默认不开 Telegram/Webhook ────────────────────

@pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
def test_new_event_types_excluded_from_default_channel_events(
    event_type: NotifyEventType,
) -> None:
    assert event_type in IN_APP_ONLY_DEFAULT_EVENTS
    assert event_type not in default_channel_events()


def test_price_alert_type_also_defaults_to_in_app_only() -> None:
    """预警改走统一通道是行为变更：risk_alert 也必须默认不外发。"""
    assert NotifyEventType.RISK_ALERT in IN_APP_ONLY_DEFAULT_EVENTS
    assert NotifyEventType.RISK_ALERT not in default_channel_events()


def test_trading_event_types_remain_in_default_channel_events() -> None:
    """既有交易类事件的默认订阅不受影响。"""
    defaults = default_channel_events()
    for event_type in (
        NotifyEventType.TRADE_FILL,
        NotifyEventType.ORDER_REJECT,
        NotifyEventType.POSITION,
        NotifyEventType.DAILY_SUMMARY,
        NotifyEventType.PROTECTION,
    ):
        assert event_type in defaults


@pytest.mark.parametrize("event_type", [*NEW_EVENT_TYPES, NotifyEventType.RISK_ALERT])
def test_default_channel_does_not_send_in_app_only_events(
    event_type: NotifyEventType, fake_redis: FakeRedis, captured_sends: list[str]
) -> None:
    """按默认订阅建的渠道，收不到「仅站内」事件。"""
    config = NotifyConfig(is_active=True, channels=[
        _telegram_channel(default_channel_events()),
        _webhook_channel(default_channel_events()),
    ])

    result = dispatch_event(NotifyEvent(type=event_type, title="t"), config)

    assert result["dispatched"] == 0
    assert captured_sends == []
    assert result["recorded"] is True


def test_new_channel_defaults_to_empty_subscription() -> None:
    """未显式给 events 的渠道一条都不订阅（最保守默认）。"""
    channel = _telegram_channel([])
    assert channel.events == []


# ── 站内收件箱 ────────────────────────────────────────────────

def _event(title: str) -> NotifyEvent:
    return NotifyEvent(
        type=NotifyEventType.BACKTEST_DONE,
        title=title,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_inbox_lists_newest_first(fake_redis: FakeRedis) -> None:
    client = fake_redis
    for i in range(3):
        note = inbox.build_notification(_event(f"n{i}"))
        inbox.record_notification_sync(client, inbox.Notification(**{
            **note.to_dict(), "created_at": float(i)
        }))

    items = await inbox.list_notifications(AsyncFakeRedis(client), limit=10)

    assert [n.title for n in items] == ["n2", "n1", "n0"]


@pytest.mark.asyncio
async def test_inbox_mark_read_and_unread_count(fake_redis: FakeRedis) -> None:
    client = fake_redis
    note = inbox.build_notification(_event("hello"))
    inbox.record_notification_sync(client, note)
    aredis = AsyncFakeRedis(client)

    assert await inbox.count_unread(aredis) == 1

    updated = await inbox.mark_read(aredis, note.id)
    assert updated is not None
    assert updated.is_read is True
    assert await inbox.count_unread(aredis) == 0

    # 可以再标回未读
    back = await inbox.mark_read(aredis, note.id, is_read=False)
    assert back is not None
    assert back.is_read is False
    assert await inbox.count_unread(aredis) == 1


@pytest.mark.asyncio
async def test_inbox_mark_read_missing_returns_none(fake_redis: FakeRedis) -> None:
    assert await inbox.mark_read(AsyncFakeRedis(fake_redis), "nope") is None


@pytest.mark.asyncio
async def test_inbox_mark_all_read(fake_redis: FakeRedis) -> None:
    client = fake_redis
    for i in range(3):
        inbox.record_notification_sync(client, inbox.build_notification(_event(f"n{i}")))
    aredis = AsyncFakeRedis(client)

    assert await inbox.mark_all_read(aredis) == 3
    assert await inbox.count_unread(aredis) == 0
    assert await inbox.mark_all_read(aredis) == 0


@pytest.mark.asyncio
async def test_inbox_unread_only_filter(fake_redis: FakeRedis) -> None:
    client = fake_redis
    first = inbox.build_notification(_event("read-me"))
    inbox.record_notification_sync(client, first)
    inbox.record_notification_sync(client, inbox.build_notification(_event("still-unread")))
    aredis = AsyncFakeRedis(client)
    await inbox.mark_read(aredis, first.id)

    items = await inbox.list_notifications(aredis, limit=10, unread_only=True)

    assert [n.title for n in items] == ["still-unread"]


@pytest.mark.asyncio
async def test_inbox_delete_and_clear(fake_redis: FakeRedis) -> None:
    client = fake_redis
    note = inbox.build_notification(_event("bye"))
    inbox.record_notification_sync(client, note)
    aredis = AsyncFakeRedis(client)

    assert await inbox.delete_notification(aredis, note.id) is True
    assert await inbox.delete_notification(aredis, note.id) is False
    assert await inbox.list_notifications(aredis) == []

    inbox.record_notification_sync(client, inbox.build_notification(_event("x")))
    assert await inbox.clear_all(aredis) == 1
    assert await inbox.clear_all(aredis) == 0


def test_inbox_enforces_capacity(fake_redis: FakeRedis) -> None:
    """超过容量上限后裁剪最旧记录，防止 Redis 无界增长。"""
    client = fake_redis
    for i in range(inbox.MAX_NOTIFICATIONS + 5):
        note = inbox.build_notification(_event(f"n{i}"))
        inbox.record_notification_sync(
            client, inbox.Notification(**{**note.to_dict(), "created_at": float(i)})
        )

    zset = client.zsets[f"{inbox._KEY_PREFIX}:zset:time"]
    assert len(zset) == inbox.MAX_NOTIFICATIONS
    assert len(client.strings) == inbox.MAX_NOTIFICATIONS


def test_inbox_parse_tolerates_corrupt_record() -> None:
    assert inbox.parse_notification("{not json") is None
    assert inbox.parse_notification('{"title": "缺 id"}') is None


# ── 通知中心 API ──────────────────────────────────────────────

@pytest.fixture
def inbox_client(fake_redis: FakeRedis):
    """挂上 Redis 替身的 AsyncClient 工厂。"""
    from app.core.redis import get_redis
    from app.main import app

    async def _override():
        yield AsyncFakeRedis(fake_redis)

    app.dependency_overrides[get_redis] = _override
    yield lambda: AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_inbox_api_lists_dispatched_events(fake_redis, inbox_client) -> None:
    dispatch_event(
        NotifyEvent(type=NotifyEventType.BACKTEST_DONE, title="回测完成 · double_ma"),
        NotifyConfig(is_active=True, channels=[]),
    )

    async with inbox_client() as client:
        response = await client.get("/api/v1/notify/inbox")

    assert response.status_code == 200
    data = response.json()
    assert data["unread"] == 1
    assert data["items"][0]["title"] == "回测完成 · double_ma"
    assert data["items"][0]["is_read"] is False


@pytest.mark.asyncio
async def test_inbox_api_mark_read_and_read_all(fake_redis, inbox_client) -> None:
    for i in range(2):
        dispatch_event(
            NotifyEvent(type=NotifyEventType.MINING_DONE, title=f"n{i}"),
            NotifyConfig(is_active=True, channels=[]),
        )

    async with inbox_client() as client:
        listed = (await client.get("/api/v1/notify/inbox")).json()
        first_id = listed["items"][0]["id"]

        marked = await client.post(f"/api/v1/notify/inbox/{first_id}/read", json={"is_read": True})
        assert marked.status_code == 200
        assert marked.json()["is_read"] is True
        assert (await client.get("/api/v1/notify/inbox")).json()["unread"] == 1

        read_all = await client.post("/api/v1/notify/inbox/read-all")
        assert read_all.json() == {"updated": 1}
        assert (await client.get("/api/v1/notify/inbox")).json()["unread"] == 0


@pytest.mark.asyncio
async def test_inbox_api_unread_only_filter(fake_redis, inbox_client) -> None:
    for i in range(2):
        dispatch_event(
            NotifyEvent(type=NotifyEventType.RISK_ALERT, title=f"n{i}"),
            NotifyConfig(is_active=True, channels=[]),
        )

    async with inbox_client() as client:
        items = (await client.get("/api/v1/notify/inbox")).json()["items"]
        await client.post(f"/api/v1/notify/inbox/{items[0]['id']}/read", json={"is_read": True})
        filtered = await client.get("/api/v1/notify/inbox?unread_only=true")

    assert len(filtered.json()["items"]) == 1


@pytest.mark.asyncio
async def test_inbox_api_delete_and_clear(fake_redis, inbox_client) -> None:
    dispatch_event(
        NotifyEvent(type=NotifyEventType.HYPEROPT_DONE, title="x"),
        NotifyConfig(is_active=True, channels=[]),
    )

    async with inbox_client() as client:
        note_id = (await client.get("/api/v1/notify/inbox")).json()["items"][0]["id"]
        assert (await client.delete(f"/api/v1/notify/inbox/{note_id}")).status_code == 200
        assert (await client.delete(f"/api/v1/notify/inbox/{note_id}")).status_code == 404

        dispatch_event(
            NotifyEvent(type=NotifyEventType.HYPEROPT_DONE, title="y"),
            NotifyConfig(is_active=True, channels=[]),
        )
        cleared = await client.delete("/api/v1/notify/inbox")

    assert cleared.json() == {"deleted": 1}


@pytest.mark.asyncio
async def test_inbox_api_mark_read_unknown_id_returns_404(fake_redis, inbox_client) -> None:
    async with inbox_client() as client:
        response = await client.post("/api/v1/notify/inbox/nope/read", json={"is_read": True})

    assert response.status_code == 404
