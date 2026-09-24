"""自动因子循环的 LLM 复盘测试（V3 · I2 契约 §6.3）

覆盖三条验收：

1. LLM 返回非法表达式 → 丢弃并计数，不抛异常
2. LLM 输出的字符串**不经 eval** —— 断言解析确实走 `expression_tree.parse_expr`
3. 未配置 provider → 循环正常完成，`llm_review is None`

全部 mock LLM，一次真实网络请求都不发。
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from app.core.llm.base import ChatResponse, LLMUnavailableError
from app.quant.lab import loop_llm
from app.quant.lab.auto_loop import FactorCandidate, LoopRound
from app.quant.lab.factor_eval import FactorScore
from app.quant.lab.loop_llm import (
    LlmOutcome,
    build_prompt,
    interpret_reply,
    review_round,
)
from tests.copilot_fakes import FakeProvider, text_turn


def _score(ic: float) -> FactorScore:
    return FactorScore(
        fitness=1.0, ic_mean=ic, rank_ic_mean=ic, icir=1.0,
        mean_net_return=0.05, turnover=2.0, n_dates=40,
    )


def _round(**overrides) -> LoopRound:
    candidate = FactorCandidate(
        expr="DIV(MOM20, ATR_RATIO)",
        tokens=("MOM20", "ATR_RATIO", "DIV"),
        in_sample=_score(0.08),
        out_of_sample=_score(0.02),
        ic_decay_ratio=0.25,
        overfit_suspect=True,
    )
    base = {
        "round_id": "r-1",
        "seeds": ("MOM5",),
        "survivors": (candidate,),
        "hypotheses_tested": 1500,
        "out_of_sample_available": True,
        "is_end": date(2020, 6, 30).isoformat(),
        "embargo_bars": 5,
        "universe": ("AAA", "BBB", "CCC"),
    }
    base.update(overrides)
    return LoopRound(**base)


# ── 1. 非法表达式：丢弃 + 计数，不抛 ─────────────────────────────

class TestIllegalExpressions:
    async def test_illegal_seeds_are_discarded_and_counted(self):
        reply = (
            '{"review": "两条因子都在吃动量，结构高度相似。",'
            ' "seeds": ["DIV(MOM20, ATR_RATIO)", "FOO(BAR)",'
            ' "__import__(\'os\').system(\'rm -rf /\')", "DIV(MOM20)"]}'
        )
        provider = FakeProvider([text_turn(reply)])

        outcome = await review_round(provider, _round())

        assert outcome.next_seeds == ("DIV(MOM20, ATR_RATIO)",)
        assert outcome.rejected_seed_count == 3
        assert outcome.review == "两条因子都在吃动量，结构高度相似。"
        assert outcome.error is None

    async def test_every_seed_illegal_is_still_a_clean_completion(self):
        reply = '{"review": "没什么可说的", "seeds": ["exec(1)", "os.system(1)"]}'
        provider = FakeProvider([text_turn(reply)])

        outcome = await review_round(provider, _round())

        assert outcome.next_seeds == ()
        assert outcome.rejected_seed_count == 2
        assert outcome.review == "没什么可说的"

    async def test_garbage_reply_degrades_to_plain_review(self):
        """小模型经常不吐 JSON。整段当复盘文字，逐行捞表达式，不抛异常。"""
        provider = FakeProvider([text_turn("这几条因子都在捕捉短期动量。\nZSCORE(MOM5)\n随便写的一行")])

        outcome = await review_round(provider, _round())

        assert outcome.review is not None
        assert "短期动量" in outcome.review
        assert "ZSCORE(MOM5)" in outcome.next_seeds

    async def test_provider_failure_is_reported_not_raised(self):
        class BrokenProvider(FakeProvider):
            async def chat(self, *args, **kwargs):
                raise LLMUnavailableError("connection refused")

        outcome = await review_round(BrokenProvider([]), _round())

        assert outcome.review is None
        assert outcome.next_seeds == ()
        assert "connection refused" in (outcome.error or "")

    async def test_empty_reply_is_not_mistaken_for_a_review(self):
        provider = FakeProvider([ChatResponse(content="   ", model="fake")])
        outcome = await review_round(provider, _round())
        assert outcome.review is None
        assert outcome.error is not None


# ── 2. 绝不 eval：解析必须走 expression_tree ─────────────────────

class TestNoEval:
    async def test_parse_expr_is_the_only_gate_for_model_output(self, monkeypatch):
        """每一条模型给的表达式都必须经过 `parse_expr` —— 用间谍断言，不靠信任。"""
        from app.quant.mining import expression_tree

        seen: list[str] = []
        original = expression_tree.parse_expr

        def spy(text, include_cross_section=False):
            seen.append(text)
            return original(text, include_cross_section=include_cross_section)

        monkeypatch.setattr("app.quant.lab.auto_loop.parse_expr", spy)

        reply = '{"review": "ok", "seeds": ["ZSCORE(MOM5)", "NOT_A_FEATURE"]}'
        outcome = await review_round(FakeProvider([text_turn(reply)]), _round())

        assert seen == ["ZSCORE(MOM5)", "NOT_A_FEATURE"]
        assert outcome.next_seeds == ("ZSCORE(MOM5)",)
        assert outcome.rejected_seed_count == 1

    def test_llm_modules_contain_no_dynamic_execution(self):
        """AST 级断言：复盘/内核模块里没有 eval / exec / compile / __import__。"""
        forbidden = {"eval", "exec", "compile", "__import__", "literal_eval"}
        for name in ("loop_llm.py", "auto_loop.py", "loop_runner.py"):
            tree = ast.parse(Path("app/quant/lab", name).read_text(encoding="utf-8"))
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert not (called & forbidden), f"{name} 里出现了动态执行"

    def test_expression_parser_rejects_python_syntax_outright(self):
        """解析器只认 `名字(参数)`：引号、数字、点号、分号一律非法字符。"""
        from app.quant.mining.expression_tree import ExpressionParseError, parse_expr

        for payload in (
            "__import__('os').system('id')",
            "().__class__.__bases__",
            "MOM20; import os",
            "lambda: 1",
            "MOM20.__class__",
        ):
            with pytest.raises(ExpressionParseError):
                parse_expr(payload)

    def test_interpret_reply_never_touches_unparsed_strings(self):
        outcome = interpret_reply('{"review": "x", "seeds": ["os.system(\'id\')"]}')
        assert outcome.next_seeds == ()
        assert outcome.rejected_seed_count == 1


# ── 3. 未配置 provider：循环照跑，llm_review is None ─────────────

class TestProviderAbsent:
    async def test_round_completes_without_any_provider(self, monkeypatch):
        from app.core.llm.base import LLMNotConfiguredError
        from app.quant.lab import loop_runner

        async def not_configured(_redis, **_kwargs):
            raise LLMNotConfiguredError("尚未配置任何可用的模型服务")

        monkeypatch.setattr("app.core.llm.service.resolve_active", not_configured)

        round_ = _round()
        result = await loop_runner._attach_review(None, round_, _loop_config())

        assert result.llm_review is None
        assert result.next_seeds == ()
        assert result.llm_error is None      # 「没配」不是错误，别在界面上报红
        assert result.survivors == round_.survivors
        assert result.hypotheses_tested == 1500

    async def test_provider_resolution_failure_is_surfaced(self, monkeypatch):
        from app.quant.lab import loop_runner

        async def boom(_redis, **_kwargs):
            raise RuntimeError("redis down")

        monkeypatch.setattr("app.core.llm.service.resolve_active", boom)

        result = await loop_runner._attach_review(None, _round(), _loop_config())

        assert result.llm_review is None
        assert "redis down" in (result.llm_error or "")


# ── 提示词：分母也要给模型看 ─────────────────────────────────────

class TestPrompt:
    def test_prompt_carries_the_multiple_testing_denominator(self):
        text = build_prompt(_round())
        assert "1500" in text
        assert "2020-06-30" in text
        assert "DIV(MOM20, ATR_RATIO)" in text
        assert "衰减" in text

    def test_prompt_survives_an_empty_round(self):
        text = build_prompt(_round(survivors=()))
        assert "没有任何因子" in text

    def test_system_prompt_forbids_scoring(self):
        assert "不要给因子打分" in loop_llm.SYSTEM_PROMPT

    def test_review_is_clipped(self):
        outcome = interpret_reply("啊" * (loop_llm.MAX_REVIEW_CHARS + 200))
        assert isinstance(outcome, LlmOutcome)
        assert len(outcome.review) <= loop_llm.MAX_REVIEW_CHARS + 20


def _loop_config():
    from app.quant.lab.auto_loop import LoopConfig

    return LoopConfig(universe=("AAA", "BBB", "CCC"), is_end=date(2020, 6, 30))
