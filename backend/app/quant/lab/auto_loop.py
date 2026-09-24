"""
自动因子研发循环 —— 纯计算内核（V3 · I2）

把已有的几块串成一轮可重复的自动研发::

    种子表达式 → evolve() 遗传搜索 → compute_factor_fitness() 适应度
              → 样本外验证 → LabStore 入库 → LLM 复盘 + 提出下一轮种子

本模块只管中间那一段**纯计算**：搜索 + 双样本打分。取数、入库、调模型都在
`loop_runner.py` —— 内核不碰 I/O，测试才能在几十毫秒内跑完一整轮。

三条不能含糊的（契约 §2）：

1. **样本外在搜索期物理不可见**。`search_fn` 的签名只收一个 `SamplePanels`，
   而本模块只把 `split.in_sample` 传进去。机制细节见 `sample_split.py`。
2. **`hypotheses_tested` 如实计数**。评估了两千个表达式再挑最好的五个，
   它们的 IC 一定好看 —— 这是多重检验下的必然，不是发现。把分母摆出来。
3. **只算不上线**。本模块不认识策略、不认识实盘，连 `promote_to_strategy`
   都没导入。晋级永远是人在界面上点的那一下。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.quant.lab.factor_eval import FactorScore, score_tokens
from app.quant.lab.sample_split import SamplePanels, SampleSplit
from app.quant.mining.expression_tree import ExpressionParseError, Node, parse_expr
from app.quant.mining.genetic import GAConfig, GAResult, evolve

logger = logging.getLogger(__name__)

#: 样本外 IC 掉到样本内的这个比例以下即标记 overfit_suspect（契约 §2.1）
OVERFIT_DECAY_THRESHOLD = 0.5

#: 未提供种子时使用的固定种子集 —— 都是教科书上的经典组合，
#: 作用是给初代种群一个不算离谱的起点，不是「推荐因子」。
DEFAULT_SEED_EXPRESSIONS: tuple[str, ...] = (
    "DIV(MOM20, ATR_RATIO)",
    "ZSCORE(MOM5)",
    "NEG(BB_POS)",
    "MUL(RSI14, VOL_CHG)",
    "RANK(OBV_MOM)",
    "SLOPE20(PX_SMA20)",
)

#: 和 `hypotheses_tested` 一起展示的一句话说明。前端如实照抄，不要自己编措辞。
MULTIPLE_TESTING_NOTE = (
    "本轮共评估了 {n} 个不同表达式，下面这几条是从中挑出的最好的几条。"
    "评估的表达式越多，最好的那几条指标越亮眼 —— 这是多重检验的必然结果，"
    "不是发现。请把样本外指标和这个数字放在一起看。"
    "（本期未做 deflated Sharpe 等正式校正，只如实给出分母。）"
)

#: 搜索函数的形状：给一段样本 + GA 超参 + 种子树，还回 GAResult。
#: 参数里**没有**样本外那一份 —— 这不是疏忽，是设计。
SearchFn = Callable[[SamplePanels, GAConfig, object, tuple[Node, ...]], GAResult]


@dataclass(frozen=True)
class LoopConfig:
    """一轮自动循环的配置。"""

    universe: tuple[str, ...]
    is_end: date
    generations: int = 5
    population: int = 40
    top_k: int = 5
    max_candidates_evaluated: int = 2000
    forward_period: int = 5
    max_depth: int = 4
    seed: int = 42
    use_cross_section: bool = False

    def to_ga_config(self) -> GAConfig:
        return GAConfig(
            population_size=self.population,
            generations=self.generations,
            max_depth=self.max_depth,
            top_k=self.top_k,
            seed=self.seed,
            use_cross_section=self.use_cross_section,
            max_evaluations=self.max_candidates_evaluated,
        )


@dataclass(frozen=True)
class FactorCandidate:
    """一条存活因子：样本内 / 样本外两套指标**并排**存放，不合并、不取平均。"""

    expr: str
    tokens: tuple[str, ...]
    in_sample: FactorScore
    out_of_sample: FactorScore | None
    #: 样本外 IC ÷ 样本内 IC（有符号）。None = 无法计算（缺样本外或样本内 IC 为 0）
    ic_decay_ratio: float | None
    #: 衰减超过阈值。⚠️ 为 True 也照样入库 —— 让人看到衰减幅度，
    #: 比替他把结果丢掉有价值得多。
    overfit_suspect: bool

    @property
    def out_of_sample_evaluated(self) -> bool:
        return self.out_of_sample is not None

    def to_dict(self) -> dict:
        data: dict = {
            "expr": self.expr,
            "tokens": list(self.tokens),
            "ic_decay_ratio": None if self.ic_decay_ratio is None else round(self.ic_decay_ratio, 6),
            "overfit_suspect": self.overfit_suspect,
            "out_of_sample_evaluated": self.out_of_sample_evaluated,
        }
        data.update(self.in_sample.to_dict(prefix="is_"))
        if self.out_of_sample is not None:
            data.update(self.out_of_sample.to_dict(prefix="oos_"))
        return data


@dataclass(frozen=True)
class LoopRound:
    """一轮的全部产出。"""

    round_id: str
    seeds: tuple[str, ...]
    survivors: tuple[FactorCandidate, ...]
    #: 本轮实际评估过的**不同表达式**数（含全部被淘汰的）。多重检验的分母。
    hypotheses_tested: int
    llm_review: str | None = None
    #: LLM 为下一轮提出的种子（已过解析与白名单）。本期不自动带入下一轮。
    next_seeds: tuple[str, ...] = ()
    #: LLM 提出但被解析/白名单拒绝的表达式条数
    rejected_seed_count: int = 0
    #: 是否因 `max_candidates_evaluated` 达上限而提前停止
    truncated: bool = False
    out_of_sample_available: bool = False
    is_end: str = ""
    embargo_bars: int = 0
    universe: tuple[str, ...] = ()
    invalid_seed_count: int = 0
    #: 入库产物 ID（`LabStore` 的 DATASET）。None = 没有存活因子，或入库失败
    artifact_id: str | None = None
    #: 入库失败原因。不为 None 时前端必须提示 —— 一轮「成功」却在产物库里
    #: 找不到东西，是最容易让人白等一天的那种失败。
    artifact_error: str | None = None
    llm_error: str | None = None

    @property
    def multiple_testing_note(self) -> str:
        return MULTIPLE_TESTING_NOTE.format(n=self.hypotheses_tested)

    def to_dict(self) -> dict:
        return {
            "round_id": self.round_id,
            "seeds": list(self.seeds),
            "survivors": [c.to_dict() for c in self.survivors],
            "hypotheses_tested": self.hypotheses_tested,
            "multiple_testing_note": self.multiple_testing_note,
            "llm_review": self.llm_review,
            "llm_error": self.llm_error,
            "next_seeds": list(self.next_seeds),
            "rejected_seed_count": self.rejected_seed_count,
            "invalid_seed_count": self.invalid_seed_count,
            "truncated": self.truncated,
            "out_of_sample_available": self.out_of_sample_available,
            "is_end": self.is_end,
            "embargo_bars": self.embargo_bars,
            "universe": list(self.universe),
            "artifact_id": self.artifact_id,
            "artifact_error": self.artifact_error,
        }


def new_round_id() -> str:
    return uuid.uuid4().hex[:12]


def parse_seed_expressions(
    seeds: tuple[str, ...] | list[str], include_cross_section: bool = False
) -> tuple[tuple[Node, ...], tuple[str, ...], int]:
    """
    把种子字符串解析成表达式树，返回 `(树, 被接受的表达式, 被拒绝的条数)`。

    ⚠️ 这里是外部字符串进入执行链路的唯一闸口。非法的直接丢弃并计数 ——
    不抛异常（一条坏种子不该让整轮循环失败），也绝不 `eval`。
    """
    trees: list[Node] = []
    accepted: list[str] = []
    rejected = 0
    for raw in seeds:
        try:
            node = parse_expr(raw, include_cross_section=include_cross_section)
        except ExpressionParseError as exc:
            logger.info("种子表达式被拒绝：%r（%s）", raw, exc)
            rejected += 1
            continue
        trees.append(node)
        accepted.append(raw.strip())
    return tuple(trees), tuple(accepted), rejected


def default_search(
    panels: SamplePanels,
    ga_config: GAConfig,
    fitness_config,
    seed_trees: tuple[Node, ...],
) -> GAResult:
    """默认搜索器：把样本内那一份喂给已有的 `evolve()`。"""
    return evolve(
        panels.ohlcv_by_symbol,
        panels.forward_return_panel,
        panels.liquidity_panel,
        fitness_config,
        ga_config,
        seed_trees=seed_trees,
    )


def run_search_round(
    split: SampleSplit,
    config: LoopConfig,
    fitness_config,
    seeds: tuple[str, ...] = DEFAULT_SEED_EXPRESSIONS,
    round_id: str | None = None,
    search_fn: SearchFn = default_search,
) -> LoopRound:
    """
    跑一轮搜索 + 双样本打分（纯计算，无 I/O，无 LLM）。

    `search_fn` 只拿得到 `split.in_sample` —— 样本外那份在这里不会被传出去，
    见模块 docstring 第 1 条。
    """
    seed_trees, accepted_seeds, invalid_seeds = parse_seed_expressions(
        seeds, include_cross_section=config.use_cross_section
    )
    ga_result = search_fn(
        split.in_sample, config.to_ga_config(), fitness_config, seed_trees
    )

    survivors = _score_survivors(ga_result, split, config, fitness_config)
    return LoopRound(
        round_id=round_id or new_round_id(),
        seeds=accepted_seeds,
        survivors=survivors,
        hypotheses_tested=ga_result.n_evaluated,
        truncated=ga_result.truncated,
        out_of_sample_available=split.has_out_of_sample,
        is_end=split.is_end.isoformat(),
        embargo_bars=split.embargo_bars,
        universe=config.universe,
        invalid_seed_count=invalid_seeds,
    )


def _score_survivors(
    ga_result: GAResult,
    split: SampleSplit,
    config: LoopConfig,
    fitness_config,
) -> tuple[FactorCandidate, ...]:
    """对 GA 交出的候选逐条重算样本内 / 样本外指标。

    刻意**不复用** GA 内部算出的指标：GA 的因子面板覆盖样本内全部日期（含末尾
    没有标签的禁运段），而样本外打分是裁到有标签日期上的。两个数字出自不同口径时，
    「衰减了多少」就成了一句没有意义的话。
    """
    out: list[FactorCandidate] = []
    for candidate in ga_result.candidates[: config.top_k]:
        tokens = tuple(candidate.tokens)
        in_score = score_tokens(tokens, split.in_sample, fitness_config)
        if in_score is None:
            continue  # 样本内都算不出来的公式没有讨论价值
        oos_score = (
            score_tokens(tokens, split.out_of_sample, fitness_config)
            if split.out_of_sample is not None
            else None
        )
        ratio = _decay_ratio(in_score, oos_score)
        out.append(
            FactorCandidate(
                expr=candidate.expr,
                tokens=tokens,
                in_sample=in_score,
                out_of_sample=oos_score,
                ic_decay_ratio=ratio,
                overfit_suspect=ratio is not None and ratio < OVERFIT_DECAY_THRESHOLD,
            )
        )
    return tuple(out)


def _decay_ratio(in_score: FactorScore, oos_score: FactorScore | None) -> float | None:
    """样本外 IC ÷ 样本内 IC（有符号）。符号翻转会得到负比值，同样触发 suspect。"""
    if oos_score is None or in_score.ic_mean is None or oos_score.ic_mean is None:
        return None
    if abs(in_score.ic_mean) < 1e-12:
        return None
    return float(oos_score.ic_mean / in_score.ic_mean)


__all__ = [
    "DEFAULT_SEED_EXPRESSIONS",
    "MULTIPLE_TESTING_NOTE",
    "OVERFIT_DECAY_THRESHOLD",
    "FactorCandidate",
    "LoopConfig",
    "LoopRound",
    "default_search",
    "new_round_id",
    "parse_seed_expressions",
    "run_search_round",
]
