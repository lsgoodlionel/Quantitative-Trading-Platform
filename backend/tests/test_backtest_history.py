"""
回测历史持久化测试（V3 · H5）

覆盖：
1. 保存 → 列表 → 详情 → 重跑（重跑结果与原结果一致，证明配置完整落库）
2. 对比接口返回多条曲线且逐点对齐
3. 分页与筛选
4. 净值曲线降采样（体积策略）与删除

用内存仓储替换 Postgres 仓储（dependency_overrides），不依赖真实数据库。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.backtest_history import get_service, get_store
from app.api.v1.endpoints.backtests import get_service as get_run_service
from app.data.models import Bar, Frequency, Market
from app.data.storage.backtest_history import (
    MAX_CURVE_POINTS,
    BacktestHistoryRecord,
    HistoryFilter,
    build_record,
    downsample_curve,
)
from app.engine.backtest.history_compare import COMPARE_METRIC_KEYS, build_comparison
from app.main import app

BASE = "/api/v1/backtests/history"


# ── 内存仓储 ──────────────────────────────────────────────────────

class InMemoryHistoryStore:
    """`BacktestHistoryStore` 的内存实现，语义与 Postgres 版一致。"""

    def __init__(self) -> None:
        self._rows: dict[str, BacktestHistoryRecord] = {}
        self._order: list[str] = []

    async def save(self, record: BacktestHistoryRecord) -> BacktestHistoryRecord:
        self._rows[record.id] = record
        self._order.insert(0, record.id)   # created_at DESC
        return record

    async def list(self, filters: HistoryFilter, limit: int, offset: int):
        matched = [self._rows[i] for i in self._order if _matches(self._rows[i], filters)]
        return matched[offset : offset + limit], len(matched)

    async def get(self, record_id: str) -> BacktestHistoryRecord | None:
        return self._rows.get(record_id)

    async def get_many(self, record_ids: list[str]) -> list[BacktestHistoryRecord]:
        return [self._rows[i] for i in record_ids if i in self._rows]

    async def delete(self, record_id: str) -> bool:
        if record_id not in self._rows:
            return False
        del self._rows[record_id]
        self._order.remove(record_id)
        return True


def _matches(record: BacktestHistoryRecord, f: HistoryFilter) -> bool:
    if f.strategy_name and record.strategy_name != f.strategy_name:
        return False
    if f.symbol and record.symbol != f.symbol.upper():
        return False
    if f.market and record.market != f.market.upper():
        return False
    if f.start_after and date.fromisoformat(record.start_date) < f.start_after:
        return False
    return not (f.end_before and date.fromisoformat(record.end_date) > f.end_before)


# ── 行情桩：确定性的上升趋势，保证策略产生成交 ─────────────────────

def _make_bars(n: int = 120) -> list[Bar]:
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        # 正弦式波动叠加缓慢上行，制造均线交叉
        price = 100.0 + i * 0.3 + (8.0 if (i // 12) % 2 else -8.0)
        bars.append(
            Bar(
                time=datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i),
                symbol="AAPL", market=Market.US, frequency=Frequency.DAY_1,
                open=price, high=price * 1.01, low=price * 0.99,
                close=price, volume=1_000_000,
            )
        )
    return bars


class _StubService:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    async def get_bars(self, **_kwargs) -> list[Bar]:
        return list(self._bars)


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def store() -> InMemoryHistoryStore:
    memory = InMemoryHistoryStore()
    app.dependency_overrides[get_store] = lambda: memory
    stub = _StubService(_make_bars())
    app.dependency_overrides[get_service] = lambda: stub
    app.dependency_overrides[get_run_service] = lambda: stub
    yield memory
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _payload(**overrides) -> dict:
    body = {
        "strategy_name": "double_ma",
        "symbol": "AAPL",
        "market": "US",
        "frequency": "1d",
        "start_date": "2023-01-01",
        "end_date": "2023-04-30",
        "initial_cash": 100_000.0,
        "final_value": 118_000.0,
        "params": {"fast_period": 5, "slow_period": 20},
        "metrics": {"sharpe_ratio": 1.42, "total_return_pct": 18.0, "total_trades": 6},
        "equity_curve": [
            {"time": "2023-01-01", "value": 100_000.0},
            {"time": "2023-02-01", "value": 105_000.0},
            {"time": "2023-03-01", "value": 112_000.0},
            {"time": "2023-04-30", "value": 118_000.0},
        ],
    }
    body.update(overrides)
    return body


# ══════════════════════════════════════════════════════════════════
# 1. 保存 → 列表 → 详情 → 重跑
# ══════════════════════════════════════════════════════════════════

async def test_save_then_list_then_detail(store, client):
    async with client as c:
        created = (await c.post(BASE, json=_payload())).json()
        listed = (await c.get(BASE)).json()
        detail = (await c.get(f"{BASE}/{created['id']}")).json()

    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]
    # 列表投影不带净值曲线（体积控制）
    assert "equity_curve" not in listed["items"][0]
    # 详情带完整配置与曲线
    assert detail["equity_curve"] == _payload()["equity_curve"]
    assert detail["params"] == {"fast_period": 5, "slow_period": 20}
    assert detail["metrics"]["sharpe_ratio"] == 1.42


async def test_list_reports_storage_medium_without_capacity_cap(store, client):
    async with client as c:
        listed = (await c.get(BASE)).json()
    assert listed["storage"] == "timescaledb"


async def test_save_rejects_unknown_strategy(store, client):
    async with client as c:
        resp = await c.post(BASE, json=_payload(strategy_name="not_a_strategy"))
    assert resp.status_code == 400


async def test_detail_404_for_missing_record(store, client):
    async with client as c:
        resp = await c.get(f"{BASE}/00000000-0000-0000-0000-0000000000ff")
    assert resp.status_code == 404


async def test_detail_400_for_malformed_id(store, client):
    async with client as c:
        resp = await c.get(f"{BASE}/not-a-uuid")
    assert resp.status_code == 400


async def test_rerun_reproduces_original_metrics(store, client):
    """配置完整落库 ⇒ 同一份行情下重跑，指标与保存时逐项一致。"""
    async with client as c:
        # Arrange：先跑一次真实回测，用它的产物存历史
        run = await c.post("/api/v1/backtests/run", json={
            "strategy_name": "double_ma", "symbol": "AAPL", "market": "US",
            "frequency": "1d", "start_date": "2023-01-01", "end_date": "2023-04-30",
            "initial_cash": 100_000.0, "params": {"fast_period": 5, "slow_period": 20},
        })
        assert run.status_code == 200, run.text
        original = run.json()

        saved = (await c.post(BASE, json=_payload(
            final_value=original["final_value"],
            metrics=original["metrics"],
            equity_curve=original["equity_curve"],
        ))).json()

        # Act
        rerun = await c.post(f"{BASE}/{saved['id']}/rerun")

    # Assert
    assert rerun.status_code == 200, rerun.text
    body = rerun.json()
    assert body["source_id"] == saved["id"]
    assert body["metrics"] == original["metrics"]
    assert body["final_value"] == pytest.approx(original["final_value"])
    assert body["metrics_changed"] is False
    assert body["config"]["params"] == {"fast_period": 5, "slow_period": 20}


async def test_rerun_flags_metric_drift(store, client):
    """存下来的指标与重跑结果不一致时，必须显式标出而非静默覆盖。"""
    async with client as c:
        saved = (await c.post(BASE, json=_payload(
            metrics={"sharpe_ratio": 99.0, "total_return_pct": 999.0}
        ))).json()
        body = (await c.post(f"{BASE}/{saved['id']}/rerun")).json()

    assert body["metrics_changed"] is True


async def test_rerun_404_for_missing_record(store, client):
    async with client as c:
        resp = await c.post(f"{BASE}/00000000-0000-0000-0000-0000000000ff/rerun")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════
# 2. 对比接口
# ══════════════════════════════════════════════════════════════════

async def test_compare_returns_aligned_curves(store, client):
    async with client as c:
        first = (await c.post(BASE, json=_payload())).json()
        second = (await c.post(BASE, json=_payload(
            symbol="MSFT",
            equity_curve=[
                {"time": "2023-02-01", "value": 50_000.0},
                {"time": "2023-03-15", "value": 60_000.0},
            ],
            initial_cash=50_000.0,
        ))).json()
        resp = await c.get(f"{BASE}/compare", params={"ids": f"{first['id']},{second['id']}"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 公共时间轴 = 两条曲线时间标签的并集
    assert data["axis"] == ["2023-01-01", "2023-02-01", "2023-03-01", "2023-03-15", "2023-04-30"]
    assert len(data["series"]) == 2
    for series in data["series"]:
        assert len(series["values"]) == len(data["axis"])
    # 第二条在自己开始之前留 None（前端断线），之后前向填充
    assert data["series"][1]["values"][0] is None
    assert data["series"][1]["values"][1] == pytest.approx(1.0)
    assert data["series"][1]["values"][4] == pytest.approx(1.2)
    assert data["metrics"]["keys"] == list(COMPARE_METRIC_KEYS)
    assert len(data["metrics"]["rows"]) == 2


async def test_compare_reports_missing_ids(store, client):
    async with client as c:
        first = (await c.post(BASE, json=_payload())).json()
        ghost = "00000000-0000-0000-0000-0000000000ff"
        data = (await c.get(f"{BASE}/compare", params={"ids": f"{first['id']},{ghost}"})).json()

    assert data["missing_ids"] == [ghost]
    assert len(data["series"]) == 1


async def test_compare_404_when_nothing_found(store, client):
    async with client as c:
        resp = await c.get(f"{BASE}/compare", params={"ids": "00000000-0000-0000-0000-0000000000ff"})
    assert resp.status_code == 404


async def test_compare_rejects_too_many_ids(store, client):
    ids = ",".join(f"00000000-0000-0000-0000-0000000000{i:02x}" for i in range(9))
    async with client as c:
        resp = await c.get(f"{BASE}/compare", params={"ids": ids})
    assert resp.status_code == 400


def test_build_comparison_on_empty_records():
    data = build_comparison([])
    assert data == {"axis": [], "series": [], "metrics": {"keys": list(COMPARE_METRIC_KEYS), "rows": []}}


# ══════════════════════════════════════════════════════════════════
# 3. 分页与筛选
# ══════════════════════════════════════════════════════════════════

async def test_pagination(store, client):
    async with client as c:
        for i in range(5):
            await c.post(BASE, json=_payload(name=f"run-{i}"))
        page1 = (await c.get(BASE, params={"limit": 2, "offset": 0})).json()
        page2 = (await c.get(BASE, params={"limit": 2, "offset": 2})).json()
        page3 = (await c.get(BASE, params={"limit": 2, "offset": 4})).json()

    assert page1["total"] == page2["total"] == 5
    assert [len(p["items"]) for p in (page1, page2, page3)] == [2, 2, 1]
    ids = {item["id"] for p in (page1, page2, page3) for item in p["items"]}
    assert len(ids) == 5


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"symbol": "MSFT"}, 1),
        ({"symbol": "AAPL"}, 2),
        ({"strategy_name": "bollinger"}, 1),
        ({"market": "HK"}, 1),
        ({"start_after": "2023-06-01"}, 1),
        ({"end_before": "2023-05-01"}, 2),
    ],
)
async def test_filters(store, client, query: dict, expected: int):
    async with client as c:
        await c.post(BASE, json=_payload())
        await c.post(BASE, json=_payload(symbol="MSFT"))
        await c.post(BASE, json=_payload(
            strategy_name="bollinger", market="HK", symbol="AAPL",
            start_date="2023-06-01", end_date="2023-09-30",
        ))
        listed = (await c.get(BASE, params=query)).json()

    assert listed["total"] == expected


async def test_list_rejects_oversized_page(store, client):
    async with client as c:
        resp = await c.get(BASE, params={"limit": 1000})
    assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════
# 4. 曲线降采样与删除
# ══════════════════════════════════════════════════════════════════

def test_downsample_keeps_endpoints_and_caps_length():
    curve = [{"time": f"t{i}", "value": float(i)} for i in range(50_000)]

    picked, downsampled = downsample_curve(curve)

    assert downsampled is True
    assert len(picked) == MAX_CURVE_POINTS
    assert picked[0] == curve[0]
    assert picked[-1] == curve[-1]


def test_downsample_is_noop_for_short_curves():
    curve = [{"time": "t0", "value": 1.0}, {"time": "t1", "value": 2.0}]
    picked, downsampled = downsample_curve(curve)
    assert picked == curve
    assert downsampled is False


def test_build_record_marks_downsampling():
    record = build_record(
        strategy_name="double_ma", symbol="aapl", market="us", frequency="1d",
        start_date="2023-01-01", end_date="2023-12-31",
        initial_cash=100_000.0, final_value=110_000.0,
        params={}, metrics={},
        equity_curve=[{"time": f"t{i}", "value": float(i)} for i in range(5_000)],
    )
    assert record.curve_downsampled is True
    assert record.curve_points == MAX_CURVE_POINTS
    assert record.symbol == "AAPL"
    assert record.market == "US"
    assert record.name.startswith("double_ma · AAPL")


async def test_delete_removes_record(store, client):
    async with client as c:
        created = (await c.post(BASE, json=_payload())).json()
        deleted = await c.delete(f"{BASE}/{created['id']}")
        after = (await c.get(BASE)).json()
        missing = await c.delete(f"{BASE}/{created['id']}")

    assert deleted.status_code == 204
    assert after["total"] == 0
    assert missing.status_code == 404
