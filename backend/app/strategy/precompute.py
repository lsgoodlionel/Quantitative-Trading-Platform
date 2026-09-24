"""指标预算（V3 Wave E-a，J2 重定向）

实测（`docs/profiling-backtest-j2.md`）：一次回测里 86%（单标的）/ 97.6%（组合）
的时间花在策略的 `on_bar` 里，而不是引擎。热点是**每根 bar 对全量历史重算指标**。
这个模块提供另一条路：策略声明要哪些指标，框架**一次算完**，`on_bar` 只按游标取值。

## 前视偏差是这件事唯一真正难的地方

一次算完整条均线，意味着那条序列里装着未来。三道防线：

1. **视图物理裁剪**（`IndicatorView.__init__`）—— 视图持有的数组本身就到当前游标
   为止。不是「取值时判断一下」，是**装进去的就没有未来**。策略即便绕过公开方法
   去摸私有字段，摸到的也只是过去。
2. **公开接口不返回完整序列** —— `value()` 取标量、`series(name, n)` 取有界窗口，
   两者的上界都是当前游标。没有任何方法能交出整条指标。
3. **因果性抽检**（`build_indicator_store`）—— 用前缀重算一遍与全帧比对，
   `close.shift(-1)` 这类偷看未来的指标函数在声明时就被拦下。
   抽检不是证明，兜底的是 `app/engine/backtest/bias_detection.py`。

## 旧写法不受影响

未覆盖 `declare_indicators()` 的策略拿到 `indicator_spec() is None`，整条预算路径
不构造、不计算、不进入上下文 —— 用户策略在仓库外，不能要求他们改。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from app.core.errors import StrategyContractError

__all__ = [
    "IndicatorBook",
    "IndicatorProvider",
    "IndicatorSpec",
    "IndicatorView",
    "LiveIndicatorBook",
    "build_indicator_store",
    "indicator_spec_of",
    "view_from_history",
]

#: 因果性抽检的切点（占全帧长度的比例）。每个切点比对**整段前缀** ——
#: 两个切点足以拦住绝大多数非因果写法，代价是每条指标多算两次
_CAUSALITY_PROBES = (0.55, 0.85)
#: 抽检要求的最短帧长：太短的帧上前缀几乎全是 NaN，检不出东西
_MIN_PROBE_BARS = 20
#: 抽检的浮点容差。真正的偷看未来带来的偏差远大于这个量级
_CAUSALITY_REL_TOL = 1e-9
_CAUSALITY_ABS_TOL = 1e-12

#: 指标函数签名：`fn(frame, *args, **kwargs)`，返回 Series / Series 序列 / Series 字典
IndicatorFn = Callable[..., object]


# ── 声明 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _IndicatorDef:
    """一条指标声明。`names` 长度 > 1 或 `multi=True` 表示多输出。"""

    names: tuple[str, ...]
    fn: IndicatorFn
    args: tuple
    kwargs: dict
    multi: bool


class IndicatorSpec:
    """
    `declare_indicators()` 收到的收集器。**只记声明，不做计算** ——
    计算发生在框架侧（回测：全帧一次；实盘：当前历史一次），策略无从插手。
    """

    __slots__ = ("_defs", "_names")

    def __init__(self) -> None:
        self._defs: list[_IndicatorDef] = []
        self._names: set[str] = set()

    def add(self, name: str, fn: IndicatorFn, *args, **kwargs) -> None:
        """
        声明一个单输出指标：`fn(frame, *args, **kwargs) -> pd.Series`。

        ::

            spec.add("fast", sma, 10)
            spec.add("atr14", atr, period=14)
        """
        self._register((name,), fn, args, kwargs, multi=False)

    def add_multi(self, names: Sequence[str], fn: IndicatorFn, *args, **kwargs) -> None:
        """
        声明一个多输出指标。`fn` 返回**序列**时按位置绑定 `names`，
        返回**字典**时按键取（键必须与 `names` 同名）。

        ::

            spec.add_multi(("upper", "mid", "lower"), bollinger_bands, 20, 2.0)
            spec.add_multi(("kijun_sen",), ichimoku)     # 字典输出，按键取
        """
        self._register(tuple(names), fn, args, kwargs, multi=True)

    @property
    def definitions(self) -> tuple[_IndicatorDef, ...]:
        return tuple(self._defs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n for d in self._defs for n in d.names)

    def __len__(self) -> int:
        return len(self._defs)

    def _register(
        self, names: tuple[str, ...], fn: IndicatorFn, args: tuple, kwargs: dict, *, multi: bool
    ) -> None:
        if not names:
            raise ValueError("指标声明至少要有一个名字")
        if not callable(fn):
            raise TypeError(f"指标 {names} 的计算函数不可调用: {fn!r}")
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError(f"指标名必须是非空字符串，收到 {name!r}")
            if name in self._names:
                raise ValueError(f"指标名 {name!r} 重复声明")
        self._names.update(names)
        self._defs.append(
            _IndicatorDef(names=names, fn=fn, args=tuple(args), kwargs=dict(kwargs), multi=multi)
        )


def indicator_spec_of(strategy: object) -> IndicatorSpec | None:
    """
    取策略的指标声明；未声明返回 None。

    与 `portfolio_engine._exit_rules_of` 同理走鸭子类型 —— 引擎历史上一直接受
    只实现 `on_bar` 的策略桩，它们没有 `indicator_spec`，一律视为「未声明」
    而不是让回测因缺方法崩掉。
    """
    getter = getattr(strategy, "indicator_spec", None)
    if not callable(getter):
        return None
    return getter()


# ── 计算 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SymbolIndicators:
    """单个标的算完的全部指标。`values` 是与 `index` 等长的 float64 数组。"""

    values: dict[str, np.ndarray]
    index: pd.Index


def _resolve_outputs(defn: _IndicatorDef, raw: object) -> tuple:
    """把指标函数的返回值摊平成与 `defn.names` 一一对应的序列。"""
    if not defn.multi:
        return (raw,)
    if isinstance(raw, Mapping):
        missing = [n for n in defn.names if n not in raw]
        if missing:
            raise StrategyContractError(
                f"指标 {defn.names} 的函数返回字典，但缺少键 {missing}；"
                f"可用键：{sorted(raw)}"
            )
        return tuple(raw[n] for n in defn.names)
    if isinstance(raw, (pd.Series, pd.DataFrame, str)) or not isinstance(raw, Sequence):
        raise StrategyContractError(
            f"指标 {defn.names} 声明为多输出，但函数返回了 {type(raw).__name__}"
        )
    values = tuple(raw)
    if len(values) != len(defn.names):
        raise StrategyContractError(
            f"指标 {defn.names} 声明了 {len(defn.names)} 个输出，函数返回了 {len(values)} 个"
        )
    return values


def _to_float_array(name: str, series: object, frame: pd.DataFrame) -> np.ndarray:
    """
    指标输出 → float64 数组。

    统一成 float64 有两个理由：一是取值路径变成一次数组下标（`Series.iloc` 慢一个
    量级），二是热身期语义收敛到 NaN 一种表示。代价是布尔序列会变成 1.0/0.0 ——
    但布尔类指标（金叉/死叉）本就该用 `crossed_up()`，不该走预算。
    """
    if not isinstance(series, pd.Series):
        raise StrategyContractError(
            f"指标 {name!r} 返回 {type(series).__name__}，预算路径只接受 pd.Series"
        )
    if len(series) != len(frame):
        raise StrategyContractError(
            f"指标 {name!r} 返回长度 {len(series)}，与行情帧长度 {len(frame)} 不一致 —— "
            f"预算路径按**位置**对齐游标，两者必须等长"
        )
    if not series.index.equals(frame.index):
        raise StrategyContractError(
            f"指标 {name!r} 返回的索引与行情帧不一致 —— 预算路径按位置对齐游标，"
            f"索引被改写后取到的值会整体错位"
        )
    try:
        return np.asarray(series.to_numpy(dtype=float, copy=False))
    except (TypeError, ValueError) as exc:
        raise StrategyContractError(
            f"指标 {name!r} 的取值无法转成 float（dtype={series.dtype}）：{exc}"
        ) from exc


def _compute_def(defn: _IndicatorDef, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    raw = defn.fn(frame, *defn.args, **defn.kwargs)
    outputs = _resolve_outputs(defn, raw)
    return {
        name: _to_float_array(name, series, frame)
        for name, series in zip(defn.names, outputs, strict=True)
    }


def _compute_all(spec: IndicatorSpec, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    values: dict[str, np.ndarray] = {}
    for defn in spec.definitions:
        values.update(_compute_def(defn, frame))
    return values


def build_indicator_store(
    spec: IndicatorSpec,
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, SymbolIndicators]:
    """
    在**全量帧**上把所有声明的指标算一次，并做因果性抽检。

    在全帧上算是这项优化的全部意义；不让策略看到未来是 `IndicatorView` 的职责。
    """
    store = {
        symbol: SymbolIndicators(values=_compute_all(spec, frame), index=frame.index)
        for symbol, frame in frames.items()
    }
    probe = _pick_probe_symbol(frames)
    if probe is not None:
        _assert_causal(spec, frames[probe], store[probe], probe)
    return store


def view_from_history(spec: IndicatorSpec | None, history: pd.DataFrame | None) -> IndicatorView | None:
    """
    实盘 / 纸面路径的 `ctx.ind`：在**当前已知历史**上现算。

    实盘拿不到「完整帧」——`history` 本身就是截至当前 bar 的前缀，所以这条路径
    结构上没有前视风险，代价是每根 bar 重算一次（与改造前的成本相同）。
    它存在的理由：改用 `ctx.ind` 的策略必须能在实盘照跑，否则「预算」就成了
    一条只在回测里成立的分叉语义 —— 那比慢更糟。
    """
    if spec is None or history is None or len(history) == 0:
        return None
    return IndicatorView(_compute_all(spec, history), history.index, len(history))


# ── 因果性抽检 ────────────────────────────────────────────────


def _pick_probe_symbol(frames: Mapping[str, pd.DataFrame]) -> str | None:
    """
    抽检只挑**一个**标的。

    指标函数对所有标的是同一个函数，非因果的写法在任何一份数据上都非因果；
    逐标的重算两遍会把这道防线本身变成新的热点。
    """
    for symbol, frame in frames.items():
        if len(frame) >= _MIN_PROBE_BARS:
            return symbol
    return None


def _assert_causal(
    spec: IndicatorSpec, frame: pd.DataFrame, computed: SymbolIndicators, symbol: str
) -> None:
    """
    抽检「用前缀算 == 用全帧算」。

    `sma` / `ema` / `atr` 这类指标在前缀上重算，逐位与全帧一致 —— rolling 与 ewm
    都是从 0 起的前向递推，前 k 步的运算序列完全相同。而 `close.shift(-1)`、
    `close / close.mean()` 这类引用未来的写法，前缀上算出的尾值必然对不上：
    它在全帧里读到的那根 bar，在前缀里还不存在。

    比对的是**整段前缀**而不只是尾值：同样一次计算，顺手多比 N 个点，
    对「尾值碰巧对上」的情况更结实。

    **抽检不是证明。** 前缀切在 k 处只能暴露 `[k-偷看长度, k)` 这一小段的非因果性；
    一个「只在某个固定时间窗里偷看未来」的指标，只要那个窗不压在任何一个抽检点上
    就能躲过去 —— 那正是 `bias_detection.py` 存在的理由，两道防线各管一段。
    """
    n = len(frame)
    for ratio in _CAUSALITY_PROBES:
        cut = max(2, int(n * ratio))
        if cut >= n:
            continue
        prefix = frame.iloc[:cut]
        for defn in spec.definitions:
            for name, arr in _compute_def(defn, prefix).items():
                _assert_prefix_matches(name, arr, computed.values[name][:cut], symbol, cut)


def _assert_prefix_matches(
    name: str, prefix: np.ndarray, full: np.ndarray, symbol: str, cut: int
) -> None:
    """NaN 位置必须一致，非 NaN 位置必须在容差内相等。"""
    prefix_nan = np.isnan(prefix)
    if np.array_equal(prefix_nan, np.isnan(full)) and np.allclose(
        prefix, full, rtol=_CAUSALITY_REL_TOL, atol=_CAUSALITY_ABS_TOL, equal_nan=True
    ):
        return

    at = _first_mismatch(prefix, full)
    raise StrategyContractError(
        f"指标 {name!r} 不是因果指标：{symbol} 只用前 {cut} 根数据重算时，"
        f"第 {at} 个值是 {float(prefix[at])!r}，用全量数据算出的是 {float(full[at])!r}。"
        f"两者不一致说明该指标引用了当前时点之后的数据 —— 预算路径不承载非因果指标，"
        f"否则整份回测都建立在偷看未来之上。"
    )


def _first_mismatch(prefix: np.ndarray, full: np.ndarray) -> int:
    """定位第一个对不上的下标，好让报错指向真正出问题的那根 bar。"""
    differing = ~(
        np.isclose(prefix, full, rtol=_CAUSALITY_REL_TOL, atol=_CAUSALITY_ABS_TOL, equal_nan=True)
    )
    hits = np.flatnonzero(differing)
    return int(hits[0]) if hits.size else len(prefix) - 1


# ── 取值视图 ──────────────────────────────────────────────────


class IndicatorView:
    """
    绑定到**当前游标**的指标视图 —— 策略接触预算指标的唯一入口。

    刻意**不提供任何返回完整序列的方法**：一次算完的均线里装着未来，
    只要策略能拿到整条 Series 就能 `.iloc[-1]` 取到回测结束时的值，
    跑出一条漂亮但完全无效的曲线。这条红线由框架守，不靠策略作者自觉。

    构造时就把持有的数组裁到当前游标（`arr[:stop]` 是 numpy 视图，O(1) 不复制），
    所以「未来」根本没进过这个对象。
    """

    __slots__ = ("_values", "_index")

    def __init__(self, values: Mapping[str, np.ndarray], index: pd.Index, cursor: int) -> None:
        stop = max(0, min(int(cursor), len(index)))
        # ★ 上界裁剪发生在这里，不是在取值时判断一下 —— 见类文档
        self._values = {name: arr[:stop] for name, arr in values.items()}
        self._index = index[:stop]

    # ── 元信息（都是「过去」的计数，不含任何未来取值）──────────

    @property
    def names(self) -> tuple[str, ...]:
        """已声明的指标名。"""
        return tuple(self._values)

    @property
    def bars_seen(self) -> int:
        """
        截至当前时点该标的已出现的 bar 数（== `len(ctx.history)`）。

        给热身期守卫用：`if ctx.ind.bars_seen < period + 1: return`。
        存在的理由是省掉一次 `len(ctx.history)` —— 那会触发一次 DataFrame 切片，
        在一根只剩几微秒预算的 bar 上不是可忽略的开销。
        """
        return len(self._index)

    def has(self, name: str) -> bool:
        return name in self._values

    def __contains__(self, name: str) -> bool:
        return name in self._values

    # ── 取值 ─────────────────────────────────────────────────

    def value(self, name: str, offset: int = 0) -> float | None:
        """
        当前时点（`offset=0`）或往回 `offset` 根的指标值。

        **热身期返回 `None` 而不是 NaN**：NaN 参与比较会静默得到 False，
        把「还没数据」和「条件不成立」混为一谈。返回 None 时任何比较都会炸，
        逼调用方显式处理。

        `offset` 必须 >= 0 —— 负偏移就是取未来值。
        """
        if offset < 0:
            raise ValueError(f"offset 必须 >= 0（收到 {offset}）：负偏移就是取未来值")
        arr = self._array(name)
        i = len(arr) - 1 - offset
        if i < 0:
            return None
        raw = float(arr[i])
        return None if math.isnan(raw) else raw

    def series(self, name: str, n: int) -> pd.Series:
        """
        截至当前时点、往回最多 `n` 个点的窗口。

        上界永远是**当前游标**，不是帧尾 —— 回测跑到一半时这两者完全不同。
        `n` 没有默认值：一个 `series(name)` 的重载就是这条红线的缺口。
        """
        if n <= 0:
            raise ValueError(f"n 必须 > 0（收到 {n}）")
        arr = self._array(name)
        start = max(0, len(arr) - n)
        # copy(): 交出去的是快照，策略改它不会污染后续时点的取值
        return pd.Series(arr[start:].copy(), index=self._index[start:], name=name)

    def crossed_up(self, fast: str, slow: str) -> bool:
        """
        `fast` 在当前 bar 上穿 `slow`（金叉）。

        与 `indicators.crossover(a, b).iloc[-1]` 逐位等价：任一端未就绪时返回
        False —— pandas 里 NaN 的比较结果同样是 False。
        """
        prev_fast, prev_slow = self.value(fast, 1), self.value(slow, 1)
        now_fast, now_slow = self.value(fast), self.value(slow)
        if None in (prev_fast, prev_slow, now_fast, now_slow):
            return False
        return prev_fast < prev_slow and now_fast >= now_slow

    def crossed_down(self, fast: str, slow: str) -> bool:
        """`fast` 在当前 bar 下穿 `slow`（死叉）。等价于 `indicators.crossunder`。"""
        prev_fast, prev_slow = self.value(fast, 1), self.value(slow, 1)
        now_fast, now_slow = self.value(fast), self.value(slow)
        if None in (prev_fast, prev_slow, now_fast, now_slow):
            return False
        return prev_fast > prev_slow and now_fast <= now_slow

    def _array(self, name: str) -> np.ndarray:
        try:
            return self._values[name]
        except KeyError:
            raise KeyError(
                f"未声明的指标 {name!r}；本策略在 declare_indicators() 里声明了：{sorted(self._values)}"
            ) from None

    def __repr__(self) -> str:
        return f"IndicatorView(names={sorted(self._values)}, bars_seen={self.bars_seen})"


class IndicatorBook:
    """
    按标的取 `IndicatorView`，**按需构造**。

    组合回测下 50 标的 × 每时点建 50 个视图是白搭的开销：策略这一时点可能
    只看其中三个。命中缓存按标的记，一个 `IndicatorBook` 的生命周期就是一个时点。
    """

    __slots__ = ("_store", "_cursors", "_cache")

    def __init__(self, store: Mapping[str, SymbolIndicators], cursors: Mapping[str, int]) -> None:
        self._store = store
        self._cursors = cursors
        self._cache: dict[str, IndicatorView] = {}

    def view(self, symbol: str) -> IndicatorView:
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        try:
            indicators = self._store[symbol]
        except KeyError:
            raise KeyError(f"{symbol!r} 不在本次回测的标的内") from None
        view = IndicatorView(indicators.values, indicators.index, self._cursors.get(symbol, 0))
        self._cache[symbol] = view
        return view


class LiveIndicatorBook:
    """
    实盘 / 纸面**组合**路径的 `ctx.ind(symbol)`。

    与回测的 `IndicatorBook` 接口一致，实现换成在当前已知历史上现算 ——
    理由同 `view_from_history`：实盘拿不到完整帧，`histories[symbol]` 本身就是
    截至当前 bar 的前缀，结构上没有前视风险。

    存在的意义是让 `PortfolioStrategyBase` 的预算写法在回测与实盘**同一份代码**，
    否则 `declare_indicators` 就成了一条只在回测里成立的分叉语义。
    """

    __slots__ = ("_spec", "_histories", "_cache")

    def __init__(self, spec: IndicatorSpec, histories: Mapping[str, pd.DataFrame]) -> None:
        self._spec = spec
        self._histories = histories
        self._cache: dict[str, IndicatorView] = {}

    def view(self, symbol: str) -> IndicatorView:
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        history = self._histories[symbol]
        view = IndicatorView(_compute_all(self._spec, history), history.index, len(history))
        self._cache[symbol] = view
        return view


class IndicatorProvider(Protocol):
    """`PortfolioContext.indicators` 的形状：回测装 `IndicatorBook`，实盘装 `LiveIndicatorBook`。"""

    def view(self, symbol: str) -> IndicatorView: ...
