"""
遗传/进化因子挖掘引擎（B5）

移植 AlphaGPT `model_core/engine.py` 的**进化循环思想**（种群 → 评分 → 选择 → 变异
迭代出高分公式），但用遗传算法（GA）替代其 Transformer + REINFORCE 采样，从而完全
不依赖 PyTorch：

  AlphaGPT                     本模块（GA）
  ────────────────────────    ────────────────────────
  Transformer 采样 token 串     随机初始化表达式树种群
  StackVM 批量执行             formula_factor.evaluate_formula 逐标的执行
  MemeBacktest 适应度          factor_fitness.compute_factor_fitness（成本感知）
  REINFORCE 梯度更新参数        锦标赛选择 + 子树交叉 + 点变异

个体基因型 = expression_tree.Node（序列化为 RPN token）。适应度 = 成本感知净收益标量。
相同 seed → 相同结果（确定性、可复现，服务于 B7 实验记录）。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.formula_factor import (
    FormulaError,
    formula_requires_panel,
)
from app.quant.mining.expression_tree import (
    Node,
    clamp_size,
    crossover,
    mutate,
    random_tree,
    to_rpn,
)
from app.quant.mining.panel_eval import (
    build_factor_panel,
    cross_sectional_ic_stats,
    ohlcv_to_panel,
)

logger = logging.getLogger(__name__)

# 汇总日志里最多列出几种失败原因（同一算子坏掉时原因高度重复，列几条就够定位）
_MAX_REPORTED_ERROR_REASONS = 5


class BudgetExhaustedError(Exception):
    """评估预算（`GAConfig.max_evaluations`）用尽 —— 由 `evolve` 捕获后提前收尾。

    不是错误：这是「资源上限到了，如实标注 truncated 并交出目前最好的东西」，
    而不是「搜索失败」。用异常是因为预算可能在任意一次 `evaluate()` 上耗尽，
    逐层返回哨兵值会把整条评分链路弄脏。
    """


@dataclass(frozen=True)
class GAConfig:
    population_size: int = 24
    generations: int = 12
    tournament_size: int = 3
    crossover_rate: float = 0.7
    mutation_rate: float = 0.3
    elite_count: int = 2
    max_depth: int = 4
    top_k: int = 10
    seed: int = 42
    #: 是否把 CS_* 截面算子并入搜索空间（M2）。开启后含 CS_* 的个体走 panel 求值路径。
    #: 默认关闭：搜索空间变化会改变「相同 seed → 相同结果」的映射，
    #: 既有实验记录必须保持可复现。
    use_cross_section: bool = False
    #: 不同公式的评估次数硬上限（V3 I2）。None = 不设限，与历史行为完全一致。
    #: 达到上限时进化提前停止，`GAResult.truncated` 置 True。
    max_evaluations: int | None = None


@dataclass(frozen=True)
class Candidate:
    tokens: tuple[str, ...]
    expr: str
    fitness: float
    ic_mean: float
    rank_ic_mean: float
    icir: float
    mean_net_return: float
    turnover: float

    def to_dict(self) -> dict:
        return {
            "tokens": list(self.tokens),
            "expr": self.expr,
            "fitness": _safe(self.fitness),
            "ic_mean": _safe(self.ic_mean),
            "rank_ic_mean": _safe(self.rank_ic_mean),
            "icir": _safe(self.icir),
            "mean_net_return": _safe(self.mean_net_return),
            "turnover": _safe(self.turnover),
        }


@dataclass(frozen=True)
class GenerationStat:
    generation: int
    best_fitness: float
    mean_fitness: float
    best_expr: str

    def to_dict(self) -> dict:
        return {
            "generation": self.generation,
            "best_fitness": _safe(self.best_fitness),
            "mean_fitness": _safe(self.mean_fitness),
            "best_expr": self.best_expr,
        }


@dataclass(frozen=True)
class GAResult:
    best: Candidate | None
    candidates: list[Candidate]
    history: list[GenerationStat]
    n_evaluated: int
    n_unique: int
    #: 是否因评估预算耗尽而提前停止（V3 I2）。False = 跑满了配置的代数。
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "best": self.best.to_dict() if self.best else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "history": [h.to_dict() for h in self.history],
            "n_evaluated": self.n_evaluated,
            "n_unique": self.n_unique,
            "truncated": self.truncated,
        }


# ── 评分 ──────────────────────────────────────────────────────────

class _Evaluator:
    """封装评分所需的共享面板；带缓存避免重复评估同一公式。"""

    def __init__(
        self,
        ohlcv_by_symbol: dict[str, pd.DataFrame],
        forward_return_panel: pd.DataFrame,
        liquidity_panel: pd.DataFrame | None,
        fitness_config,
        max_evaluations: int | None = None,
    ) -> None:
        self._ohlcv = ohlcv_by_symbol
        self._fwd = forward_return_panel
        self._liq = liquidity_panel
        self._fit_cfg = fitness_config
        self._max_evaluations = max_evaluations
        self._cache: dict[tuple[str, ...], Candidate] = {}
        #: OHLCV 面板惰性构建一次，供所有含 CS_* 的候选公式复用
        self._panel: pd.DataFrame | None = None
        self.n_evaluated = 0
        #: 公式求值失败原因 → 次数。按原因去重记账，见 _record_formula_error
        self._formula_errors: dict[str, int] = {}

    @property
    def formula_errors(self) -> dict[str, int]:
        """公式求值失败原因 → 次数（只读快照）。"""
        return dict(self._formula_errors)

    def _factor_panel(self, tokens: tuple[str, ...]) -> pd.DataFrame:
        """按公式形态选择求值路径：含 CS_* 走面板，否则逐标的。"""
        token_list = list(tokens)
        if formula_requires_panel(token_list) and self._panel is None:
            self._panel = ohlcv_to_panel(self._ohlcv)
        return build_factor_panel(self._ohlcv, token_list, panel=self._panel)

    def evaluate(self, tree: Node) -> Candidate:
        tokens = tuple(to_rpn(tree))
        cached = self._cache.get(tokens)
        if cached is not None:
            return cached
        if self._max_evaluations is not None and self.n_evaluated >= self._max_evaluations:
            raise BudgetExhaustedError(
                f"已评估 {self.n_evaluated} 个不同公式，达到上限 {self._max_evaluations}"
            )
        candidate = self._score(tokens)
        self._cache[tokens] = candidate
        self.n_evaluated += 1
        return candidate

    def _record_formula_error(self, exc: FormulaError, tokens: tuple[str, ...]) -> None:
        """按原因去重记账：首次出现打一条 WARNING，之后只累加计数。

        求值失败本身是正常的（随机生成的个体确实可能退化），所以这里不能抛；
        但也不能像以前那样一声不吭 —— RANK 曾经对**任何**输入都失败，含它的
        个体全被静默打底分，搜索空间悄悄缺了一块，运维侧只看到「这轮没挖出
        好东西」。逐条打日志又会被淹没（一轮进化要评上千个个体），
        故首次告警 + 计数，收尾时再由 _log_formula_error_summary 汇总。
        """
        reason = str(exc)
        seen = self._formula_errors.get(reason, 0)
        self._formula_errors[reason] = seen + 1
        if seen == 0:
            logger.warning(
                "公式求值失败，候选记为触底适应度：%s（公式 %s）", reason, " ".join(tokens),
            )

    def _score(self, tokens: tuple[str, ...]) -> Candidate:
        from app.quant.factor_fitness import compute_factor_fitness

        floor = float(self._fit_cfg.inactivity_floor)
        try:
            factor_panel = self._factor_panel(tokens)
        except FormulaError as exc:
            self._record_formula_error(exc, tokens)
            return _floor_candidate(tokens, floor)

        try:
            result = compute_factor_fitness(
                factor_panel=factor_panel,
                forward_return_panel=self._fwd,
                liquidity_panel=self._liq,
                config=self._fit_cfg,
            )
        except Exception:  # noqa: BLE001 — 退化因子记为触底适应度
            return _floor_candidate(tokens, floor)

        ic_mean, rank_ic_mean, icir = cross_sectional_ic_stats(
            factor_panel["factor"], self._fwd.iloc[:, 0],
        )
        return Candidate(
            tokens=tokens,
            expr=_expr_from_tokens(tokens),
            fitness=result.fitness,
            ic_mean=ic_mean,
            rank_ic_mean=rank_ic_mean,
            icir=icir,
            mean_net_return=result.mean_net_return,
            turnover=result.turnover,
        )

    @property
    def n_unique(self) -> int:
        return len(self._cache)

    @property
    def scored_candidates(self) -> tuple[Candidate, ...]:
        """本轮评估过的全部不同公式（只读快照）。"""
        return tuple(self._cache.values())


# ── 遗传算子编排 ──────────────────────────────────────────────────

def _tournament_select(
    rng: random.Random, scored: list[tuple[Node, Candidate]], k: int,
) -> Node:
    """锦标赛选择：随机取 k 个个体，返回适应度最高者的基因型。"""
    contenders = rng.sample(scored, min(k, len(scored)))
    winner = max(contenders, key=lambda pair: pair[1].fitness)
    return winner[0]


def _breed_offspring(
    rng: random.Random,
    scored: list[tuple[Node, Candidate]],
    config: GAConfig,
) -> Node:
    """由当前种群产生一个子代（交叉 + 变异 + 尺寸约束）。"""
    parent = _tournament_select(rng, scored, config.tournament_size)
    if rng.random() < config.crossover_rate:
        mate = _tournament_select(rng, scored, config.tournament_size)
        child = crossover(rng, parent, mate)
    else:
        child = parent
    if rng.random() < config.mutation_rate:
        child = mutate(
            rng, child, config.max_depth, include_cross_section=config.use_cross_section
        )
    return clamp_size(
        rng, child, config.max_depth, include_cross_section=config.use_cross_section
    )


def _next_population(
    rng: random.Random,
    scored: list[tuple[Node, Candidate]],
    config: GAConfig,
) -> list[Node]:
    """精英保留 + 繁殖，生成下一代种群。"""
    elites = [tree for tree, _ in scored[: config.elite_count]]
    offspring = [
        _breed_offspring(rng, scored, config)
        for _ in range(config.population_size - len(elites))
    ]
    return elites + offspring


def evolve(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    forward_return_panel: pd.DataFrame,
    liquidity_panel: pd.DataFrame | None,
    fitness_config,
    config: GAConfig = GAConfig(),
    *,
    seed_trees: tuple[Node, ...] = (),
) -> GAResult:
    """运行遗传因子挖掘主循环，返回按适应度降序的候选因子。

    Parameters
    ----------
    ohlcv_by_symbol      : 每标的 OHLCV DataFrame（index 为 ISO 时间字符串）
    forward_return_panel : (datetime, instrument) 前瞻收益单列面板
    liquidity_panel      : 流动性面板（可选，用于滑点冲击）
    fitness_config       : factor_fitness.FitnessConfig
    config               : 遗传算法超参
    seed_trees           : 播进初代种群的种子表达式树（V3 I2）。
                           空元组（默认）时行为与历史完全一致。
    """
    if not ohlcv_by_symbol:
        raise ValueError("evolve: 空 universe")

    rng = random.Random(config.seed)
    evaluator = _Evaluator(
        ohlcv_by_symbol,
        forward_return_panel,
        liquidity_panel,
        fitness_config,
        config.max_evaluations,
    )

    population = _initial_population(rng, config, seed_trees)
    history: list[GenerationStat] = []
    truncated = False

    try:
        for gen in range(config.generations):
            scored = _score_population(evaluator, population)
            history.append(_generation_stat(gen, scored))
            if gen < config.generations - 1:
                population = _next_population(rng, scored, config)
    except BudgetExhaustedError as exc:
        # 预算耗尽不是失败：把已经评估出来的东西如实交出去，并标 truncated。
        logger.info("进化提前停止（%s），返回已评估候选中的最优解", exc)
        truncated = True

    _log_formula_error_summary(evaluator)
    return _finalize(evaluator, history, config.top_k, truncated)


def _initial_population(
    rng: random.Random, config: GAConfig, seed_trees: tuple[Node, ...]
) -> list[Node]:
    """初代种群：先按历史方式随机生成满员，再用种子**覆盖**前若干个。

    刻意「先生成再覆盖」而不是「少生成几个」——后者会改变 rng 的消费序列，
    于是「无种子」的调用也会算出与历史不同的结果，把
    「相同 seed → 相同结果」这条可复现性承诺悄悄毁掉。
    """
    random_pop = [
        random_tree(rng, config.max_depth, include_cross_section=config.use_cross_section)
        for _ in range(config.population_size)
    ]
    if not seed_trees:
        return random_pop
    seeds = list(seed_trees[: config.population_size])
    return seeds + random_pop[len(seeds):]


def _log_formula_error_summary(evaluator: _Evaluator) -> None:
    """收尾汇总求值失败：单条告警看不出「偶发退化」还是「算子整体坏掉」，计数能。"""
    errors = evaluator.formula_errors
    if not errors:
        return
    top = sorted(errors.items(), key=lambda kv: kv[1], reverse=True)
    listed = "；".join(
        f"{reason} ×{count}" for reason, count in top[:_MAX_REPORTED_ERROR_REASONS]
    )
    logger.warning(
        "本轮进化共 %d 次公式求值失败（%d 种原因），对应候选均记为触底适应度：%s",
        sum(errors.values()), len(errors), listed,
    )


def _score_population(
    evaluator: _Evaluator, population: list[Node],
) -> list[tuple[Node, Candidate]]:
    scored = [(tree, evaluator.evaluate(tree)) for tree in population]
    scored.sort(key=lambda pair: pair[1].fitness, reverse=True)
    return scored


def _generation_stat(gen: int, scored: list[tuple[Node, Candidate]]) -> GenerationStat:
    fits = [c.fitness for _, c in scored]
    best = scored[0][1]
    return GenerationStat(
        generation=gen,
        best_fitness=best.fitness,
        mean_fitness=float(np.mean(fits)) if fits else 0.0,
        best_expr=best.expr,
    )


def _finalize(
    evaluator: _Evaluator,
    history: list[GenerationStat],
    top_k: int,
    truncated: bool = False,
) -> GAResult:
    """从缓存中挑出全局最优的 top_k 个不同公式作为候选。"""
    unique = list(evaluator.scored_candidates)
    unique.sort(key=lambda c: c.fitness, reverse=True)
    candidates = unique[:top_k]
    return GAResult(
        best=candidates[0] if candidates else None,
        candidates=candidates,
        history=history,
        n_evaluated=evaluator.n_evaluated,
        n_unique=evaluator.n_unique,
        truncated=truncated,
    )


# ── 工具 ──────────────────────────────────────────────────────────

def _expr_from_tokens(tokens: tuple[str, ...]) -> str:
    """RPN token → 可读中缀近似（评分缓存已丢弃树，故从 token 反推）。"""
    return " ".join(tokens)


def _floor_candidate(tokens: tuple[str, ...], floor: float) -> Candidate:
    return Candidate(
        tokens=tokens, expr=_expr_from_tokens(tokens), fitness=floor,
        ic_mean=float("nan"), rank_ic_mean=float("nan"), icir=float("nan"),
        mean_net_return=0.0, turnover=0.0,
    )


def _safe(v: float) -> float | None:
    if v is None:
        return None
    f = float(v)
    if np.isnan(f) or np.isinf(f):
        return None
    return round(f, 6)
