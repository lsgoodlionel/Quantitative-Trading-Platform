"""
自适应再训练测试（V4 · M6）

覆盖契约 §4.3 的四条验收：

1. 重训**不自动替换线上模型**（产物标签 + 没有任何上线代码路径）
2. 新旧模型的样本外指标**并排**出现在产出与产物标签里
3. 训练中途失败 → 旧模型不变（一次写入都没发生）+ 发通知
4. 检测到漂移**不**自动触发重训

外加：时序切分不泄漏、样本外对比走同一段数据、失败通知带原因。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.quant.drift import DriftAwareAlphaModel
from app.quant.ml_strategy import FEATURE_NAMES
from app.quant.models.linear import LassoAlphaModel
from app.quant.retrain import (
    RetrainConfig,
    RetrainError,
    build_feature_frame,
    build_training_frame,
    compare_metrics,
    evaluate_out_of_sample,
    run_drift_check,
    run_retrain,
    signal_sharpe,
    split_train_holdout,
    train_and_compare,
)

SYMBOLS = ("AAPL", "MSFT", "NVDA")


# ── 替身 ──────────────────────────────────────────────────────────

class FakeMeta:
    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id


class FakeLabStore:
    """内存版产物库。只实现 M6 用到的两个方法，其余一律不该被调用。"""

    def __init__(self) -> None:
        self.saved: list[tuple[str, object, dict]] = []
        self.models: dict[str, object] = {}
        self.load_error: Exception | None = None
        #: 被调用过的方法名 —— 用来证明重训只读写产物，没走任何上线路径
        self.calls: list[str] = []

    async def save_model(self, name: str, model, tags=None) -> FakeMeta:
        self.calls.append("save_model")
        # 用真 UUID：真实 LabStore 会 `validate_artifact_id`，
        # 替身发一个 "artifact-1" 出去会让测试假装通过在别处失败
        artifact_id = str(uuid.uuid4())
        self.saved.append((name, model, dict(tags or {})))
        self.models[artifact_id] = model
        return FakeMeta(artifact_id)

    async def load_model(self, artifact_id: str):
        self.calls.append("load_model")
        if self.load_error is not None:
            raise self.load_error
        if artifact_id not in self.models:
            raise KeyError(f"no such artifact: {artifact_id}")
        return self.models[artifact_id]

    # 上线路径的哨兵：一旦被调用说明有人偷偷加了自动上线
    async def promote_to_strategy(self, *args, **kwargs):  # pragma: no cover - 不该被调用
        raise AssertionError("重训绝不能自动上线")


def _bars(symbol: str, n: int, seed: int, start_price: float = 100.0) -> list[Bar]:
    """构造一段有趋势 + 噪声的日线，保证技术指标算得出来。"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.0006, scale=0.015, size=n)
    prices = start_price * np.exp(np.cumsum(steps))
    base = datetime(2023, 1, 2, tzinfo=UTC)
    out: list[Bar] = []
    for i, close in enumerate(prices):
        high = float(close * (1 + abs(rng.normal(0, 0.004))))
        low = float(close * (1 - abs(rng.normal(0, 0.004))))
        out.append(
            Bar(
                time=base + timedelta(days=i),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=float(close),
                high=max(high, float(close)),
                low=min(low, float(close)),
                close=float(close),
                volume=int(rng.integers(1_000_000, 5_000_000)),
            )
        )
    return out


def _bars_by_symbol(n: int = 400, seed: int = 7) -> dict[str, list[Bar]]:
    return {sym: _bars(sym, n, seed + i) for i, sym in enumerate(SYMBOLS)}


def _config(**overrides) -> RetrainConfig:
    params = {"symbols": SYMBOLS, "market": "US", "lookback_days": 730, "model_kind": "lasso"}
    params.update(overrides)
    return RetrainConfig(**params)


@pytest.fixture
def frame() -> pd.DataFrame:
    return build_training_frame(_bars_by_symbol(), forward_period=5)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeLabStore:
    """
    内存产物库，并接管 `_resolve_store` 的兜底路径。

    没有这一步，走任务层（不接受 store 参数）的用例会掉进真实的
    `create_async_engine` —— 单测不该碰数据库。
    """
    from contextlib import asynccontextmanager

    import app.quant.retrain as retrain_module

    fake = FakeLabStore()

    @asynccontextmanager
    async def _resolve(_store=None):
        yield fake

    monkeypatch.setattr(retrain_module, "_resolve_store", _resolve)
    return fake


@pytest.fixture
def stub_fetch(monkeypatch: pytest.MonkeyPatch):
    """拦下取数 —— 单测不该碰数据库。"""
    import app.quant.retrain as retrain_module

    bars = _bars_by_symbol()

    async def _fake(config):
        return bars

    monkeypatch.setattr(retrain_module, "_fetch_bars", _fake)
    return bars


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list:
    """拦下通知派发，只记录事件。"""
    events: list = []
    import app.notify.emit as emit_module

    def _fake_dispatch(event):
        events.append(event)
        return {"dispatched": 1}

    monkeypatch.setattr(emit_module, "notify_safe", lambda event: _fake_dispatch(event))
    return events


# ── 建表 / 切分 ───────────────────────────────────────────────────

class TestTrainingFrame:
    def test_frame_has_features_and_label(self):
        frame = build_training_frame(_bars_by_symbol(), forward_period=5)

        assert list(frame.columns) == [*FEATURE_NAMES, "label"]
        assert frame.index.names == ["datetime", "instrument"]
        assert not frame.isna().to_numpy().any()

    def test_too_few_bars_raises(self):
        with pytest.raises(RetrainError, match="根 bar"):
            build_training_frame({"AAPL": _bars("AAPL", 30, seed=1)}, forward_period=5)

    def test_feature_frame_keeps_the_newest_bars(self):
        """
        漂移检测用的无标签特征表必须保留最新几根 bar ——
        带标签的那张会因 label=NaN 丢掉每个标的末尾 forward_period 行。
        """
        bars = _bars_by_symbol()

        features = build_feature_frame(bars)
        labeled = build_training_frame(bars, forward_period=5)

        assert list(features.columns) == list(FEATURE_NAMES)
        newest_feature = max(features.index.get_level_values("datetime"))
        newest_labeled = max(labeled.index.get_level_values("datetime"))
        assert newest_feature > newest_labeled


class TestSplit:
    def test_holdout_is_strictly_after_train(self, frame: pd.DataFrame):
        """时序切分：样本外的每一天都必须晚于训练集的最后一天。"""
        train, holdout = split_train_holdout(frame, 0.2)

        train_dates = train.index.get_level_values("datetime")
        holdout_dates = holdout.index.get_level_values("datetime")
        assert max(train_dates) < min(holdout_dates)
        assert len(train) + len(holdout) == len(frame)

    def test_no_date_appears_on_both_sides(self, frame: pd.DataFrame):
        """同一天的横截面绝不能被切到两边 —— 那是最隐蔽的一种泄漏。"""
        train, holdout = split_train_holdout(frame, 0.2)

        overlap = set(train.index.get_level_values("datetime")) & set(
            holdout.index.get_level_values("datetime")
        )
        assert overlap == set()

    def test_empty_frame_raises(self):
        with pytest.raises(RetrainError):
            split_train_holdout(pd.DataFrame(), 0.2)


class TestSharpe:
    def test_zero_variance_returns_zero_not_nan(self):
        idx = pd.MultiIndex.from_product(
            [["2024-01-01", "2024-01-02"], ["A"]], names=["datetime", "instrument"]
        )
        pred = pd.Series([1.0, 1.0], index=idx)
        label = pd.Series([0.01, 0.01], index=idx)

        assert signal_sharpe(pred, label, 5) == 0.0

    def test_perfect_signal_beats_inverted_signal(self):
        rng = np.random.default_rng(3)
        label = pd.Series(rng.normal(size=200))
        good = signal_sharpe(label.copy(), label, 5)
        bad = signal_sharpe(-label, label, 5)

        assert good > 0 > bad


# ── 契约 §4.3.2：新旧指标并排 ─────────────────────────────────────

class TestSideBySideComparison:
    def test_previous_model_scored_on_the_same_holdout(self, frame: pd.DataFrame):
        # Arrange：先训一个「旧模型」
        train, _ = split_train_holdout(frame, 0.2)
        previous = LassoAlphaModel()
        previous.fit(train)

        # Act
        bundle = train_and_compare(frame, _config(), previous_model=previous)

        # Assert：两套指标同键、同一段样本外
        assert bundle.outcome.previous_metrics is not None
        assert set(bundle.outcome.new_metrics) == set(bundle.outcome.previous_metrics)
        assert bundle.outcome.previous_metrics["n_samples"] == bundle.outcome.n_holdout_rows
        assert bundle.outcome.new_metrics["n_samples"] == bundle.outcome.n_holdout_rows

    def test_comparison_carries_deltas_for_ic_and_sharpe(self, frame: pd.DataFrame):
        train, _ = split_train_holdout(frame, 0.2)
        previous = LassoAlphaModel()
        previous.fit(train)

        comparison = train_and_compare(frame, _config(), previous_model=previous).outcome.comparison

        assert comparison["has_previous"] is True
        for key in ("ic_delta", "rank_ic_delta", "sharpe_delta"):
            assert key in comparison

    def test_missing_previous_model_is_stated_not_assumed_better(self, frame: pd.DataFrame):
        """没有旧模型时 is_better 必须是 None —— 默认 True 等于替用户下结论。"""
        outcome = train_and_compare(frame, _config()).outcome

        assert outcome.previous_metrics is None
        assert outcome.previous_unavailable_reason
        assert outcome.comparison["is_better"] is None
        assert outcome.comparison["has_previous"] is False

    def test_compare_metrics_delta_is_new_minus_old(self):
        result = compare_metrics(
            {"ic": 0.10, "rank_ic": 0.08, "sharpe": 1.2},
            {"ic": 0.04, "rank_ic": 0.03, "sharpe": 0.9},
        )

        assert result["ic_delta"] == pytest.approx(0.06)
        assert result["sharpe_delta"] == pytest.approx(0.3)
        assert result["is_better"] is True


# ── 契约 §4.3.1：不自动上线 ───────────────────────────────────────

class TestNeverAutoActivates:
    async def test_artifact_is_tagged_not_activated(
        self, store: FakeLabStore, stub_fetch, captured_events: list
    ):
        outcome = await run_retrain(_config(), store=store)

        assert len(store.saved) == 1
        _, _, tags = store.saved[0]
        assert tags["activated"] == "false"
        assert tags["promoted"] == "false"
        assert outcome.activated is False
        assert "未上线" in outcome.to_dict()["activation_note"] or "人工" in outcome.to_dict()[
            "activation_note"
        ]

    async def test_only_touches_save_and_load(self, store: FakeLabStore, stub_fetch):
        """
        哨兵测试：重训全程只该调 `save_model` / `load_model`。
        `FakeLabStore.promote_to_strategy` 一旦被调用就直接 AssertionError。
        """
        await run_retrain(_config(), store=store)

        assert set(store.calls) <= {"save_model", "load_model"}
        assert store.calls.count("save_model") == 1

    async def test_new_and_previous_metrics_both_land_in_artifact_tags(
        self, store: FakeLabStore, stub_fetch
    ):
        first = await run_retrain(_config(), store=store)

        second = await run_retrain(
            _config(previous_artifact_id=first.artifact_id), store=store
        )

        _, _, tags = store.saved[-1]
        assert tags["has_previous_metrics"] == "true"
        assert tags["previous_artifact_id"] == first.artifact_id
        assert second.previous_metrics is not None
        assert "oos_ic" in tags
        assert "oos_sharpe" in tags

    async def test_saved_object_carries_the_training_baseline(
        self, store: FakeLabStore, stub_fetch
    ):
        """契约 §2.2.2：训练集统计量必须随模型一起持久化。"""
        await run_retrain(_config(), store=store)

        _, model, _ = store.saved[0]
        assert isinstance(model, DriftAwareAlphaModel)
        assert model.baseline.n_train_rows > 0


# ── 契约 §4.3.3：失败不留半个模型 ─────────────────────────────────

class TestFailureLeavesOldModelUntouched:
    async def test_training_failure_writes_nothing(
        self, store: FakeLabStore, stub_fetch, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange：让训练在写入之前炸掉
        import app.quant.retrain as retrain_module

        def _boom(*args, **kwargs):
            raise RuntimeError("模型训练炸了")

        monkeypatch.setattr(retrain_module, "train_and_compare", _boom)

        # Act / Assert
        with pytest.raises(RuntimeError, match="炸了"):
            await run_retrain(_config(), store=store)
        assert store.saved == []

    async def test_evaluation_failure_writes_nothing(
        self, store: FakeLabStore, stub_fetch, monkeypatch: pytest.MonkeyPatch
    ):
        import app.quant.retrain as retrain_module

        def _boom(*args, **kwargs):
            raise RuntimeError("样本外打分炸了")

        monkeypatch.setattr(retrain_module, "evaluate_out_of_sample", _boom)

        with pytest.raises(RuntimeError):
            await run_retrain(_config(), store=store)
        assert store.saved == []

    async def test_previous_model_load_failure_degrades_but_does_not_abort(
        self, store: FakeLabStore, stub_fetch
    ):
        """
        旧模型加载失败只降级为「无对比」，不让整次重训白跑 —— 但必须写明原因。
        """
        store.load_error = RuntimeError("产物文件被删了")

        outcome = await run_retrain(_config(previous_artifact_id="gone"), store=store)

        assert outcome.artifact_id is not None
        assert outcome.previous_metrics is None
        assert "加载失败" in (outcome.previous_unavailable_reason or "")

    async def test_task_layer_emits_failure_notification(
        self, monkeypatch: pytest.MonkeyPatch, captured_events: list
    ):
        """训练失败必须发通知，否则「模型三个月没更新过」不会有任何人知道。"""
        import app.quant.retrain as retrain_module
        from app.tasks.retrain import _run_retrain_guarded
        from tests.fake_redis import FakeRedis

        async def _boom(config):
            raise RetrainError("窗口内没有任何标的达标")

        monkeypatch.setattr(retrain_module, "run_retrain", _boom)
        monkeypatch.setattr("app.tasks.retrain._redis", _fake_redis_ctx(FakeRedis()))

        result = await _run_retrain_guarded(
            {"job_id": "job-1", "symbols": list(SYMBOLS), "model_kind": "lasso"}
        )

        assert result["status"] == "error"
        assert len(captured_events) == 1
        assert captured_events[0].type.value == "retrain_done"
        assert "失败" in captured_events[0].title
        assert "未被改动" in captured_events[0].payload["影响"]


def _fake_redis_ctx(redis):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield redis

    return _ctx


# ── 契约 §4.3.4：漂移不触发重训 ───────────────────────────────────

class TestDriftNeverTriggersRetrain:
    async def test_drift_check_result_says_no_retrain(
        self, store: FakeLabStore, stub_fetch
    ):
        trained = await run_retrain(_config(), store=store)

        result = await run_drift_check(
            trained.artifact_id, _config(lookback_days=180), store=store
        )

        assert result["retrain_triggered"] is False
        assert "不自动重训" in result["retrain_note"]
        assert store.saved == [store.saved[0]]  # 没有产生第二份产物

    async def test_drift_check_does_not_dispatch_any_celery_task(
        self, store: FakeLabStore, stub_fetch, monkeypatch: pytest.MonkeyPatch
    ):
        """
        哨兵：把重训任务的 `delay` 换成会炸的桩。漂移检测路径一旦派发就红。
        """
        import app.tasks.retrain as task_module
        from tests.fake_redis import FakeRedis

        class _Explode:
            @staticmethod
            def delay(*args, **kwargs):
                raise AssertionError("漂移检测绝不能派发重训任务")

        monkeypatch.setattr(task_module, "run_retrain_task", _Explode)
        monkeypatch.setattr(task_module, "_redis", _fake_redis_ctx(FakeRedis()))

        trained = await run_retrain(_config(), store=store)
        result = await task_module._run_drift_check_guarded(
            {
                "job_id": "job-2",
                "artifact_id": trained.artifact_id,
                "symbols": list(SYMBOLS),
                "lookback_days": 180,
            }
        )

        assert result["status"] == "ok"
        assert result["result"]["retrain_triggered"] is False

    async def test_drift_notification_only_fires_when_drifting(
        self, store: FakeLabStore, stub_fetch, captured_events: list, monkeypatch
    ):
        """
        没漂就不发通知：每次检测都响一下，等于训练用户忽略它。
        """
        import app.tasks.retrain as task_module
        from tests.fake_redis import FakeRedis

        monkeypatch.setattr(task_module, "_redis", _fake_redis_ctx(FakeRedis()))
        trained = await run_retrain(_config(), store=store)
        captured_events.clear()

        # 同一段数据 → 不漂
        await task_module._run_drift_check_guarded(
            {
                "job_id": "job-3",
                "artifact_id": trained.artifact_id,
                "symbols": list(SYMBOLS),
                "lookback_days": 730,
            }
        )
        assert captured_events == []

        # 阈值压到极低 → 判漂 → 发一条 model_drift
        await task_module._run_drift_check_guarded(
            {
                "job_id": "job-4",
                "artifact_id": trained.artifact_id,
                "symbols": list(SYMBOLS),
                "lookback_days": 730,
                "di_threshold": 1e-6,
                "outlier_ratio_threshold": 1e-6,
            }
        )
        assert len(captured_events) == 1
        assert captured_events[0].type.value == "model_drift"
        assert "不会自动重训" in captured_events[0].payload["处置"]

    async def test_drift_check_rejects_model_without_baseline(
        self, store: FakeLabStore, stub_fetch, frame: pd.DataFrame
    ):
        """M6 之前归档的裸模型没有分布快照 —— 明确报错，而不是拿新数据现算一个。"""
        bare = LassoAlphaModel()
        bare.fit(frame)
        meta = await store.save_model("legacy", bare)

        with pytest.raises(RetrainError, match="分布快照"):
            await run_drift_check(meta.artifact_id, _config(), store=store)


# ── 配置校验 ──────────────────────────────────────────────────────

class TestConfigValidation:
    def test_empty_symbols_rejected(self):
        with pytest.raises(RetrainError, match="symbols"):
            RetrainConfig(symbols=())

    def test_unknown_model_kind_rejected(self):
        with pytest.raises(RetrainError, match="未知模型类型"):
            RetrainConfig(symbols=SYMBOLS, model_kind="xgboost")

    def test_holdout_ratio_bounds(self):
        with pytest.raises(RetrainError, match="holdout_ratio"):
            RetrainConfig(symbols=SYMBOLS, holdout_ratio=0.9)

    def test_window_derived_from_lookback(self):
        from datetime import date

        config = RetrainConfig(symbols=SYMBOLS, end=date(2024, 6, 30), lookback_days=100)

        assert config.end_date.isoformat() == "2024-06-30"
        assert config.start_date.isoformat() == "2024-03-22"


# ── 契约 §3.2：定时调度默认关闭 ───────────────────────────────────

class TestScheduleDefaultsOff:
    def test_setting_is_off_by_default(self):
        from app.core.config import settings

        assert settings.retrain_schedule_enabled is False

    def test_beat_schedule_has_no_retrain_entry_by_default(self):
        """
        关闭时连 beat 条目都不注册 —— 注册一个进去就立刻 return 的任务，
        只会在 beat 日志里每周留下一条看起来像在工作的记录。
        """
        from app.tasks.celery_app import celery_app

        assert "adaptive-retrain" not in celery_app.conf.beat_schedule

    def test_scheduled_task_skips_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """任务体内还有第二道开关检查（防御性），且要留下「因为关着」的证据。"""
        import app.quant.retrain as retrain_module
        from app.tasks.retrain import scheduled_retrain_task

        async def _never(config):  # pragma: no cover - 不该被调用
            raise AssertionError("开关关着时绝不能真的开训")

        monkeypatch.setattr(retrain_module, "run_retrain", _never)

        result = scheduled_retrain_task()

        assert result["status"] == "disabled"
        assert "retrain_schedule_enabled" in result["reason"]

    def test_scheduled_task_skips_when_symbols_empty(self, monkeypatch: pytest.MonkeyPatch):
        """开着但没配标的：跳过并说明原因，不能拿一个空 universe 去开训。"""
        from app.core.config import settings
        from app.tasks.retrain import scheduled_retrain_task

        monkeypatch.setattr(settings, "retrain_schedule_enabled", True)
        monkeypatch.setattr(settings, "retrain_symbols", "  ,  ")

        result = scheduled_retrain_task()

        assert result["status"] == "skipped"
        assert "retrain_symbols" in result["reason"]


# ── 样本外评估口径 ────────────────────────────────────────────────

class TestOutOfSampleEvaluation:
    def test_metrics_keys_are_stable(self, frame: pd.DataFrame):
        train, holdout = split_train_holdout(frame, 0.2)
        model = LassoAlphaModel()
        model.fit(train)

        metrics = evaluate_out_of_sample(model, holdout, "label", 5)

        assert set(metrics) == {"ic", "rank_ic", "n_samples", "sharpe"}
        assert all(np.isfinite(v) for v in metrics.values())
