"""自动因子研发循环 —— 搜索内核测试（V3 · I2 契约 §6.2）

覆盖五条验收：

1. `is_end` 之后的数据在搜索期**不可见**（断言搜索器拿到的面板末日期）
2. 样本外 IC 掉到样本内一半以下 → `overfit_suspect=True` 且**仍然入库**
3. `hypotheses_tested` == 实际评估过的候选数（含被淘汰的）
4. 达到 `max_candidates_evaluated` → `truncated=True` 且提前停止
5. 循环不注册策略（`promote_to_strategy` 零调用）
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_fitness import FitnessConfig
from app.quant.lab.auto_loop import (
    OVERFIT_DECAY_THRESHOLD,
    LoopConfig,
    default_search,
    parse_seed_expressions,
    run_search_round,
)
from app.quant.lab.factor_eval import score_tokens
from app.quant.lab.sample_split import (
    SamplePanels,
    SampleSplitError,
    split_by_is_end,
)
from app.quant.mining.expression_tree import ExpressionParseError, parse_expr
from app.quant.mining.genetic import Candidate, GAConfig, GAResult, evolve

N_BARS = 260
IS_END = date(2020, 6, 30)


# ── 合成数据 ──────────────────────────────────────────────────────

def _ohlcv(seed: int, n: int = N_BARS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = (
        pd.date_range("2020-01-01", periods=n, freq="D")
        .strftime("%Y-%m-%dT00:00:00")
        .tolist()
    )
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.02, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": (np.arange(n) + 1_000_000).astype(float),
        },
        index=idx,
    )


def _universe(symbols: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD")):
    ohlcv = {sym: _ohlcv(i) for i, sym in enumerate(symbols)}
    records = []
    for sym, frame in ohlcv.items():
        fwd = frame["close"].pct_change(1).shift(-1)
        for ts in frame.index:
            records.append((ts, sym, float(fwd.loc[ts]) if not np.isnan(fwd.loc[ts]) else 0.0))
    panel = (
        pd.DataFrame(records, columns=["datetime", "instrument", "forward_return"])
        .set_index(["datetime", "instrument"])
        .sort_index()
    )
    liq = panel.copy()
    liq.columns = ["liquidity"]
    liq["liquidity"] = 5_000_000.0
    return ohlcv, panel, liq


def _split(forward_period: int = 1, is_end: date = IS_END):
    ohlcv, fwd, liq = _universe()
    return split_by_is_end(ohlcv, fwd, liq, is_end, forward_period)


def _config(**overrides) -> LoopConfig:
    base = {
        "universe": ("AAA", "BBB", "CCC", "DDD"),
        "is_end": IS_END,
        "generations": 2,
        "population": 6,
        "top_k": 3,
        "max_candidates_evaluated": 2000,
        "forward_period": 1,
        "seed": 7,
    }
    base.update(overrides)
    return LoopConfig(**base)


# ── 1. 样本外在搜索期不可见 ───────────────────────────────────────

class TestOutOfSampleInvisibility:
    def test_search_never_sees_bars_after_is_end(self):
        """搜索器拿到的 OHLCV 与收益面板末日期都不得越过 is_end。"""
        split = _split(forward_period=5)
        seen: list[SamplePanels] = []

        def spy_search(panels, ga_config, fitness_config, seed_trees):
            seen.append(panels)
            return GAResult(best=None, candidates=[], history=[], n_evaluated=0, n_unique=0)

        run_search_round(
            split, _config(forward_period=5), FitnessConfig(), search_fn=spy_search
        )

        assert len(seen) == 1
        panels = seen[0]
        assert panels.feature_end is not None
        assert panels.feature_end <= IS_END, "搜索器看到了 is_end 之后的 OHLCV"
        assert panels.label_end is not None
        assert panels.label_end < IS_END, "样本内标签必须再退禁运期，否则会偷看未来价格"

    def test_embargo_drops_exactly_forward_period_bars(self):
        """禁运期长度 == forward_period：标签 t 用到 close[t+p]，这几根必须丢。"""
        forward_period = 5
        split = _split(forward_period=forward_period)

        in_dates = sorted(
            set(split.in_sample.forward_return_panel.index.get_level_values("datetime"))
        )
        feature_dates = sorted(set(split.in_sample.ohlcv_by_symbol["AAA"].index))

        assert len(feature_dates) - len(in_dates) == forward_period
        assert split.embargo_bars == forward_period

    def test_out_of_sample_panels_are_strictly_after_is_end(self):
        split = _split(forward_period=1)
        assert split.has_out_of_sample
        oos_dates = pd.to_datetime(
            pd.Index(
                split.out_of_sample.forward_return_panel.index.get_level_values("datetime")
            )
        ).date
        assert all(d > IS_END for d in oos_dates)

    def test_out_of_sample_keeps_full_history_for_warmup(self):
        """样本外保留全量 OHLCV —— 砍掉热身段会让滚动算子前 60 根全是 NaN。"""
        split = _split(forward_period=1)
        assert split.out_of_sample.feature_end > IS_END
        assert len(split.out_of_sample.ohlcv_by_symbol["AAA"]) == N_BARS

    def test_scoring_is_restricted_to_labelled_dates(self):
        """样本外打分只落在样本外日期上，热身段不进分母。"""
        split = _split(forward_period=1)
        tokens = ("MOM20", "ATR_RATIO", "DIV")
        oos = score_tokens(tokens, split.out_of_sample, FitnessConfig())
        assert oos is not None
        n_oos_dates = split.out_of_sample.forward_return_panel.index.get_level_values(
            "datetime"
        ).nunique()
        assert oos.n_dates == n_oos_dates

    def test_empty_in_sample_is_an_error_not_a_silent_pass(self):
        ohlcv, fwd, liq = _universe()
        with pytest.raises(SampleSplitError):
            split_by_is_end(ohlcv, fwd, liq, date(2019, 1, 1), 5)

    def test_no_out_of_sample_is_reported_not_faked(self):
        """is_end 覆盖全部数据时，样本外为 None 而不是伪造出来的一段。"""
        ohlcv, fwd, liq = _universe()
        split = split_by_is_end(ohlcv, fwd, liq, date(2099, 1, 1), 1)
        assert split.out_of_sample is None
        assert not split.has_out_of_sample


# ── 2. 过拟合嫌疑标注 —— 标了也照样入库 ──────────────────────────

class TestOverfitSuspect:
    @staticmethod
    def _round_with_scores(is_ic: float, oos_ic: float | None):
        """用打桩的打分函数造一条候选，直接检验判定与保留逻辑。"""
        from app.quant.lab import auto_loop as module
        from app.quant.lab.factor_eval import FactorScore

        split = _split(forward_period=1)
        tokens = ("MOM20", "ATR_RATIO", "DIV")
        ga = GAResult(
            best=None,
            candidates=[
                Candidate(
                    tokens=tokens, expr="DIV(MOM20, ATR_RATIO)", fitness=1.0,
                    ic_mean=is_ic, rank_ic_mean=is_ic, icir=1.0,
                    mean_net_return=0.1, turnover=1.0,
                )
            ],
            history=[],
            n_evaluated=1234,
            n_unique=1234,
        )

        def score(ic: float | None):
            if ic is None:
                return None
            return FactorScore(
                fitness=1.0, ic_mean=ic, rank_ic_mean=ic, icir=1.0,
                mean_net_return=0.1, turnover=1.0, n_dates=50,
            )

        calls = {"n": 0}

        def fake_score(_tokens, panels, _cfg):
            calls["n"] += 1
            return score(is_ic) if calls["n"] == 1 else score(oos_ic)

        original = module.score_tokens
        module.score_tokens = fake_score
        try:
            return run_search_round(
                split,
                _config(),
                FitnessConfig(),
                search_fn=lambda *_: ga,
            )
        finally:
            module.score_tokens = original

    def test_halved_out_of_sample_ic_flags_suspect_and_still_stores(self):
        round_ = self._round_with_scores(is_ic=0.10, oos_ic=0.02)

        assert len(round_.survivors) == 1, "过拟合嫌疑不是丢弃的理由"
        candidate = round_.survivors[0]
        assert candidate.overfit_suspect is True
        assert candidate.ic_decay_ratio == pytest.approx(0.2)
        # 两套指标必须并排存在，不能被合并成一个数
        assert candidate.in_sample.ic_mean == pytest.approx(0.10)
        assert candidate.out_of_sample.ic_mean == pytest.approx(0.02)

    def test_healthy_decay_is_not_flagged(self):
        round_ = self._round_with_scores(is_ic=0.10, oos_ic=0.09)
        assert round_.survivors[0].overfit_suspect is False
        assert round_.survivors[0].ic_decay_ratio > OVERFIT_DECAY_THRESHOLD

    def test_sign_flip_counts_as_decay(self):
        """样本外 IC 翻号比衰减更糟，同样要标出来。"""
        round_ = self._round_with_scores(is_ic=0.10, oos_ic=-0.10)
        assert round_.survivors[0].overfit_suspect is True

    def test_missing_out_of_sample_is_not_silently_called_healthy(self):
        round_ = self._round_with_scores(is_ic=0.10, oos_ic=None)
        candidate = round_.survivors[0]
        assert candidate.out_of_sample_evaluated is False
        assert candidate.ic_decay_ratio is None
        assert candidate.to_dict()["out_of_sample_evaluated"] is False


# ── 3. hypotheses_tested 如实计数 ─────────────────────────────────

class TestHypothesesTested:
    def test_counts_every_distinct_candidate_including_the_discarded(self):
        split = _split(forward_period=1)
        config = _config(generations=3, population=8, top_k=3)

        captured: dict = {}

        def counting_search(panels, ga_config, fitness_config, seed_trees):
            result = default_search(panels, ga_config, fitness_config, seed_trees)
            captured["result"] = result
            return result

        round_ = run_search_round(
            split, config, FitnessConfig(), search_fn=counting_search
        )

        ga = captured["result"]
        assert round_.hypotheses_tested == ga.n_evaluated
        # 分母必须大于交出去的存活因子数，否则「多重检验」这句提示就是假的
        assert round_.hypotheses_tested > len(round_.survivors)

    def test_note_carries_the_denominator_and_the_caveat(self):
        split = _split(forward_period=1)
        round_ = run_search_round(
            split,
            _config(),
            FitnessConfig(),
            search_fn=lambda *_: GAResult(
                best=None, candidates=[], history=[], n_evaluated=777, n_unique=777
            ),
        )
        note = round_.to_dict()["multiple_testing_note"]
        assert "777" in note
        assert "多重检验" in note
        assert round_.to_dict()["hypotheses_tested"] == 777


# ── 4. 资源上限：truncated 且提前停止 ────────────────────────────

class TestCandidateBudget:
    def test_budget_stops_early_and_flags_truncated(self):
        split = _split(forward_period=1)
        budget = 12
        config = _config(generations=20, population=10, max_candidates_evaluated=budget)

        round_ = run_search_round(split, config, FitnessConfig())

        assert round_.truncated is True
        assert round_.hypotheses_tested <= budget

    def test_unbounded_run_is_not_flagged(self):
        split = _split(forward_period=1)
        config = _config(generations=2, population=6, max_candidates_evaluated=5000)
        round_ = run_search_round(split, config, FitnessConfig())
        assert round_.truncated is False

    def test_evolve_without_budget_is_byte_for_byte_unchanged(self):
        """默认不设预算时，行为必须与历史完全一致（实验可复现是硬约束）。"""
        ohlcv, fwd, _ = _universe()
        cfg = GAConfig(population_size=6, generations=2, top_k=3, seed=11)
        a = evolve(ohlcv, fwd, None, FitnessConfig(), cfg)
        b = evolve(ohlcv, fwd, None, FitnessConfig(), cfg, seed_trees=())
        assert a.truncated is False
        assert [c.tokens for c in a.candidates] == [c.tokens for c in b.candidates]

    def test_seeds_enter_the_initial_population(self):
        ohlcv, fwd, _ = _universe()
        seed_tree = parse_expr("DIV(MOM20, ATR_RATIO)")
        cfg = GAConfig(population_size=6, generations=1, top_k=30, seed=11)
        result = evolve(ohlcv, fwd, None, FitnessConfig(), cfg, seed_trees=(seed_tree,))
        assert ("MOM20", "ATR_RATIO", "DIV") in {c.tokens for c in result.candidates}


# ── 5. 循环只入库，绝不上线 ───────────────────────────────────────

class TestNeverPromotes:
    async def test_round_does_not_register_any_strategy(self, monkeypatch):
        import app.quant.experiments.recorder as recorder

        promote = AsyncMock(return_value="never")
        monkeypatch.setattr(recorder, "promote_to_strategy", promote)

        split = _split(forward_period=1)
        run_search_round(split, _config(), FitnessConfig())

        assert promote.await_count == 0
        assert promote.call_count == 0

    def test_loop_modules_do_not_reference_the_promotion_path(self):
        """不是「没调用」而是「够不着」：内核的 AST 里根本没有策略晋级的名字。

        用 AST 而非字符串查找 —— 模块 docstring 里明写着「不认识
        promote_to_strategy」，纯文本匹配会把这句注释也当成引用。
        """
        import ast
        from pathlib import Path

        forbidden = {"promote_to_strategy", "save_factor_strategy"}
        for name in ("auto_loop.py", "loop_runner.py", "loop_llm.py"):
            tree = ast.parse(Path("app/quant/lab", name).read_text(encoding="utf-8"))
            names = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            assert not (names & forbidden), f"{name} 引用了策略晋级路径"


# ── 种子解析：非法输入丢弃并计数，绝不 eval ──────────────────────

class TestSeedParsing:
    def test_rejects_anything_outside_the_operator_whitelist(self):
        trees, accepted, rejected = parse_seed_expressions(
            [
                "DIV(MOM20, ATR_RATIO)",       # 合法
                "__import__('os').system('x')",  # 危险
                "FOO(MOM20)",                   # 未知算子
                "DIV(MOM20)",                   # 元数不符
                "open('/etc/passwd')",          # 危险
            ]
        )
        assert len(trees) == 1
        assert accepted == ("DIV(MOM20, ATR_RATIO)",)
        assert rejected == 4

    def test_cross_section_ops_need_the_flag(self):
        with pytest.raises(ExpressionParseError):
            parse_expr("CS_RANK(MOM20)")
        assert parse_expr("CS_RANK(MOM20)", include_cross_section=True).value == "CS_RANK"

    def test_oversized_expression_is_rejected(self):
        nested = "NEG(" * 30 + "MOM20" + ")" * 30
        with pytest.raises(ExpressionParseError):
            parse_expr(nested)
