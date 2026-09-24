"""命名因子策略存储 + 实验提升测试（V3 Wave A-a / G1）

对应契约 docs/contracts/waveAa-factor-strategy-adapter.md §二.3 与 §四 验收 5。
"""

from __future__ import annotations

import pytest

from app.quant.experiments.recorder import (
    MAX_RECORDS,
    ExperimentMetrics,
    build_record,
    get_experiment,
    promote_to_strategy,
    save_experiment,
)
from app.strategy.factor_store import (
    delete_factor_strategy,
    get_factor_strategy,
    list_factor_strategies,
    save_factor_strategy,
)
from app.strategy.factor_strategy import FactorStrategySpec, build_factor_strategy
from tests.fake_redis import FakeRedis


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


def _spec(**overrides) -> FactorStrategySpec:
    base = {"formula": "MOM20 ATR_RATIO DIV", "universe": ["AAPL", "MSFT", "NVDA"]}
    return FactorStrategySpec(**(base | overrides))


async def _record(redis: FakeRedis, name: str = "候选因子"):
    return await save_experiment(
        redis,
        build_record(
            kind="genetic_mining",
            name=name,
            market="US",
            symbols=["AAPL", "MSFT", "NVDA"],
            metrics=ExperimentMetrics(fitness=0.42),
            tokens=["MOM20", "ATR_RATIO", "DIV"],
        ),
    )


# ── 存储 ─────────────────────────────────────────────────────────


class TestFactorStore:
    async def test_saved_strategy_round_trips(self, redis: FakeRedis) -> None:
        spec = _spec(portfolio_method="hrp", max_positions=2)

        await save_factor_strategy(redis, name="动量组合", spec=spec)
        loaded = await get_factor_strategy(redis, "动量组合")

        assert loaded is not None
        assert loaded.spec == spec

    async def test_missing_strategy_returns_none(self, redis: FakeRedis) -> None:
        assert await get_factor_strategy(redis, "不存在") is None

    async def test_list_is_newest_first(self, redis: FakeRedis) -> None:
        await save_factor_strategy(redis, name="旧", spec=_spec())
        await save_factor_strategy(redis, name="新", spec=_spec(rebalance_days=9))

        names = [r.name for r in await list_factor_strategies(redis)]

        assert names == ["新", "旧"]

    async def test_list_is_empty_when_nothing_saved(self, redis: FakeRedis) -> None:
        assert await list_factor_strategies(redis) == []

    async def test_resave_keeps_created_at_and_updates_spec(self, redis: FakeRedis) -> None:
        first = await save_factor_strategy(redis, name="策略", spec=_spec())

        second = await save_factor_strategy(redis, name="策略", spec=_spec(rebalance_days=21))

        assert second.created_at == first.created_at
        assert second.spec.rebalance_days == 21
        assert len(await list_factor_strategies(redis)) == 1

    async def test_delete_reports_existence(self, redis: FakeRedis) -> None:
        await save_factor_strategy(redis, name="策略", spec=_spec())

        assert await delete_factor_strategy(redis, "策略") is True
        assert await delete_factor_strategy(redis, "策略") is False
        assert await list_factor_strategies(redis) == []

    @pytest.mark.parametrize("bad", ["", "   ", "a:b", "危险*名", "x" * 121])
    async def test_illegal_names_rejected(self, redis: FakeRedis, bad: str) -> None:
        with pytest.raises(ValueError, match="策略名"):
            await save_factor_strategy(redis, name=bad, spec=_spec())

    async def test_corrupted_record_is_skipped_not_raised(self, redis: FakeRedis) -> None:
        await save_factor_strategy(redis, name="好的", spec=_spec())
        await save_factor_strategy(redis, name="坏的", spec=_spec())
        redis.strings["strategies:factor:坏的"] = "{ not json"

        records = await list_factor_strategies(redis)

        assert [r.name for r in records] == ["好的"]


# ── 实验 → 策略 ───────────────────────────────────────────────────


class TestPromoteToStrategy:
    async def test_promotes_and_back_references_the_experiment(self, redis: FakeRedis) -> None:
        record = await _record(redis)
        spec = _spec()

        name = await promote_to_strategy(redis, record.id, "提升后的策略", spec)

        stored = await get_factor_strategy(redis, name)
        assert stored is not None
        assert stored.source_experiment_id == record.id
        updated = await get_experiment(redis, record.id)
        assert updated is not None
        assert updated.promoted_strategy == "提升后的策略"

    async def test_promotion_survives_experiment_eviction(self, redis: FakeRedis) -> None:
        """契约验收 5：实验记录被 MAX_RECORDS 淘汰后策略仍完整可用。"""
        record = await _record(redis)
        await promote_to_strategy(redis, record.id, "长命策略", _spec())

        # 灌满记录器把来源实验挤出滚动窗口
        for i in range(MAX_RECORDS):
            await _record(redis, name=f"填充{i}")

        assert await get_experiment(redis, record.id) is None
        stored = await get_factor_strategy(redis, "长命策略")
        assert stored is not None
        assert stored.spec == _spec()
        assert build_factor_strategy(stored.spec) is not None

    async def test_promoting_unknown_experiment_still_stores_strategy(
        self, redis: FakeRedis
    ) -> None:
        name = await promote_to_strategy(redis, "已经没了", "孤儿策略", _spec())

        stored = await get_factor_strategy(redis, name)
        assert stored is not None
        assert stored.source_experiment_id == "已经没了"

    async def test_illegal_strategy_name_rejected(self, redis: FakeRedis) -> None:
        record = await _record(redis)

        with pytest.raises(ValueError, match="策略名"):
            await promote_to_strategy(redis, record.id, "非法:名", _spec())

    async def test_legacy_record_without_promoted_field_still_parses(
        self, redis: FakeRedis
    ) -> None:
        """老记录没有 promoted_strategy 字段，反序列化必须向后兼容。"""
        import json

        record = await _record(redis)
        payload = record.to_dict()
        payload.pop("promoted_strategy")
        redis.strings[f"experiments:record:{record.id}"] = json.dumps(payload)

        loaded = await get_experiment(redis, record.id)

        assert loaded is not None
        assert loaded.promoted_strategy is None
