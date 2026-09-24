"""自动因子循环 —— 异步编排测试（V3 · I2）

覆盖 `loop_runner.execute_round` 的端到端路径：入参解析 → 取数 → 切分 →
搜索 → 入库 → 复盘。取数、入库、LLM 全部替身，不碰数据库也不发网络请求。

重点仍然是那条红线：**样本外在搜索期不可见**。这里从最外层再验一次 ——
即使经过取数与面板构建这两道加工，喂给搜索器的面板末日期也不得越过 `is_end`。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.quant.lab import loop_runner
from app.quant.lab.auto_loop import FactorCandidate, LoopRound
from app.quant.lab.factor_eval import FactorScore
from app.quant.lab.loop_runner import (
    LoopDataError,
    _build_split,
    _parse_payload,
    execute_round,
    survivors_to_frame,
)

START = datetime(2024, 1, 2, tzinfo=UTC)
DAY = timedelta(days=1)
SYMBOLS = ("AAA", "BBB", "CCC", "DDD")
IS_END = "2024-04-30"

BASE_PAYLOAD = {
    "universe": list(SYMBOLS),
    "is_end": IS_END,
    "market": "US",
    "generations": 2,
    "population": 6,
    "top_k": 3,
    "forward_period": 5,
    "max_candidates_evaluated": 200,
    "seed": 3,
}


def _bars(symbol: str, offset: int, n: int = 220) -> list[Bar]:
    return [
        Bar(
            time=START + i * DAY,
            symbol=symbol,
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=(price := round(100.0 + 8 * math.sin((i + offset * 7) / 11.0) + i * 0.05, 4)),
            high=price * 1.001,
            low=price * 0.999,
            close=price,
            volume=500_000,
        )
        for i in range(n)
    ]


def _universe() -> dict[str, list[Bar]]:
    return {sym: _bars(sym, k) for k, sym in enumerate(SYMBOLS)}


@pytest.fixture(autouse=True)
def stub_io(monkeypatch: pytest.MonkeyPatch):
    """取数、入库、复盘三处 I/O 全部替身。"""

    async def fake_fetch(*_args, **_kwargs):
        return _universe()

    async def fake_persist(round_, market):
        del market
        return round_

    async def fake_review(_redis, round_, _config):
        return round_

    monkeypatch.setattr(loop_runner, "_fetch_bars", fake_fetch)
    monkeypatch.setattr(loop_runner, "_persist_artifact", fake_persist)
    monkeypatch.setattr(loop_runner, "_attach_review", fake_review)


class TestParsePayload:
    def test_rejects_a_universe_too_small_for_a_cross_section(self):
        with pytest.raises(LoopDataError):
            _parse_payload({**BASE_PAYLOAD, "universe": ["AAA"]})

    def test_rejects_a_malformed_is_end(self):
        with pytest.raises(LoopDataError):
            _parse_payload({**BASE_PAYLOAD, "is_end": "not-a-date"})

    def test_rejects_a_missing_is_end(self):
        payload = {k: v for k, v in BASE_PAYLOAD.items() if k != "is_end"}
        with pytest.raises(LoopDataError):
            _parse_payload(payload)

    def test_normalizes_symbols_and_carries_the_budget(self):
        config, _fitness, fetch = _parse_payload(
            {**BASE_PAYLOAD, "universe": [" aaa ", "bbb", "ccc"]}
        )
        assert config.universe == ("AAA", "BBB", "CCC")
        assert config.to_ga_config().max_evaluations == 200
        assert fetch["market"] == "US"


class TestBuildSplit:
    def test_split_boundary_survives_panel_construction(self):
        """经过 bars → 面板 → 切分之后，样本内仍然停在 is_end。"""
        config, _fitness, _fetch = _parse_payload(BASE_PAYLOAD)

        split = _build_split(_universe(), config)

        assert split.in_sample.feature_end.isoformat() == IS_END
        assert split.in_sample.label_end < split.is_end
        assert split.has_out_of_sample
        assert split.out_of_sample.forward_return_panel.index.get_level_values(
            "datetime"
        ).min() > IS_END


class TestExecuteRound:
    async def test_full_round_reports_both_samples_and_the_denominator(self):
        result = await execute_round(None, BASE_PAYLOAD, round_id="r-42")

        assert result["round_id"] == "r-42"
        assert result["is_end"] == IS_END
        assert result["out_of_sample_available"] is True
        assert result["hypotheses_tested"] > 0
        assert str(result["hypotheses_tested"]) in result["multiple_testing_note"]
        # 存活因子必须两套指标齐备
        for survivor in result["survivors"]:
            assert "is_ic_mean" in survivor
            assert survivor["out_of_sample_evaluated"] is True
            assert "oos_ic_mean" in survivor

    async def test_search_receives_only_the_in_sample_panels(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """最外层再验一次红线：搜索器拿到的 OHLCV 末日期不得越过 is_end。"""
        seen: list = []
        original = loop_runner.run_search_round

        def spy(split, config, fitness_config, seeds, round_id=None, **kwargs):
            def capture(panels, *args):
                seen.append(panels)
                from app.quant.lab.auto_loop import default_search

                return default_search(panels, *args)

            return original(
                split, config, fitness_config, seeds=seeds, round_id=round_id,
                search_fn=capture, **kwargs
            )

        monkeypatch.setattr(loop_runner, "run_search_round", spy)

        await execute_round(None, BASE_PAYLOAD)

        assert seen
        assert seen[0].feature_end.isoformat() == IS_END

    async def test_round_never_produces_a_strategy_reference(self):
        result = await execute_round(None, BASE_PAYLOAD)
        assert "promoted_strategy" not in result
        assert "strategy_name" not in result

    async def test_data_error_propagates_instead_of_returning_empty_results(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def empty_fetch(*_args, **_kwargs):
            raise LoopDataError("有效标的不足")

        monkeypatch.setattr(loop_runner, "_fetch_bars", empty_fetch)

        with pytest.raises(LoopDataError):
            await execute_round(None, BASE_PAYLOAD)


class TestTaskStateMachine:
    """Celery 任务层：queued → running → done / error，绝不留下悬空的 queued。"""

    @staticmethod
    def _patch_redis(monkeypatch: pytest.MonkeyPatch):
        from tests.fake_redis import FakeRedis

        fake = FakeRedis()
        fake.aclose = _noop  # type: ignore[attr-defined]
        monkeypatch.setattr("app.core.redis.get_redis_pool", lambda: None)
        monkeypatch.setattr("redis.asyncio.Redis", lambda connection_pool: fake)
        return fake

    async def test_success_marks_done_and_stores_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.quant.lab.loop_store import (
            STATUS_DONE,
            get_round,
            new_record,
            save_round,
        )
        from app.tasks.auto_loop import _run_guarded

        fake = self._patch_redis(monkeypatch)
        payload = {**BASE_PAYLOAD, "round_id": "r-ok"}
        await save_round(fake, new_record("r-ok", payload))

        outcome = await _run_guarded(payload)

        assert outcome["status"] == "ok"
        record = await get_round(fake, "r-ok")
        assert record.status == STATUS_DONE
        assert record.result["hypotheses_tested"] > 0

    async def test_failure_is_recorded_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.quant.lab.loop_store import (
            STATUS_ERROR,
            get_round,
            new_record,
            save_round,
        )
        from app.tasks.auto_loop import _run_guarded

        fake = self._patch_redis(monkeypatch)

        async def boom(*_args, **_kwargs):
            raise LoopDataError("有效标的不足")

        monkeypatch.setattr(loop_runner, "_fetch_bars", boom)

        payload = {**BASE_PAYLOAD, "round_id": "r-bad"}
        await save_round(fake, new_record("r-bad", payload))

        outcome = await _run_guarded(payload)

        assert outcome["status"] == "error"
        assert "有效标的不足" in outcome["error"]
        record = await get_round(fake, "r-bad")
        assert record.status == STATUS_ERROR
        assert "有效标的不足" in record.error


async def _noop() -> None:
    return None


class TestSurvivorsFrame:
    def test_frame_keeps_both_samples_as_separate_columns(self):
        candidate = FactorCandidate(
            expr="DIV(MOM20, ATR_RATIO)",
            tokens=("MOM20", "ATR_RATIO", "DIV"),
            in_sample=FactorScore(1.0, 0.08, 0.07, 0.9, 0.05, 2.0, 100),
            out_of_sample=FactorScore(0.5, 0.02, 0.01, 0.3, 0.01, 1.0, 40),
            ic_decay_ratio=0.25,
            overfit_suspect=True,
        )
        round_ = LoopRound(
            round_id="r", seeds=(), survivors=(candidate,), hypotheses_tested=9,
        )

        frame = survivors_to_frame(round_)

        assert {"is_ic_mean", "oos_ic_mean", "overfit_suspect"} <= set(frame.columns)
        assert frame.loc[0, "is_ic_mean"] == pytest.approx(0.08)
        assert frame.loc[0, "oos_ic_mean"] == pytest.approx(0.02)
        assert bool(frame.loc[0, "overfit_suspect"]) is True
