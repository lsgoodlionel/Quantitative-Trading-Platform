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

import random
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.formula_factor import FormulaError, evaluate_formula
from app.quant.mining.expression_tree import (
    Node, clamp_size, crossover, mutate, random_tree, to_rpn,
)

# 单日横截面 IC 所需最少标的数（与 factor_lib/ranking 口径一致）
_MIN_NAMES = 3


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

    def to_dict(self) -> dict:
        return {
            "best": self.best.to_dict() if self.best else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "history": [h.to_dict() for h in self.history],
            "n_evaluated": self.n_evaluated,
            "n_unique": self.n_unique,
        }


# ── 因子面板构建 & 评分 ────────────────────────────────────────────

def _build_factor_panel(
    ohlcv_by_symbol: dict[str, pd.DataFrame], tokens: list[str],
) -> pd.DataFrame:
    """逐标的执行 RPN 公式，拼为 (datetime, instrument) 单列因子面板。"""
    parts: list[pd.DataFrame] = []
    for symbol, frame in ohlcv_by_symbol.items():
        series = evaluate_formula(frame, tokens).astype(float)
        df = series.to_frame("factor")
        df.index.name = "datetime"
        df["instrument"] = symbol
        parts.append(df.set_index("instrument", append=True))
    return pd.concat(parts).sort_index()


def _cross_sectional_ic_stats(
    factor: pd.Series, forward_return: pd.Series,
) -> tuple[float, float, float]:
    """复用 factor_lib.ranking 的横截面 IC 口径，返回 (ic_mean, rank_ic_mean, icir)。"""
    from app.quant.factor_lib.ranking import _cross_sectional_ic

    ic_arr, rank_arr = _cross_sectional_ic(factor, forward_return, _MIN_NAMES)
    if ic_arr.size == 0:
        return float("nan"), float("nan"), float("nan")
    ic_mean = float(np.mean(ic_arr))
    ic_std = float(np.std(ic_arr))
    icir = ic_mean / ic_std if ic_std > 1e-9 else float("nan")
    rank_ic_mean = float(np.mean(rank_arr)) if rank_arr.size else float("nan")
    return ic_mean, rank_ic_mean, icir


class _Evaluator:
    """封装评分所需的共享面板；带缓存避免重复评估同一公式。"""

    def __init__(
        self,
        ohlcv_by_symbol: dict[str, pd.DataFrame],
        forward_return_panel: pd.DataFrame,
        liquidity_panel: pd.DataFrame | None,
        fitness_config,
    ) -> None:
        self._ohlcv = ohlcv_by_symbol
        self._fwd = forward_return_panel
        self._liq = liquidity_panel
        self._fit_cfg = fitness_config
        self._cache: dict[tuple[str, ...], Candidate] = {}
        self.n_evaluated = 0

    def evaluate(self, tree: Node) -> Candidate:
        tokens = tuple(to_rpn(tree))
        cached = self._cache.get(tokens)
        if cached is not None:
            return cached
        candidate = self._score(tokens)
        self._cache[tokens] = candidate
        self.n_evaluated += 1
        return candidate

    def _score(self, tokens: tuple[str, ...]) -> Candidate:
        from app.quant.factor_fitness import compute_factor_fitness

        floor = float(self._fit_cfg.inactivity_floor)
        try:
            factor_panel = _build_factor_panel(self._ohlcv, list(tokens))
        except FormulaError:
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

        ic_mean, rank_ic_mean, icir = _cross_sectional_ic_stats(
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
        child = mutate(rng, child, config.max_depth)
    return clamp_size(rng, child, config.max_depth)


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
) -> GAResult:
    """运行遗传因子挖掘主循环，返回按适应度降序的候选因子。

    Parameters
    ----------
    ohlcv_by_symbol      : 每标的 OHLCV DataFrame（index 为 ISO 时间字符串）
    forward_return_panel : (datetime, instrument) 前瞻收益单列面板
    liquidity_panel      : 流动性面板（可选，用于滑点冲击）
    fitness_config       : factor_fitness.FitnessConfig
    config               : 遗传算法超参
    """
    if not ohlcv_by_symbol:
        raise ValueError("evolve: 空 universe")

    rng = random.Random(config.seed)
    evaluator = _Evaluator(
        ohlcv_by_symbol, forward_return_panel, liquidity_panel, fitness_config,
    )

    population = [random_tree(rng, config.max_depth) for _ in range(config.population_size)]
    history: list[GenerationStat] = []

    for gen in range(config.generations):
        scored = _score_population(evaluator, population)
        history.append(_generation_stat(gen, scored))
        if gen < config.generations - 1:
            population = _next_population(rng, scored, config)

    return _finalize(evaluator, history, config.top_k)


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


def _finalize(evaluator: _Evaluator, history: list[GenerationStat], top_k: int) -> GAResult:
    """从缓存中挑出全局最优的 top_k 个不同公式作为候选。"""
    unique = list(evaluator._cache.values())
    unique.sort(key=lambda c: c.fitness, reverse=True)
    candidates = unique[:top_k]
    return GAResult(
        best=candidates[0] if candidates else None,
        candidates=candidates,
        history=history,
        n_evaluated=evaluator.n_evaluated,
        n_unique=evaluator.n_unique,
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
