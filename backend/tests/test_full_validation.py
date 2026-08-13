"""
完整验证套件测试（V3 · H2）

覆盖：
1. 五步全跑的正常路径
2. 某一步抛异常时其余步骤照常返回，该步带 error，评级标注「基于 N/5 步」
3. steps 参数只跑指定步骤
4. 评级规则的边界用例（五条扣分项各一个）
5. 参数空间推导 / 窗口自适应 / 得分离散度等纯函数
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.engine.backtest.full_validation import (
    MAX_ERROR_CHARS,
    resolve_steps,
    run_full_validation,
)
from app.engine.backtest.validation_grade import (
    ALL_STEPS,
    BASE_SCORE,
    MC_P5_RETURN_THRESHOLD,
    OOS_SHARPE_RETENTION_THRESHOLD,
    PARAM_SENSITIVITY_THRESHOLD,
    PENALTY_BIAS_DETECTED,
    PENALTY_MC_P5_NEGATIVE,
    PENALTY_NEGATIVE_SHARPE,
    PENALTY_OOS_SHARPE_DECAY,
    PENALTY_PARAM_SENSITIVITY,
    STEP_BACKTEST,
    STEP_BIAS,
    STEP_OPTIMIZE,
    STEP_ROBUSTNESS,
    STEP_WALKFORWARD,
    TOTAL_STEPS,
    classify_score,
    grade_validation,
)
from app.engine.backtest.validation_steps import (
    DEFAULT_PROBE_SPACE,
    auto_window_sizes,
    compute_score_dispersion,
    derive_param_space,
)

# ── 干净样本：五步全通过、不触发任何扣分项 ─────────────────────────

def _clean_steps() -> dict[str, dict]:
    return {
        STEP_BACKTEST: {"metrics": {"sharpe_ratio": 1.5, "total_trades": 20}, "final_value": 120_000.0},
        STEP_OPTIMIZE: {"best_score": 1.5, "score_dispersion": 0.1, "trials": []},
        STEP_WALKFORWARD: {"avg_is_sharpe": 1.2, "avg_oos_sharpe": 1.0, "total_windows": 3},
        STEP_BIAS: {"has_lookahead_bias": False, "has_recursive_bias": False, "total_signals": 12},
        STEP_ROBUSTNESS: {"p5_total_return_pct": 4.2, "prob_profit": 0.9},
    }


def _runners_from(steps: dict[str, dict]) -> dict:
    return {name: (lambda payload=payload: payload) for name, payload in steps.items()}


# ══════════════════════════════════════════════════════════════════
# 1. 正常路径
# ══════════════════════════════════════════════════════════════════

def test_runs_all_five_steps_and_grades_complete():
    # Arrange
    runners = _runners_from(_clean_steps())

    # Act
    outcome = run_full_validation(runners)

    # Assert
    assert list(outcome.steps.keys()) == list(ALL_STEPS)
    assert outcome.grade is not None
    assert outcome.grade.score == BASE_SCORE
    assert outcome.grade.level == "A"
    assert outcome.grade.is_complete is True
    assert outcome.grade.based_on == f"基于 {TOTAL_STEPS}/{TOTAL_STEPS} 步"
    assert outcome.grade.findings == []
    assert outcome.grade.not_evaluated == []


def test_run_id_is_generated_when_absent():
    outcome = run_full_validation(_runners_from(_clean_steps()))
    assert outcome.run_id
    assert run_full_validation(_runners_from(_clean_steps()), run_id="fixed").run_id == "fixed"


def test_grade_never_recommends_going_live():
    """评级是启发式汇总，不得输出结论性的实盘建议。"""
    grade = grade_validation(_clean_steps(), requested=list(ALL_STEPS))
    blob = str(grade.to_dict())
    for banned in ("建议实盘", "可以实盘", "推荐实盘"):
        assert banned not in blob
    assert "不构成任何交易或实盘建议" in grade.disclaimer


# ══════════════════════════════════════════════════════════════════
# 2. 单步失败不中断整体
# ══════════════════════════════════════════════════════════════════

def test_failing_step_does_not_abort_others():
    # Arrange
    runners = _runners_from(_clean_steps())

    def boom() -> dict:
        raise RuntimeError("walkforward 数据不足")

    runners[STEP_WALKFORWARD] = boom

    # Act
    outcome = run_full_validation(runners)

    # Assert —— 其余四步照常，失败步带 error
    assert "error" in outcome.steps[STEP_WALKFORWARD]
    assert "walkforward 数据不足" in outcome.steps[STEP_WALKFORWARD]["error"]
    for step in (STEP_BACKTEST, STEP_OPTIMIZE, STEP_BIAS, STEP_ROBUSTNESS):
        assert "error" not in outcome.steps[step]

    grade = outcome.grade
    assert grade.based_on == f"基于 4/{TOTAL_STEPS} 步"
    assert grade.is_complete is False
    assert grade.failed_steps == [STEP_WALKFORWARD]
    # 失败步的规则进入 not_evaluated，而不是默默按通过处理
    assert [n.rule for n in grade.not_evaluated] == ["oos_sharpe_decay"]
    assert grade.not_evaluated[0].reason == "该步骤执行失败"


def test_all_steps_failing_yields_zero_coverage_not_perfect_score():
    def boom() -> dict:
        raise ValueError("engine down")

    outcome = run_full_validation(dict.fromkeys(ALL_STEPS, boom))

    assert outcome.grade.based_on == f"基于 0/{TOTAL_STEPS} 步"
    assert outcome.grade.is_complete is False
    assert len(outcome.grade.not_evaluated) == TOTAL_STEPS
    assert outcome.grade.findings == []


def test_missing_runner_reports_error_for_that_step():
    runners = _runners_from(_clean_steps())
    del runners[STEP_BIAS]

    outcome = run_full_validation(runners)

    assert "error" in outcome.steps[STEP_BIAS]
    assert outcome.grade.failed_steps == [STEP_BIAS]


def test_long_error_message_is_truncated():
    def boom() -> dict:
        raise RuntimeError("x" * 5000)

    outcome = run_full_validation({STEP_BACKTEST: boom}, steps=[STEP_BACKTEST])

    assert len(outcome.steps[STEP_BACKTEST]["error"]) <= MAX_ERROR_CHARS


# ══════════════════════════════════════════════════════════════════
# 3. steps 参数
# ══════════════════════════════════════════════════════════════════

def test_steps_parameter_runs_only_requested_steps():
    outcome = run_full_validation(
        _runners_from(_clean_steps()), steps=[STEP_BACKTEST, STEP_BIAS]
    )

    assert set(outcome.steps.keys()) == {STEP_BACKTEST, STEP_BIAS}
    assert outcome.requested_steps == [STEP_BACKTEST, STEP_BIAS]
    assert outcome.grade.based_on == f"基于 2/{TOTAL_STEPS} 步"
    assert set(outcome.grade.skipped_steps) == {STEP_OPTIMIZE, STEP_WALKFORWARD, STEP_ROBUSTNESS}
    assert {n.reason for n in outcome.grade.not_evaluated} == {"该步骤本次未运行"}


def test_resolve_steps_normalizes_order_and_case():
    assert resolve_steps(None) == list(ALL_STEPS)
    assert resolve_steps([]) == list(ALL_STEPS)
    assert resolve_steps(["BIAS", "backtest"]) == [STEP_BACKTEST, STEP_BIAS]


def test_resolve_steps_rejects_all_unknown():
    with pytest.raises(ValueError, match="无可执行步骤"):
        resolve_steps(["nope", "also_nope"])


def test_resolve_steps_ignores_unknown_when_some_valid():
    assert resolve_steps(["backtest", "nope"]) == [STEP_BACKTEST]


# ══════════════════════════════════════════════════════════════════
# 4. 评级规则边界用例（每条扣分项各一个）
# ══════════════════════════════════════════════════════════════════

def test_rule_negative_sharpe_boundary():
    # 恰好等于阈值 0.0 即命中（≤ 0）
    steps = _clean_steps()
    steps[STEP_BACKTEST]["metrics"]["sharpe_ratio"] = 0.0
    grade = grade_validation(steps, requested=list(ALL_STEPS))

    assert grade.score == BASE_SCORE - PENALTY_NEGATIVE_SHARPE
    assert [f.rule for f in grade.findings] == ["negative_sharpe"]

    # 略高于阈值则不命中
    steps[STEP_BACKTEST]["metrics"]["sharpe_ratio"] = 0.01
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []


def test_rule_param_sensitivity_boundary():
    steps = _clean_steps()
    steps[STEP_OPTIMIZE]["score_dispersion"] = PARAM_SENSITIVITY_THRESHOLD
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []

    steps[STEP_OPTIMIZE]["score_dispersion"] = PARAM_SENSITIVITY_THRESHOLD + 0.01
    grade = grade_validation(steps, requested=list(ALL_STEPS))
    assert grade.score == BASE_SCORE - PENALTY_PARAM_SENSITIVITY
    assert grade.findings[0].rule == "param_sensitivity"
    assert grade.findings[0].step == STEP_OPTIMIZE


def test_rule_param_sensitivity_skipped_when_dispersion_unknown():
    steps = _clean_steps()
    steps[STEP_OPTIMIZE]["score_dispersion"] = None
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []


def test_rule_oos_sharpe_decay_boundary():
    steps = _clean_steps()
    # 恰好 50% 保留率 → 不命中
    steps[STEP_WALKFORWARD] = {"avg_is_sharpe": 2.0, "avg_oos_sharpe": 2.0 * OOS_SHARPE_RETENTION_THRESHOLD}
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []

    # 低于 50% → 命中
    steps[STEP_WALKFORWARD] = {"avg_is_sharpe": 2.0, "avg_oos_sharpe": 0.9}
    grade = grade_validation(steps, requested=list(ALL_STEPS))
    assert grade.score == BASE_SCORE - PENALTY_OOS_SHARPE_DECAY
    assert grade.findings[0].rule == "oos_sharpe_decay"
    assert grade.findings[0].value == pytest.approx(0.45)


def test_rule_oos_decay_not_double_counted_when_is_sharpe_non_positive():
    """样本内夏普非正时由 negative_sharpe 规则处理，衰减规则不重复扣分。"""
    steps = _clean_steps()
    steps[STEP_WALKFORWARD] = {"avg_is_sharpe": -1.0, "avg_oos_sharpe": -3.0}
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []


def test_rule_bias_detected_lookahead():
    steps = _clean_steps()
    steps[STEP_BIAS]["has_lookahead_bias"] = True
    grade = grade_validation(steps, requested=list(ALL_STEPS))

    assert grade.score == BASE_SCORE - PENALTY_BIAS_DETECTED
    assert grade.findings[0].rule == "bias_detected"
    assert "前视偏差" in grade.findings[0].detail


def test_rule_bias_detected_recursive_only():
    steps = _clean_steps()
    steps[STEP_BIAS]["has_recursive_bias"] = True
    grade = grade_validation(steps, requested=list(ALL_STEPS))

    assert grade.score == BASE_SCORE - PENALTY_BIAS_DETECTED
    assert "递归偏差" in grade.findings[0].detail


def test_rule_mc_p5_negative_boundary():
    steps = _clean_steps()
    steps[STEP_ROBUSTNESS]["p5_total_return_pct"] = MC_P5_RETURN_THRESHOLD
    assert grade_validation(steps, requested=list(ALL_STEPS)).findings == []

    steps[STEP_ROBUSTNESS]["p5_total_return_pct"] = -0.01
    grade = grade_validation(steps, requested=list(ALL_STEPS))
    assert grade.score == BASE_SCORE - PENALTY_MC_P5_NEGATIVE
    assert grade.findings[0].rule == "mc_p5_negative"


def test_all_rules_hit_floors_at_zero_and_grade_d():
    steps = {
        STEP_BACKTEST: {"metrics": {"sharpe_ratio": -1.0}},
        STEP_OPTIMIZE: {"score_dispersion": 0.99},
        STEP_WALKFORWARD: {"avg_is_sharpe": 2.0, "avg_oos_sharpe": 0.1},
        STEP_BIAS: {"has_lookahead_bias": True, "has_recursive_bias": True},
        STEP_ROBUSTNESS: {"p5_total_return_pct": -30.0},
    }
    grade = grade_validation(steps, requested=list(ALL_STEPS))

    assert len(grade.findings) == 5
    assert grade.score == 0.0
    assert grade.level == "D"


def test_findings_carry_traceable_evidence():
    steps = _clean_steps()
    steps[STEP_ROBUSTNESS]["p5_total_return_pct"] = -5.0
    finding = grade_validation(steps, requested=list(ALL_STEPS)).findings[0]

    assert finding.step == STEP_ROBUSTNESS
    assert finding.metric == "p5_total_return_pct"
    assert finding.value == -5.0
    assert finding.threshold == MC_P5_RETURN_THRESHOLD
    assert finding.penalty == PENALTY_MC_P5_NEGATIVE
    assert finding.detail


@pytest.mark.parametrize(
    ("floor", "level"),
    [(85.0, "A"), (70.0, "B"), (55.0, "C"), (0.0, "D")],
)
def test_grade_thresholds_are_inclusive_lower_bounds(floor: float, level: str):
    """每档的下界必须归入本档，下界减一分则落到下一档。"""
    assert classify_score(floor)[0] == level
    if floor > 0.0:
        assert classify_score(floor - 0.01)[0] != level


# ══════════════════════════════════════════════════════════════════
# 5. 辅助纯函数
# ══════════════════════════════════════════════════════════════════

def test_derive_param_space_perturbs_numeric_params():
    space = derive_param_space({"fast_period": 10, "slow_period": 30, "ma_type": "sma"})

    assert "ma_type" not in space
    assert space["fast_period"] == [7, 10, 13]
    assert space["slow_period"] == [21, 30, 39]


def test_derive_param_space_falls_back_when_no_numeric_params():
    assert derive_param_space({}) == DEFAULT_PROBE_SPACE
    assert derive_param_space({"ma_type": "ema"}) == DEFAULT_PROBE_SPACE


def test_derive_param_space_respects_int_lower_bound():
    space = derive_param_space({"period": 2})
    assert min(space.get("period", [2])) >= 2


def test_auto_window_sizes_shrinks_when_bars_insufficient():
    train, test = auto_window_sizes(100, 250, 60)
    assert train + test <= 100
    assert train == 60
    assert test == 20


def test_auto_window_sizes_keeps_explicit_values_when_they_fit():
    assert auto_window_sizes(1000, 250, 60) == (250, 60)


def test_compute_score_dispersion():
    assert compute_score_dispersion([1.0, 0.5, 0.5]) == pytest.approx(0.5)
    assert compute_score_dispersion([1.0, 1.0, 1.0]) == pytest.approx(0.0)


def test_compute_score_dispersion_needs_three_samples():
    assert compute_score_dispersion([1.0, 0.2]) is None
    assert compute_score_dispersion([]) is None


# ══════════════════════════════════════════════════════════════════
# 6. 端点集成（行情用桩，不连数据库）
# ══════════════════════════════════════════════════════════════════

ENDPOINT = "/api/v1/backtests/full-validation"


def _request_body(**overrides) -> dict:
    body = {
        "strategy_name": "double_ma",
        "symbol": "AAPL",
        "market": "US",
        "frequency": "1d",
        "start_date": "2023-01-01",
        "end_date": "2023-06-30",
        "initial_cash": 100_000.0,
        "params": {"fast_period": 5, "slow_period": 20},
        "n_trials": 4,
        "n_scenarios": 60,
    }
    body.update(overrides)
    return body


@pytest.fixture
def api_client():
    from app.api.v1.endpoints.backtest_full_validation import get_service
    from app.main import app
    from tests.test_backtest_history import _make_bars, _StubService

    app.dependency_overrides[get_service] = lambda: _StubService(_make_bars(180))
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


async def test_endpoint_returns_steps_and_grade(api_client):
    async with api_client as c:
        resp = await c.post(ENDPOINT, json=_request_body())

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["requested_steps"] == list(ALL_STEPS)
    assert set(data["steps"].keys()) == set(ALL_STEPS)
    assert data["grade"]["based_on"].startswith("基于 ")
    assert "不构成任何交易或实盘建议" in data["grade"]["disclaimer"]


async def test_endpoint_honours_steps_subset(api_client):
    async with api_client as c:
        resp = await c.post(ENDPOINT, json=_request_body(steps=[STEP_BACKTEST]))

    data = resp.json()
    assert set(data["steps"].keys()) == {STEP_BACKTEST}
    assert data["grade"]["based_on"] == f"基于 1/{TOTAL_STEPS} 步"
    assert set(data["grade"]["skipped_steps"]) == set(ALL_STEPS) - {STEP_BACKTEST}


async def test_endpoint_rejects_unknown_strategy(api_client):
    async with api_client as c:
        resp = await c.post(ENDPOINT, json=_request_body(strategy_name="nope"))
    assert resp.status_code == 400


async def test_endpoint_rejects_all_unknown_steps(api_client):
    async with api_client as c:
        resp = await c.post(ENDPOINT, json=_request_body(steps=["nope"]))
    assert resp.status_code == 400


# ── 异步入口 ─────────────────────────────────────────────────────
#
# 同步端点在默认参数下够用，但 n_trials / n_scenarios / 回测区间都是用户可调的，
# 调大后同步路径会撞网关超时 —— 那时用户拿到 504，跑掉的算力全部作废。

ASYNC_ENDPOINT = f"{ENDPOINT}/async"


async def test_async_submit_returns_task_id(api_client, monkeypatch):
    from app.tasks import validation as validation_task

    monkeypatch.setattr(
        validation_task.run_full_validation_task,
        "delay",
        lambda payload: SimpleNamespace(id="task-123"),
    )

    async with api_client as c:
        resp = await c.post(ASYNC_ENDPOINT, json=_request_body())

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"task_id": "task-123", "status": "queued"}


async def test_async_submit_validates_input_before_queueing(api_client, monkeypatch):
    """配置错了要在提交那一刻就知道，而不是轮询半天拿到「未知策略」。"""
    called = False

    def _delay(payload):
        nonlocal called
        called = True
        return SimpleNamespace(id="never")

    from app.tasks import validation as validation_task

    monkeypatch.setattr(validation_task.run_full_validation_task, "delay", _delay)

    async with api_client as c:
        resp = await c.post(ASYNC_ENDPOINT, json=_request_body(strategy_name="nope"))

    assert resp.status_code == 400
    assert not called, "校验未通过就不该把任务扔进队列"


async def test_async_submit_reports_broker_outage(api_client, monkeypatch):
    from app.tasks import validation as validation_task

    def _boom(payload):
        raise ConnectionError("redis down")

    monkeypatch.setattr(validation_task.run_full_validation_task, "delay", _boom)

    async with api_client as c:
        resp = await c.post(ASYNC_ENDPOINT, json=_request_body())

    assert resp.status_code == 503
    assert "任务队列不可用" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [("PENDING", "queued"), ("RECEIVED", "queued"), ("STARTED", "running")],
)
async def test_async_result_reports_in_flight_states(api_client, monkeypatch, state, expected):
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        lambda task_id, app=None: SimpleNamespace(state=state, result=None),
    )

    async with api_client as c:
        resp = await c.get(f"{ASYNC_ENDPOINT}/task-123")

    assert resp.json()["status"] == expected
    assert resp.json()["result"] is None


async def test_async_result_returns_payload_on_success(api_client, monkeypatch):
    payload = {"status": "ok", "run_id": "r1", "steps": {}, "grade": {}}
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        lambda task_id, app=None: SimpleNamespace(state="SUCCESS", result=payload),
    )

    async with api_client as c:
        resp = await c.get(f"{ASYNC_ENDPOINT}/task-123")

    data = resp.json()
    assert data["status"] == "done"
    assert data["result"]["run_id"] == "r1"


async def test_async_result_surfaces_task_level_error(api_client, monkeypatch):
    """任务内部捕获的失败要带原因回到前端，而不是一句 traceback。"""
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        lambda task_id, app=None: SimpleNamespace(
            state="SUCCESS", result={"status": "error", "error": "数据不足"}
        ),
    )

    async with api_client as c:
        resp = await c.get(f"{ASYNC_ENDPOINT}/task-123")

    data = resp.json()
    assert data["status"] == "error"
    assert data["error"] == "数据不足"


async def test_async_result_surfaces_celery_failure(api_client, monkeypatch):
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        lambda task_id, app=None: SimpleNamespace(
            state="FAILURE", result=RuntimeError("worker 崩了")
        ),
    )

    async with api_client as c:
        resp = await c.get(f"{ASYNC_ENDPOINT}/task-123")

    assert resp.json()["status"] == "error"
    assert "worker 崩了" in resp.json()["error"]
