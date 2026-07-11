"""
参数优化 Hyperopt（C1）

在现有回测引擎之上提供三种搜索算法 + 多目标损失函数，用于策略参数寻优。

设计参考（仅算法定义，非复制代码）:
  refs/freqtrade/freqtrade/optimize/hyperopt/            贝叶斯优化 + 损失接口
  refs/freqtrade/freqtrade/optimize/hyperopt_loss/*      Sharpe/Sortino/Calmar/MaxDD 等损失
  refs/vnpy/vnpy/trader/optimize.py                      参数空间 generate_settings / 穷举

搜索算法:
  - grid    穷举网格（离散取值的笛卡尔积，超限随机采样）
  - random  随机搜索（在离散/连续参数空间内均匀采样）
  - bayesian 贝叶斯优化（sklearn 高斯过程代理 + UCB 采集；不可用时退化为随机）

损失函数统一约定为「越大越好」的 score（便于前端排行与现有 optimize 保持一致）。
freqtrade 的 loss 越小越好，这里等价取负后统一为 score。
"""

from __future__ import annotations

import logging
import random as _random
from dataclasses import dataclass, field
from itertools import product
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# 每种损失至少要求的成交笔数，不足则重罚（避免过拟合到极少数交易）
_TOO_FEW_TRADES_PENALTY = -100.0
# 贝叶斯优化初始随机探索比例
_BAYES_INIT_RATIO = 0.35
_BAYES_MIN_INIT = 5
# UCB 采集函数探索系数
_UCB_KAPPA = 1.96
# 贝叶斯每轮候选采样池大小
_BAYES_CANDIDATE_POOL = 256

EvaluateFn = Callable[[dict], dict]
"""params -> metrics dict（report['metrics'] 结构）。"""


# ── 损失函数：metrics(dict) -> score（越大越好）────────────────────

def _loss_sharpe(m: dict) -> float:
    return float(m.get("sharpe_ratio", 0.0))


def _loss_sortino(m: dict) -> float:
    return float(m.get("sortino_ratio", 0.0))


def _loss_calmar(m: dict) -> float:
    return float(m.get("calmar_ratio", 0.0))


def _loss_omega(m: dict) -> float:
    return float(m.get("omega_ratio", 0.0))


def _loss_sqn(m: dict) -> float:
    return float(m.get("sqn", 0.0))


def _loss_profit(m: dict) -> float:
    return float(m.get("total_return_pct", 0.0))


def _loss_annual(m: dict) -> float:
    return float(m.get("annual_return_pct", 0.0))


def _loss_max_drawdown(m: dict) -> float:
    # 回撤越小越好 → 取负绝对值最大化
    return -abs(float(m.get("max_drawdown_pct", 0.0)))


def _loss_profit_drawdown(m: dict) -> float:
    # freqtrade profit_drawdown: 收益 / |回撤|
    ret = float(m.get("total_return_pct", 0.0))
    dd = abs(float(m.get("max_drawdown_pct", 0.0)))
    return ret / dd if dd > 1e-9 else ret


def _loss_profit_factor(m: dict) -> float:
    return float(m.get("profit_factor", 0.0))


def _loss_multi_metric(m: dict) -> float:
    # 综合：夏普为主 + 年化加成 - 回撤惩罚（成本感知式）
    sharpe = float(m.get("sharpe_ratio", 0.0))
    annual = float(m.get("annual_return_pct", 0.0)) / 100.0
    dd = abs(float(m.get("max_drawdown_pct", 0.0))) / 100.0
    return sharpe + annual - 2.0 * dd


LOSS_FUNCTIONS: dict[str, tuple[str, Callable[[dict], float]]] = {
    "sharpe": ("夏普比率", _loss_sharpe),
    "sortino": ("索提诺比率", _loss_sortino),
    "calmar": ("卡玛比率", _loss_calmar),
    "omega": ("Omega 比率", _loss_omega),
    "sqn": ("SQN 系统质量数", _loss_sqn),
    "profit": ("总收益率", _loss_profit),
    "annual_return": ("年化收益率", _loss_annual),
    "max_drawdown": ("最小回撤", _loss_max_drawdown),
    "profit_drawdown": ("收益/回撤比", _loss_profit_drawdown),
    "profit_factor": ("获利因子", _loss_profit_factor),
    "multi_metric": ("综合多目标", _loss_multi_metric),
}


def list_loss_functions() -> list[dict]:
    """返回损失函数元数据（供前端下拉）。"""
    return [{"name": k, "label": v[0]} for k, v in LOSS_FUNCTIONS.items()]


def score_metrics(metrics: dict, loss_name: str, min_trades: int) -> float:
    """依据损失函数计算 score；成交过少则重罚。"""
    if loss_name not in LOSS_FUNCTIONS:
        raise ValueError(f"未知损失函数 '{loss_name}'，可用: {list(LOSS_FUNCTIONS)}")
    total_trades = int(metrics.get("total_trades", 0))
    if total_trades < min_trades:
        return _TOO_FEW_TRADES_PENALTY
    score = LOSS_FUNCTIONS[loss_name][1](metrics)
    if not np.isfinite(score):
        return _TOO_FEW_TRADES_PENALTY
    return float(score)


# ── 参数空间 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParamDim:
    """单个参数维度。

    离散取值:   choices=[5,10,20]（数值或字符串）
    连续区间:   low/high/step + is_int（用于 random / bayesian 采样）
    """

    name: str
    choices: list | None = None
    low: float | None = None
    high: float | None = None
    step: float | None = None
    is_int: bool = False

    @property
    def is_categorical(self) -> bool:
        return self.choices is not None

    @property
    def is_numeric_choice(self) -> bool:
        return self.is_categorical and all(
            isinstance(c, (int, float)) and not isinstance(c, bool) for c in self.choices
        )

    def sample(self, rng: np.random.Generator):
        if self.is_categorical:
            return self.choices[int(rng.integers(0, len(self.choices)))]
        return self._decode(float(rng.random()))

    def grid_values(self) -> list:
        if self.is_categorical:
            return list(self.choices)
        return self._range_values()

    def _range_values(self) -> list:
        low, high = float(self.low), float(self.high)
        step = float(self.step) if self.step else (1.0 if self.is_int else (high - low) / 10.0)
        step = step if step > 0 else 1.0
        out: list = []
        v = low
        while v <= high + 1e-9:
            out.append(int(round(v)) if self.is_int else round(v, 6))
            v += step
        return out or [int(low) if self.is_int else low]

    def encode(self, value) -> float:
        """把取值编码到 [0,1]（贝叶斯 GP 用）。"""
        if self.is_categorical:
            idx = self.choices.index(value) if value in self.choices else 0
            return idx / max(len(self.choices) - 1, 1)
        low, high = float(self.low), float(self.high)
        return (float(value) - low) / (high - low) if high > low else 0.0

    def _decode(self, u: float):
        u = min(max(u, 0.0), 1.0)
        low, high = float(self.low), float(self.high)
        val = low + u * (high - low)
        if self.step and self.step > 0:
            val = low + round((val - low) / self.step) * self.step
        return int(round(val)) if self.is_int else round(val, 6)

    def decode_unit(self, u: float):
        """从 [0,1] 解码为参数取值（分类→就近索引）。"""
        if self.is_categorical:
            idx = int(round(min(max(u, 0.0), 1.0) * (len(self.choices) - 1)))
            return self.choices[idx]
        return self._decode(u)


@dataclass(frozen=True)
class ParamSpace:
    dims: list[ParamDim]

    @property
    def names(self) -> list[str]:
        return [d.name for d in self.dims]

    def grid_size(self) -> int:
        """笛卡尔积总规模（不物化，仅相乘各维取值数）。"""
        size = 1
        for d in self.dims:
            size *= max(1, len(d.grid_values()))
        return size

    def grid(self) -> list[dict]:
        value_lists = [d.grid_values() for d in self.dims]
        return [dict(zip(self.names, combo)) for combo in product(*value_lists)]

    def sample(self, rng: np.random.Generator) -> dict:
        return {d.name: d.sample(rng) for d in self.dims}

    def encode(self, params: dict) -> list[float]:
        return [d.encode(params[d.name]) for d in self.dims]

    def decode(self, vec) -> dict:
        return {d.name: d.decode_unit(float(u)) for d, u in zip(self.dims, vec)}

    @staticmethod
    def from_spec(spec: dict) -> "ParamSpace":
        """从请求 param_space 构造。

        每个参数可为:
          - list:  [5,10,20]          → 离散取值
          - dict:  {"low":5,"high":50,"step":1,"type":"int"}   → 连续区间
                   {"choices":["sma","ema"]}                    → 显式分类
        """
        dims: list[ParamDim] = []
        for name, definition in spec.items():
            dims.append(_build_dim(name, definition))
        if not dims:
            raise ValueError("参数空间为空")
        return ParamSpace(dims=dims)


def _build_dim(name: str, definition) -> ParamDim:
    if isinstance(definition, list):
        if not definition:
            raise ValueError(f"参数 '{name}' 取值列表为空")
        return ParamDim(name=name, choices=list(definition))
    if isinstance(definition, dict):
        if "choices" in definition:
            return ParamDim(name=name, choices=list(definition["choices"]))
        low = definition.get("low")
        high = definition.get("high")
        if low is None or high is None:
            raise ValueError(f"参数 '{name}' 缺少 low/high 区间定义")
        is_int = str(definition.get("type", "float")).lower() in ("int", "integer")
        return ParamDim(
            name=name, low=float(low), high=float(high),
            step=definition.get("step"), is_int=is_int,
        )
    raise ValueError(f"参数 '{name}' 定义无效，应为 list 或 dict")


# ── 搜索结果 ─────────────────────────────────────────────────────

@dataclass
class Trial:
    params: dict
    score: float
    metrics: dict


@dataclass
class HyperoptOutcome:
    algorithm: str
    loss_name: str
    best_params: dict
    best_score: float
    best_metrics: dict
    trials: list[Trial] = field(default_factory=list)
    total_space: int = 0
    evaluated: int = 0
    used_fallback: bool = False  # 贝叶斯退化为随机时为 True


# ── 搜索算法 ─────────────────────────────────────────────────────

def _run_trials(
    param_dicts: list[dict],
    evaluate: EvaluateFn,
    loss_name: str,
    min_trades: int,
) -> list[Trial]:
    trials: list[Trial] = []
    for params in param_dicts:
        try:
            metrics = evaluate(params)
        except Exception:
            logger.debug("评估参数失败: %s", params, exc_info=True)
            continue
        score = score_metrics(metrics, loss_name, min_trades)
        trials.append(Trial(params=params, score=score, metrics=metrics))
    return trials


# 网格全展开的最大规模上限；超过则改用随机采样，避免内存爆炸
_MAX_GRID_MATERIALIZE = 50_000


def _grid_search(
    space: ParamSpace, evaluate: EvaluateFn, loss_name: str,
    n_trials: int, min_trades: int, rng: np.random.Generator,
) -> tuple[list[Trial], int]:
    total = space.grid_size()
    # 网格过大：不物化完整笛卡尔积，退化为随机采样 n_trials 个点
    if total > _MAX_GRID_MATERIALIZE:
        logger.warning(
            "网格规模 %d 超过上限 %d，改用随机采样 %d 个点",
            total, _MAX_GRID_MATERIALIZE, n_trials,
        )
        return _run_trials(
            [space.sample(rng) for _ in range(n_trials)],
            evaluate, loss_name, min_trades,
        ), total
    combos = space.grid()
    if len(combos) > n_trials:
        idx = rng.permutation(len(combos))[:n_trials]
        combos = [combos[i] for i in idx]
    return _run_trials(combos, evaluate, loss_name, min_trades), total


def _random_search(
    space: ParamSpace, evaluate: EvaluateFn, loss_name: str,
    n_trials: int, min_trades: int, rng: np.random.Generator,
) -> list[Trial]:
    seen: set[tuple] = set()
    combos: list[dict] = []
    attempts = 0
    while len(combos) < n_trials and attempts < n_trials * 20:
        attempts += 1
        params = space.sample(rng)
        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)
        combos.append(params)
    return _run_trials(combos, evaluate, loss_name, min_trades)


def _bayesian_search(
    space: ParamSpace, evaluate: EvaluateFn, loss_name: str,
    n_trials: int, min_trades: int, rng: np.random.Generator,
) -> tuple[list[Trial], bool]:
    """高斯过程 + UCB 采集。sklearn 不可用时退化为随机。"""
    try:
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern, ConstantKernel
    except Exception:
        logger.warning("sklearn 不可用，贝叶斯优化退化为随机搜索")
        return _random_search(space, evaluate, loss_name, n_trials, min_trades, rng), True

    n_init = max(_BAYES_MIN_INIT, int(n_trials * _BAYES_INIT_RATIO))
    n_init = min(n_init, n_trials)
    trials = _random_search(space, evaluate, loss_name, n_init, min_trades, rng)

    kernel = ConstantKernel(1.0) * Matern(nu=2.5)
    for _ in range(n_trials - len(trials)):
        if len(trials) < 2:
            break
        x_obs = np.array([space.encode(t.params) for t in trials])
        y_obs = np.array([t.score for t in trials])
        gp = GaussianProcessRegressor(
            kernel=kernel, normalize_y=True, n_restarts_optimizer=1,
            alpha=1e-6, random_state=int(rng.integers(0, 1_000_000)),
        )
        try:
            gp.fit(x_obs, y_obs)
        except Exception:
            logger.debug("GP 拟合失败，跳过本轮采集", exc_info=True)
            break
        candidates = rng.random((_BAYES_CANDIDATE_POOL, len(space.dims)))
        mu, sigma = gp.predict(candidates, return_std=True)
        acq = mu + _UCB_KAPPA * sigma
        best_c = candidates[int(np.argmax(acq))]
        next_params = space.decode(best_c)
        new = _run_trials([next_params], evaluate, loss_name, min_trades)
        if not new:
            break
        trials.extend(new)
    return trials, False


# ── 主入口 ───────────────────────────────────────────────────────

def run_hyperopt(
    evaluate: EvaluateFn,
    space: ParamSpace,
    loss_name: str = "sharpe",
    algorithm: str = "bayesian",
    n_trials: int = 40,
    min_trades: int = 1,
    seed: int = 42,
) -> HyperoptOutcome:
    """执行参数优化。

    evaluate:  params -> metrics dict（内部跑一次回测）
    space:     参数空间
    algorithm: grid / random / bayesian
    """
    if loss_name not in LOSS_FUNCTIONS:
        raise ValueError(f"未知损失函数 '{loss_name}'")
    rng = np.random.default_rng(seed)
    total_space = _safe_grid_size(space)
    used_fallback = False

    if algorithm == "grid":
        trials, total_space = _grid_search(space, evaluate, loss_name, n_trials, min_trades, rng)
    elif algorithm == "random":
        trials = _random_search(space, evaluate, loss_name, n_trials, min_trades, rng)
    elif algorithm == "bayesian":
        trials, used_fallback = _bayesian_search(space, evaluate, loss_name, n_trials, min_trades, rng)
    else:
        raise ValueError(f"未知算法 '{algorithm}'，可用: grid / random / bayesian")

    if not trials:
        raise ValueError("所有参数组合评估均失败，请检查参数空间与数据")

    trials.sort(key=lambda t: t.score, reverse=True)
    best = trials[0]
    return HyperoptOutcome(
        algorithm=algorithm,
        loss_name=loss_name,
        best_params=best.params,
        best_score=round(best.score, 6),
        best_metrics=best.metrics,
        trials=trials,
        total_space=total_space,
        evaluated=len(trials),
        used_fallback=used_fallback,
    )


def _safe_grid_size(space: ParamSpace) -> int:
    size = 1
    for d in space.dims:
        size *= max(len(d.grid_values()), 1)
        if size > 10_000_000:
            return size
    return size
