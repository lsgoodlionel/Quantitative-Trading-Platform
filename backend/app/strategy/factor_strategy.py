"""FactorStrategySpec — 「公式因子 → 可交易策略」的纯数据描述（V3 Wave A-a / G1）

V3 记录的断链是「因子挖掘的结果只能看，不能交易」。引擎侧 Wave K-d 已经补齐
（`FormulaFactorAlphaModel` / `OptimizerPCM` / `FrameworkStrategy`），还差的是一条
**能存库、能经 API 往返、能被前端编辑**的通道。

为什么不塞进 `STRATEGY_REGISTRY`：
    注册表的契约是「类 + params dict」，而 `FrameworkStrategy` 的构造参数是三个
    模型**对象**。硬塞需要一个能从 dict 还原对象的工厂，会污染注册表语义，
    16 个 preset 的加载路径也要跟着改。这里改用平行的
    `FactorStrategySpec`（纯数据）+ `build_factor_strategy()`（工厂）。

分位约定（与 `FormulaFactorAlphaModel` 不同，务必注意）：
    本模块的 `long_quantile` / `short_quantile` 是**比例**（0.2 = 取分数最高/最低的
    20%），这是因子组合的通行说法，也是契约给出的默认值语义；
    `FormulaFactorAlphaModel` 收的则是**分位切点**（0.8 = 第 80 百分位）。
    翻译在 `build_factor_strategy` 内一次性完成：切点 = 1 - 比例。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta

from app.engine.framework.alpha import AlphaModel
from app.engine.framework.factor_alpha import (
    FormulaFactorAlphaModel,
    LibraryFactorAlphaModel,
    PanelLibraryFactorAlphaModel,
)
from app.engine.framework.insight import Insight
from app.engine.framework.optimizer_pcm import OptimizerPCM
from app.engine.framework.portfolio_construction import (
    EqualWeightingPCM,
    InsightWeightingPCM,
    PortfolioConstructionModel,
)
from app.engine.framework.strategy import FrameworkStrategy
from app.engine.portfolio.optimizer import OptimizeMethod
from app.quant.formula_factor import FEATURE_META, OP_META

#: 因子求值与组合优化共同的历史长度门槛（`OptimizerPCM.MIN_HISTORY` 亦为 60）
MIN_HISTORY = 60

#: 因子库条目缓存（生成有开销，且进程内不变）
_LIBRARY_CACHE: dict | None = None
#: 截面因子至少要有两个标的才谈得上分位
MIN_UNIVERSE = 2

#: 等权 —— 直接用 `EqualWeightingPCM`，比走优化器省一遍协方差估计，
#: 且不受优化器「≥60 根 bar」的门槛限制，回测早期照样能开仓。
EQUAL_WEIGHT = "equal_weight"
#: 按因子分数绝对值加权（`FormulaFactorAlphaModel` 已在 insight.weight 里给好）
INSIGHT_WEIGHT = "insight_weight"

_OPTIMIZER_METHODS: dict[str, OptimizeMethod] = {
    method.value: method for method in OptimizeMethod if method.value != EQUAL_WEIGHT
}

#: 允许的 `portfolio_method` 取值（供 API 校验与前端下拉框）
PORTFOLIO_METHODS: tuple[str, ...] = (
    EQUAL_WEIGHT,
    INSIGHT_WEIGHT,
    *sorted(_OPTIMIZER_METHODS),
)

_FEATURE_TOKENS = frozenset(meta["name"] for meta in FEATURE_META)
_OP_ARITY = {meta["name"]: int(meta["arity"]) for meta in OP_META}


# ── 纯数据 spec ───────────────────────────────────────────────────


@dataclass(frozen=True)
class FactorStrategySpec:
    """一条「因子 → 策略」的完整描述，可序列化、可持久化、可重放。"""

    #: RPN 表达式，空格分隔，如 "MOM20 ATR_RATIO DIV"。
    #: 用因子库条目时留空，改填 `library_factor`（两者互斥）。
    formula: str = ""
    #: 声明式因子库的条目名（如 "KMID" / "MA20"）。与 `formula` 互斥。
    #: 因子库的 expr 是 Qlib 风格的展示标注，与 RPN 词表不是同一种语言，
    #: 但 FactorSpec 自带 compute 可调用对象，无需翻译即可直接打分。
    library_factor: str = ""
    #: 标的池（构造时统一大写并去重，顺序保持首次出现）
    universe: tuple[str, ...] = field(default=())
    #: 做多分数最高的这一**比例**（0.2 = 前 20%）
    long_quantile: float = 0.2
    #: 做空分数最低的这一比例；None = 纯多头
    short_quantile: float | None = None
    #: 再平衡节奏（天），同时作为观点有效期
    rebalance_days: int = 5
    #: 见 `PORTFOLIO_METHODS`
    portfolio_method: str = EQUAL_WEIGHT
    #: 同时持仓上限；None = 不限
    max_positions: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe", _normalized_universe(self.universe))
        _validate(self)

    @property
    def tokens(self) -> list[str]:
        """RPN token 列表（`evaluate_formula` 的入参形态）。"""
        return self.formula.split()

    def to_dict(self) -> dict:
        return {
            "formula": self.formula,
            "library_factor": self.library_factor,
            "universe": list(self.universe),
            "long_quantile": self.long_quantile,
            "short_quantile": self.short_quantile,
            "rebalance_days": self.rebalance_days,
            "portfolio_method": self.portfolio_method,
            "max_positions": self.max_positions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FactorStrategySpec:
        """从 dict 还原（缺省字段取默认值；多余字段视为错误而非静默忽略）。"""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"FactorStrategySpec 收到未知字段: {', '.join(unknown)}")
        return cls(**{k: v for k, v in data.items() if k in known})


def _normalized_universe(universe) -> tuple[str, ...]:
    """去空白、转大写、去重（保序）—— 标的池来自用户输入，必须归一化。"""
    seen: dict[str, None] = {}
    for raw in universe or ():
        symbol = str(raw).strip().upper()
        if symbol:
            seen.setdefault(symbol, None)
    return tuple(seen)


# ── 校验 ─────────────────────────────────────────────────────────


def _validate(spec: FactorStrategySpec) -> None:
    """spec 是系统边界（API / Redis / 前端）上的数据，必须快速失败。"""
    if bool(spec.formula) == bool(spec.library_factor):
        raise ValueError(
            "formula 与 library_factor 必须二选一：两者都给会产生歧义，"
            "都不给则没有因子可算"
        )
    if spec.formula:
        _validate_formula(spec.formula)
    else:
        _validate_library_factor(spec.library_factor)

    if len(spec.universe) < MIN_UNIVERSE:
        raise ValueError(f"标的池至少需要 {MIN_UNIVERSE} 个标的，收到 {len(spec.universe)} 个")
    if not 0.0 < spec.long_quantile <= 1.0:
        raise ValueError(f"long_quantile 必须落在 (0, 1]，收到 {spec.long_quantile}")
    if spec.short_quantile is not None:
        _validate_short_band(spec.long_quantile, spec.short_quantile)
    if spec.rebalance_days < 1:
        raise ValueError(f"rebalance_days 至少为 1，收到 {spec.rebalance_days}")
    if spec.portfolio_method not in PORTFOLIO_METHODS:
        raise ValueError(
            f"未知 portfolio_method: {spec.portfolio_method}"
            f"（可选：{', '.join(PORTFOLIO_METHODS)}）"
        )
    if spec.max_positions is not None and spec.max_positions < 1:
        raise ValueError(f"max_positions 至少为 1，收到 {spec.max_positions}")


def library_factor_specs() -> dict:
    """因子库条目名 → FactorSpec。首次调用时生成并缓存（生成有一定开销）。"""
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is None:
        from app.quant.factor_lib.loader import generate_factor_library

        _LIBRARY_CACHE = {spec.name: spec for spec in generate_factor_library()}
    return _LIBRARY_CACHE


def _validate_library_factor(name: str) -> None:
    specs = library_factor_specs()
    if name not in specs:
        # 因子库有数百个条目，全列出来对用户没帮助，给几个同前缀的更有用
        prefix = name[:2].upper()
        hint = sorted(n for n in specs if n.startswith(prefix))[:8]
        raise ValueError(
            f"未知因子库条目: {name}"
            + (f"（是否想找：{', '.join(hint)}）" if hint else "")
        )


def _validate_short_band(long_quantile: float, short_quantile: float) -> None:
    if not 0.0 <= short_quantile < 1.0:
        raise ValueError(f"short_quantile 必须落在 [0, 1)，收到 {short_quantile}")
    # 多头取上 long_quantile、空头取下 short_quantile，两段相加达到 1 就会重叠
    # （相加正好等于 1 时两条切点重合，处在切点上的标的会被同时判为多头与空头）。
    if short_quantile + long_quantile >= 1.0:
        raise ValueError(
            f"多空分位重叠：long_quantile({long_quantile}) + "
            f"short_quantile({short_quantile}) 必须 < 1"
        )


def _validate_formula(formula: str) -> None:
    """静态检查 RPN：token 是否已知 + 栈是否平衡（不求值，故无需行情）。"""
    tokens = formula.split()
    if not tokens:
        raise ValueError("公式为空")

    depth = 0
    for token in tokens:
        if token in _FEATURE_TOKENS:
            depth += 1
            continue
        arity = _OP_ARITY.get(token)
        if arity is None:
            raise ValueError(f"未知 token: {token}（既非特征也非算子）")
        if depth < arity:
            raise ValueError(f"算子 {token} 需要 {arity} 个操作数，栈中只有 {depth} 个")
        depth += 1 - arity

    if depth != 1:
        raise ValueError(f"公式不平衡：执行完毕后栈中剩余 {depth} 个值（应为 1 个）")


# ── 工厂 ─────────────────────────────────────────────────────────


def build_factor_strategy(spec: FactorStrategySpec) -> FrameworkStrategy:
    """把 spec 装配成可直接喂给 `PortfolioBacktestEngine` 的策略。"""
    period = timedelta(days=spec.rebalance_days)
    # 比例 → 分位切点：取前 20% ⇒ 分数 ≥ 第 80 百分位
    common = {
        "period": period,
        "long_quantile": 1.0 - spec.long_quantile,
        "short_quantile": spec.short_quantile,
    }
    if spec.formula:
        factor = FormulaFactorAlphaModel(
            tokens=spec.tokens, min_history=MIN_HISTORY, **common
        )
    else:
        # min_history 交给 Library*FactorAlphaModel 按因子自带窗口推。
        # 面板型（Alpha101 等截面 alpha）与单标的型走不同的求值粒度。
        entry = library_factor_specs()[spec.library_factor]
        model_cls = (
            PanelLibraryFactorAlphaModel if entry.is_panel else LibraryFactorAlphaModel
        )
        factor = model_cls(entry, **common)
    return FrameworkStrategy(
        alpha=_RebalanceThrottledAlpha(factor, period),
        portfolio_construction=_build_pcm(spec, period),
        params=spec.to_dict(),      # 带上完整 spec，便于回放与归因
    )


class _RebalanceThrottledAlpha(AlphaModel):
    """
    只在再平衡节奏上重算因子，其余 bar 不出观点。

    没有这层，`rebalance_days` 会是个**静默的空参数**：`FormulaFactorAlphaModel`
    每根 bar 都出观点，而 `FrameworkStrategy` 一见到新观点就调仓，
    `PortfolioConstructionModel.should_rebalance` 的节奏根本轮不到生效。
    因子组合的语义本就是「每 N 天重排一次，中间持有不动」。

    观点有效期与本节奏同为 `period`：下一次重算恰好发生在旧观点过期的那根 bar 上，
    新观点先并入再判过期，因此不会出现「先清仓再买回」的空窗。
    """

    def __init__(self, factor: FormulaFactorAlphaModel, period: timedelta) -> None:
        self.factor = factor
        self._period = period
        self._last_emit: datetime | None = None
        self.name = factor.name

    def on_start(self, ctx) -> None:
        self.factor.on_start(ctx)

    def update(self, ctx) -> list[Insight]:
        if self._last_emit is not None and ctx.time - self._last_emit < self._period:
            return []
        insights = self.factor.update(ctx)
        # 预热期内因子出不了观点，此时不能起算节奏，否则会白白跳过首个可交易窗口
        if insights:
            self._last_emit = ctx.time
        return insights


def _build_pcm(
    spec: FactorStrategySpec, period: timedelta
) -> PortfolioConstructionModel:
    pcm = _base_pcm(spec.portfolio_method, period)
    if spec.max_positions is None:
        return pcm
    return _MaxPositionsPCM(pcm, spec.max_positions)


def _base_pcm(method: str, period: timedelta) -> PortfolioConstructionModel:
    if method == EQUAL_WEIGHT:
        return EqualWeightingPCM(rebalance_period=period)
    if method == INSIGHT_WEIGHT:
        return InsightWeightingPCM(rebalance_period=period)
    return OptimizerPCM(method=_OPTIMIZER_METHODS[method], rebalance_period=period)


class _MaxPositionsPCM(PortfolioConstructionModel):
    """
    在任意 PCM 之上限制同时持仓数：按 |权重| 取前 N，其余**置 0**。

    置 0 而不是从结果里删掉：`create_targets` 只为「结果中不存在」的持仓补清仓单，
    权重 0 才是「明确要求清掉这个标的」，两者语义不同但都需要，留 0 更稳妥。
    """

    def __init__(self, inner: PortfolioConstructionModel, max_positions: int) -> None:
        super().__init__(rebalance_period=inner.rebalance_period)
        if max_positions < 1:
            raise ValueError(f"max_positions 至少为 1，收到 {max_positions}")
        self._inner = inner
        self._max = max_positions

    def compute_weights(self, ctx, insights: list[Insight]) -> dict[str, float]:
        weights = self._inner.compute_weights(ctx, insights)
        active = {s: w for s, w in weights.items() if w != 0.0}
        if len(active) <= self._max:
            return weights

        # 同权重时按标的名定序，保证结果可复现
        kept = sorted(active, key=lambda s: (-abs(active[s]), s))[: self._max]
        total = sum(abs(active[s]) for s in kept)
        scaled = {s: active[s] / total for s in kept} if total > 0 else {}
        return {symbol: scaled.get(symbol, 0.0) for symbol in weights}


__all__ = [
    "EQUAL_WEIGHT",
    "INSIGHT_WEIGHT",
    "MIN_HISTORY",
    "MIN_UNIVERSE",
    "PORTFOLIO_METHODS",
    "FactorStrategySpec",
    "build_factor_strategy",
]
