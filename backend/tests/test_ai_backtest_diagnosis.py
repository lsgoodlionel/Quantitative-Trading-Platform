"""回测 AI 诊断测试（V3 Wave C-a / I5）

对应契约 docs/contracts/waveCa-ai-reports.md §五 验收 3：

- `grade.findings` 被原样放进提示词（断言提示词内容）
- 模型输出与规则判据矛盾时 → 标记为异常而非照单全收
- `based_on` 覆盖度出现在诊断结论里

**不连任何模型**：provider 是 `tests/copilot_fakes.py` 的脚本回放假货。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai_reports.contradiction import OVERALL_RULE, detect_contradictions
from app.ai_reports.diagnosis import DEFAULT_SEVERITY, ensure_coverage
from app.ai_reports.diagnosis_prompt import build_user_prompt
from app.api.v1.endpoints import ai_reports as endpoint
from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.core.llm import LLMNotConfiguredError, LLMUnavailableError
from app.core.redis import get_redis
from app.engine.backtest.validation_grade import grade_validation
from tests.ai_report_fakes import (
    fail_resolve,
    json_turn,
    junk_turn,
    prompt_text,
    use_provider,
)
from tests.copilot_fakes import FakeProvider
from tests.fake_redis import FakeRedis

# ── 真实评级：直接调 A-d 的规则模块，不手搓假 grade ──────────────────────────
#
# 手搓的 grade 会随规则演进而失真；这里跑一遍真实规则，
# 断言的就是「AI 诊断消费的确实是 validation_grade 的产物」。

SENSITIVE_STEPS: dict[str, Any] = {
    "backtest": {"metrics": {"sharpe_ratio": 1.4}},
    "optimize": {"score_dispersion": 0.9},
    "walkforward": {"avg_is_sharpe": 1.5, "avg_oos_sharpe": 1.2},
}

CLEAN_STEPS: dict[str, Any] = {
    "backtest": {"metrics": {"sharpe_ratio": 1.4}},
    "optimize": {"score_dispersion": 0.1},
    "walkforward": {"avg_is_sharpe": 1.5, "avg_oos_sharpe": 1.2},
    "bias": {"has_lookahead_bias": False, "has_recursive_bias": False},
    "robustness": {"p5_total_return_pct": 3.0},
}


def sensitive_grade() -> dict[str, Any]:
    """三步覆盖度 + 参数敏感性命中 —— 矛盾检测与覆盖度都用得上。"""
    return grade_validation(SENSITIVE_STEPS, requested=list(SENSITIVE_STEPS)).to_dict()


def clean_grade() -> dict[str, Any]:
    return grade_validation(CLEAN_STEPS, requested=list(CLEAN_STEPS)).to_dict()


GOOD_OUTPUT = {
    "summary": "参数有效区间狭窄，需要收敛参数空间后重跑。",
    "findings": [
        {
            "title": "参数敏感性过高",
            "detail": "最优参数得分显著高于中位数，换一组邻近参数可能大幅劣化。",
            "severity": "high",
            "rule": "param_sensitivity",
        }
    ],
    "next_steps": ["缩小参数空间后重跑寻优", "补跑偏差检测与蒙特卡洛稳健性"],
}

CONTRADICTING_OUTPUT = {
    "summary": "整体来看参数很稳健，可以放心使用。",
    "findings": [{"title": "无明显问题", "detail": "各项指标正常。", "severity": "low"}],
    "next_steps": ["无需额外动作"],
}


# ── 夹具 ─────────────────────────────────────────────────────────────────────

def _viewer() -> UserInfo:
    return UserInfo(id="v1", email="viewer@test.local", role="viewer")


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(endpoint.router, prefix="/api/v1/ai/reports", tags=["AI Reports"])
    test_app.dependency_overrides[get_redis] = lambda: FakeRedis()
    test_app.dependency_overrides[get_current_user] = _viewer
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _diagnose(
    client: AsyncClient, grade: dict[str, Any], **overrides: Any
) -> Any:
    payload = {"run_id": "run-abc", "grade": grade, **overrides}
    return await client.post("/api/v1/ai/reports/backtest", json=payload)


# ── 验收 3.1：findings 原样进提示词 ──────────────────────────────────────────

async def test_grade_findings_go_into_the_prompt_verbatim(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I5 的价值是翻译机器判据。判据被摘要过一遍，模型解读的就不是原文了。"""
    grade = sensitive_grade()
    provider = use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    response = await _diagnose(client, grade)

    assert response.status_code == 200, response.text
    sent = prompt_text(provider)
    assert grade["findings"], "本用例需要至少一条命中的判据"
    for finding in grade["findings"]:
        assert finding["rule"] in sent
        assert finding["detail"] in sent
        assert finding["metric"] in sent


def test_prompt_includes_not_evaluated_rules() -> None:
    """缺步的规则只能说「未检验」，所以 not_evaluated 也必须进提示词。"""
    grade = sensitive_grade()

    text = build_user_prompt(grade, {})

    assert grade["not_evaluated"], "本用例需要有未评估的规则"
    for item in grade["not_evaluated"]:
        assert item["rule"] in text
    assert "未检验" in text


def test_prompt_forbids_contradicting_the_rules_and_trade_advice() -> None:
    from app.ai_reports.diagnosis_prompt import SYSTEM_PROMPT

    assert "不得与之矛盾" in SYSTEM_PROMPT
    assert "严禁输出任何交易或实盘建议" in SYSTEM_PROMPT


async def test_step_payloads_are_forwarded_when_provided(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    await _diagnose(client, sensitive_grade(), steps={"optimize": {"score_dispersion": 0.9}})

    assert "score_dispersion" in prompt_text(provider)


# ── 验收 3.2：矛盾 → 标记为异常 ──────────────────────────────────────────────

async def test_output_contradicting_the_rules_is_flagged_not_accepted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规则写着「敏感性过高」而模型说「参数很稳健」—— 这是明确的 bug。"""
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(CONTRADICTING_OUTPUT)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["has_contradiction"] is True
    rules = {c["rule"] for c in body["contradictions"]}
    assert "param_sensitivity" in rules
    assert "请以规则判据为准" in body["contradictions"][0]["detail"]


async def test_consistent_output_is_not_flagged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["has_contradiction"] is False
    assert body["contradictions"] == []


async def test_all_clear_claim_is_a_contradiction_when_findings_exist(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = {**GOOD_OUTPUT, "summary": "本次验证未发现任何问题。"}
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(output)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["has_contradiction"] is True
    assert any(c["rule"] == OVERALL_RULE for c in body["contradictions"])


async def test_contradictions_are_detected_inside_findings_and_next_steps(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """矛盾可能藏在某一条 finding 里，只扫 summary 会漏。"""
    output = {
        "summary": "参数区间需要复核。",
        "findings": [{"title": "参数评估", "detail": "该策略对参数不敏感。", "severity": "low"}],
        "next_steps": ["补跑偏差检测"],
    }
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(output)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["has_contradiction"] is True


def test_no_findings_means_no_contradictions() -> None:
    """规则一条都没触发时，说「没问题」不是矛盾。"""
    assert detect_contradictions("未发现任何问题，参数稳健。", []) == ()


def test_negated_phrasing_is_not_a_contradiction() -> None:
    """「样本外表现稳健**性不足**」是同意规则，不是反驳它 —— 不能误报。"""
    findings = [{"rule": "oos_sharpe_decay", "detail": "样本外衰减明显"}]

    assert detect_contradictions("样本外表现稳健性不足，存在拟合迹象。", findings) == ()
    assert detect_contradictions("并非无过拟合，需谨慎。", findings) == ()
    assert detect_contradictions("样本外无衰减，泛化能力良好。", findings) != ()


def test_each_rule_is_reported_at_most_once() -> None:
    findings = [{"rule": "param_sensitivity", "detail": "敏感性过高"}]

    hits = detect_contradictions("参数稳健，参数不敏感，对参数不敏感。", findings)

    assert len([h for h in hits if h.rule == "param_sensitivity"]) == 1


# ── 验收 3.3：based_on 覆盖度出现在结论里 ────────────────────────────────────

async def test_coverage_is_present_in_the_summary_even_if_the_model_omits_it(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三步的结果不能给一个听起来覆盖五步的结论。"""
    grade = sensitive_grade()
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    body = (await _diagnose(client, grade)).json()

    assert grade["based_on"] == "基于 3/5 步"
    assert body["coverage"] == "基于 3/5 步"
    assert "基于 3/5 步" in body["summary"]
    assert body["is_complete"] is False


async def test_coverage_is_not_duplicated_when_the_model_already_said_it(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = {**GOOD_OUTPUT, "summary": "基于 3/5 步的结果看，参数区间偏窄。"}
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(output)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["summary"].count("基于 3/5 步") == 1


def test_ensure_coverage_is_a_noop_without_a_coverage_string() -> None:
    assert ensure_coverage("结论", "") == "结论"


async def test_complete_run_reports_full_coverage(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    body = (await _diagnose(client, clean_grade())).json()

    assert body["coverage"] == "基于 5/5 步"
    assert body["is_complete"] is True
    assert body["grade_level"] == "A"


# ── 归一化与错误分层 ─────────────────────────────────────────────────────────

async def test_findings_are_normalized(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型给的 severity 可能是「严重」这种非法值，也可能整条就是一个字符串。"""
    output = {
        "summary": "略。",
        "findings": [
            {"title": "A", "detail": "详情", "severity": "严重"},
            "参数区间过窄",
            {"title": "", "detail": ""},
            42,
        ],
        "next_steps": "缩小参数空间",
    }
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(output)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert [f["title"] for f in body["findings"]] == ["A", "参数区间过窄"]
    assert body["findings"][0]["severity"] == DEFAULT_SEVERITY
    assert body["next_steps"] == ["缩小参数空间"]


async def test_disclaimer_and_run_id_are_returned(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_provider(monkeypatch, endpoint, FakeProvider([json_turn(GOOD_OUTPUT)]))

    body = (await _diagnose(client, sensitive_grade())).json()

    assert body["run_id"] == "run-abc"
    assert "不构成任何投资建议" in body["disclaimer"]
    assert body["provider_id"] == "ollama"


async def test_missing_provider_returns_501_pointing_at_settings_models(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_resolve(monkeypatch, endpoint, LLMNotConfiguredError("尚未配置任何可用的模型服务。"))

    response = await _diagnose(client, sensitive_grade())

    assert response.status_code == 501
    assert "/settings/models" in response.json()["detail"]


async def test_unavailable_provider_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_resolve(monkeypatch, endpoint, LLMUnavailableError("Connection refused"))

    response = await _diagnose(client, sensitive_grade())

    assert response.status_code == 503


async def test_unparsable_output_returns_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = use_provider(monkeypatch, endpoint, FakeProvider([junk_turn()]))

    response = await _diagnose(client, sensitive_grade())

    assert response.status_code == 502
    assert provider.calls == 2
