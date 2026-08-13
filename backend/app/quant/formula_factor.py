"""
公式化因子引擎（Formula Factor Engine）

灵感来自 AlphaGPT（refs/AlphaGPT/model_core）的符号化因子挖掘思想：
用「基础特征 + 算子」组合出自定义 alpha 因子表达式，通过栈式虚拟机（RPN
逆波兰表达式）执行。相比固定因子，用户可自由构造并测试 alpha 表达式。

与 AlphaGPT 的差异：
- AlphaGPT 用 PyTorch + 强化学习自动搜索公式；本模块用 pandas 手工构造，
  避免在 Web 平台引入训练依赖，专注「表达式即因子」的可组合能力。
- 算子移植自 AlphaGPT ops.py（GATE/JUMP/DECAY 等），并补充截面 RANK、
  时序 ZSCORE、TS_MEAN 等经典 alpha 算子。

RPN 示例：
  动量除以波动率  → ["MOM20", "ATR_RATIO", "DIV"]
  RSI 门控动量    → ["RSI14", "MOM20", "ZERO", "GATE"]（RSI>0 时取动量否则取0）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.cross_section import (
    cs_apply,
    cs_demean,
    cs_mean,
    cs_rank,
    cs_scale,
    cs_std,
    cs_zscore,
    validate_panel_index,
)
from app.quant.factor_lib.operators import (
    ema as _op_ema,
)
from app.quant.factor_lib.operators import (
    rolling_corr,
    rolling_cov,
    rolling_idxmax,
    rolling_idxmin,
    rolling_mad,
    rolling_quantile,
    rolling_resi,
    rolling_rsquare,
    rolling_slope,
)
from app.quant.factor_lib.operators import (
    wma as _op_wma,
)
from app.quant.indicators import (
    adx,
    atr,
    bollinger_bands,
    macd,
    mfi,
    obv,
    rsi,
    sma,
)

# ── 基础特征（叶子节点）─────────────────────────────────────────
# 每个特征是 df -> pd.Series 的纯函数

def _ret1(df: pd.DataFrame) -> pd.Series:
    return df["close"].pct_change(1)


def _feature_map() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    return {
        # 收益/动量
        "RET1":       _ret1,
        "MOM5":       lambda df: df["close"].pct_change(5),
        "MOM20":      lambda df: df["close"].pct_change(20),
        # 振荡指标
        "RSI14":      lambda df: (rsi(df, 14) - 50) / 50,          # 归一化到 [-1,1]
        "RSI21":      lambda df: (rsi(df, 21) - 50) / 50,
        "MACD_HIST":  lambda df: macd(df)[2],
        "ADX14":      lambda df: adx(df, 14) / 100,
        "MFI14":      lambda df: (mfi(df, 14) - 50) / 50,
        # 均值回归
        "BB_POS":     _bb_position,
        "PX_SMA20":   lambda df: df["close"] / sma(df, 20).replace(0, np.nan) - 1,
        # 波动率
        "ATR_RATIO":  lambda df: atr(df, 14) / df["close"].replace(0, np.nan),
        "HL_RANGE":   lambda df: (df["high"] - df["low"]) / df["close"].replace(0, np.nan),
        # 成交量
        "LOG_VOL":    lambda df: np.log1p(df["volume"]),
        "VOL_CHG":    _volume_change,
        "OBV_MOM":    lambda df: obv(df).pct_change(20),
        # 常量
        "ZERO":       lambda df: pd.Series(0.0, index=df.index),
        "ONE":        lambda df: pd.Series(1.0, index=df.index),
    }


def _bb_position(df: pd.DataFrame) -> pd.Series:
    upper, _, lower = bollinger_bands(df, 20)
    width = (upper - lower).replace(0, np.nan)
    return (df["close"] - lower) / width


def _volume_change(df: pd.DataFrame) -> pd.Series:
    avg = df["volume"].rolling(20).mean().replace(0, np.nan)
    return df["volume"] / avg - 1


# ── 算子（内部节点）─────────────────────────────────────────────
# 移植自 AlphaGPT ops.py，改写为 pandas 实现

_EPS = 1e-9


def _ts_delay(x: pd.Series, d: int) -> pd.Series:
    return x.shift(d)


def _op_gate(cond: pd.Series, x: pd.Series, y: pd.Series) -> pd.Series:
    """cond > 0 时取 x，否则取 y（AlphaGPT GATE）。"""
    mask = (cond > 0).astype(float)
    return mask * x + (1.0 - mask) * y


def _op_jump(x: pd.Series) -> pd.Series:
    """截面 z-score 超过 3σ 的跳变部分（AlphaGPT JUMP）。"""
    mean = x.mean()
    std = x.std() + _EPS
    z = (x - mean) / std
    return (z - 3.0).clip(lower=0.0)


def _op_decay(x: pd.Series) -> pd.Series:
    """线性衰减加权（AlphaGPT DECAY）：x + 0.8·x[-1] + 0.6·x[-2]。"""
    return x + 0.8 * _ts_delay(x, 1) + 0.6 * _ts_delay(x, 2)


def _op_rank(x: pd.Series) -> pd.Series:
    """滚动分位排名（经典 alpha 截面 rank 的**时序近似**，非真截面）。

    真正的「同一时刻跨标的排名」请用 `CS_RANK`（需 panel 模式，见
    `evaluate_formula_panel`）。本算子保留原样是为了兼容既有公式与已保存策略。
    """
    return x.rolling(60, min_periods=10).apply(
        lambda w: (w.argsort().argsort()[-1] + 1) / len(w), raw=False
    )


def _op_zscore(x: pd.Series) -> pd.Series:
    """滚动 60 期 z-score 标准化。"""
    mean = x.rolling(60, min_periods=10).mean()
    std = x.rolling(60, min_periods=10).std() + _EPS
    return (x - mean) / std


def _op_tsmean5(x: pd.Series) -> pd.Series:
    return x.rolling(5, min_periods=1).mean()


@dataclass(frozen=True)
class OpSpec:
    name: str
    func: Callable
    arity: int
    label: str
    group: str


OPS: list[OpSpec] = [
    OpSpec("ADD",    lambda x, y: x + y,               2, "相加 x+y",        "算术"),
    OpSpec("SUB",    lambda x, y: x - y,               2, "相减 x-y",        "算术"),
    OpSpec("MUL",    lambda x, y: x * y,               2, "相乘 x·y",        "算术"),
    OpSpec("DIV",    lambda x, y: x / (y + _EPS),      2, "相除 x/y",        "算术"),
    OpSpec("NEG",    lambda x: -x,                     1, "取负 -x",         "算术"),
    OpSpec("ABS",    lambda x: x.abs(),                1, "绝对值 |x|",      "算术"),
    OpSpec("SIGN",   lambda x: np.sign(x),             1, "符号 sign(x)",    "算术"),
    OpSpec("GATE",   _op_gate,                         3, "门控 c>0?x:y",    "逻辑"),
    OpSpec("JUMP",   _op_jump,                         1, "跳变检测",        "统计"),
    OpSpec("DECAY",  _op_decay,                        1, "线性衰减加权",     "时序"),
    OpSpec("DELAY1", lambda x: _ts_delay(x, 1),        1, "滞后1期",         "时序"),
    OpSpec("TS_MEAN5", _op_tsmean5,                    1, "5期均值",         "时序"),
    # 分类是「时序」而非「截面」：实现就是 rolling(60) 的时序近似（见 _op_rank）。
    # 早期把它标成「截面」是错的，名不副实的标签会误导用户选错算子。
    OpSpec("RANK",   _op_rank,                         1, "滚动分位排名(时序近似)", "时序"),
    OpSpec("ZSCORE", _op_zscore,                       1, "滚动Z标准化",      "统计"),
]

# ── 带窗口的扩展算子（B3，移植自 qlib data/ops.py 定义）─────────────
# RPN token 为固定名，故把窗口烘焙进算子名（如 SLOPE10 / CORR20）。
# 复用 factor_lib/operators.py 的原语，与声明式因子库共享同一份实现（DRY）。

_OP_WINDOWS: tuple[int, ...] = (10, 20)


def _windowed_unary_ops() -> list[OpSpec]:
    ops: list[OpSpec] = []
    for w in _OP_WINDOWS:
        ops += [
            OpSpec(f"SLOPE{w}", lambda x, n=w: rolling_slope(x, n),        1, f"{w}期回归斜率",   "回归"),
            OpSpec(f"RSQR{w}",  lambda x, n=w: rolling_rsquare(x, n),      1, f"{w}期回归拟合度", "回归"),
            OpSpec(f"RESI{w}",  lambda x, n=w: rolling_resi(x, n),         1, f"{w}期回归残差",   "回归"),
            OpSpec(f"WMA{w}",   lambda x, n=w: _op_wma(x, n),             1, f"{w}期加权均值",   "时序"),
            OpSpec(f"EMA{w}",   lambda x, n=w: _op_ema(x, n),             1, f"{w}期指数均值",   "时序"),
            OpSpec(f"MAD{w}",   lambda x, n=w: rolling_mad(x, n),          1, f"{w}期平均偏差",   "统计"),
            OpSpec(f"QTLU{w}",  lambda x, n=w: rolling_quantile(x, n, 0.8), 1, f"{w}期80%分位",  "分位"),
            OpSpec(f"QTLD{w}",  lambda x, n=w: rolling_quantile(x, n, 0.2), 1, f"{w}期20%分位",  "分位"),
            OpSpec(f"IMAX{w}",  lambda x, n=w: rolling_idxmax(x, n) / n,   1, f"{w}期最大值位置", "位置"),
            OpSpec(f"IMIN{w}",  lambda x, n=w: rolling_idxmin(x, n) / n,   1, f"{w}期最小值位置", "位置"),
        ]
    return ops


def _windowed_binary_ops() -> list[OpSpec]:
    ops: list[OpSpec] = []
    for w in _OP_WINDOWS:
        ops += [
            OpSpec(f"CORR{w}", lambda x, y, n=w: rolling_corr(x, y, n), 2, f"{w}期相关系数", "量价"),
            OpSpec(f"COV{w}",  lambda x, y, n=w: rolling_cov(x, y, n),  2, f"{w}期协方差",   "量价"),
        ]
    return ops


OPS += _windowed_unary_ops() + _windowed_binary_ops()

_OP_BY_NAME = {op.name: op for op in OPS}


# ── 截面算子（M2，仅 panel 模式可用）─────────────────────────────────
# 刻意**不并入 `OPS`**：`OPS` 是单标的路径的词表，遗传挖掘
# （`mining/expression_tree._vocab`）直接以它为搜索空间。把只在 panel 下
# 有意义的算子混进去，会让挖掘生成一堆在单标的路径上必然报错的公式。

CS_OPS: list[OpSpec] = [
    OpSpec("CS_RANK",   cs_rank,   1, "截面分位排名(0,1]", "截面"),
    OpSpec("CS_ZSCORE", cs_zscore, 1, "截面Z标准化",       "截面"),
    OpSpec("CS_DEMEAN", cs_demean, 1, "截面去均值",        "截面"),
    OpSpec("CS_SCALE",  cs_scale,  1, "截面缩放Σ|x|=1",    "截面"),
    OpSpec("CS_MEAN",   cs_mean,   1, "截面均值(广播)",     "截面"),
    OpSpec("CS_STD",    cs_std,    1, "截面标准差(广播)",   "截面"),
]

#: panel 模式下可用的完整词表 = 时序算子 + 截面算子
PANEL_OPS: list[OpSpec] = OPS + CS_OPS
_PANEL_OP_BY_NAME = {op.name: op for op in PANEL_OPS}
_CS_OP_NAMES = frozenset(op.name for op in CS_OPS)


def formula_requires_panel(tokens: list[str]) -> bool:
    """公式是否含截面算子（含则必须走 `evaluate_formula_panel`）。"""
    return any(tok in _CS_OP_NAMES for tok in tokens)


# ── 栈式虚拟机（RPN 执行器）─────────────────────────────────────

#: 公式 token 数硬上限
MAX_TOKENS: int = 32


class FormulaError(ValueError):
    """公式非法或求值失败。"""


def _validate_tokens(tokens: list[str]) -> None:
    """两条路径共用的 token 数量前置校验。"""
    if not tokens:
        raise FormulaError("公式为空")
    if len(tokens) > MAX_TOKENS:
        raise FormulaError(f"公式过长（最多 {MAX_TOKENS} 个 token）")


def evaluate_formula(df: pd.DataFrame, tokens: list[str]) -> pd.Series:
    """
    执行 RPN（逆波兰）公式 token 列表，返回因子值序列。

    Parameters
    ----------
    df     : OHLCV DataFrame
    tokens : RPN token 列表，如 ["MOM20", "ATR_RATIO", "DIV"]

    Raises
    ------
    FormulaError : token 非法 / 栈不平衡 / 求值异常 / 公式含截面算子
    """
    _validate_tokens(tokens)
    if formula_requires_panel(tokens):
        used = sorted({t for t in tokens if t in _CS_OP_NAMES})
        raise FormulaError(
            f"公式含截面算子 {', '.join(used)}，单标的帧上没有截面可言。"
            f"请改用 evaluate_formula_panel(panel, tokens)（panel 为 "
            f"(datetime, instrument) 双层索引面板，见 app/quant/panel.py）"
        )

    features = _feature_map()
    stack: list[pd.Series] = []

    for tok in tokens:
        if tok in features:
            stack.append(features[tok](df).astype(float))
        elif tok in _OP_BY_NAME:
            op = _OP_BY_NAME[tok]
            if len(stack) < op.arity:
                raise FormulaError(f"算子 {tok} 需要 {op.arity} 个操作数，栈中只有 {len(stack)} 个")
            args = [stack.pop() for _ in range(op.arity)]
            args.reverse()
            try:
                res = op.func(*args)
            except Exception as e:
                raise FormulaError(f"算子 {tok} 求值失败: {e}") from e
            if not isinstance(res, pd.Series):
                res = pd.Series(res, index=df.index)
            res = res.replace([np.inf, -np.inf], np.nan)
            stack.append(res)
        else:
            raise FormulaError(f"未知 token: {tok}（既非特征也非算子）")

    if len(stack) != 1:
        raise FormulaError(f"公式不平衡：执行完毕后栈中剩余 {len(stack)} 个值（应为 1 个）")

    return stack[0]


# ── 面板（panel）求值路径 ───────────────────────────────────────
# 与 `evaluate_formula` 是**两条独立路径**，刻意不合并：
# 一个函数按入参形态分叉行为，是最容易出错的设计，而 `evaluate_formula`
# 的调用方（挖掘、适应度、AlphaModel、API）众多，任何行为分叉都代价高昂。

_PANEL_REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def _validate_panel(panel: pd.DataFrame) -> None:
    """面板必须是带 OHLCV 列的 (datetime, instrument) 双层索引帧。"""
    if not isinstance(panel, pd.DataFrame):
        raise FormulaError(f"panel 必须是 DataFrame，实得 {type(panel).__name__}")
    try:
        validate_panel_index(panel.index)
    except ValueError as e:
        raise FormulaError(str(e)) from e
    missing = [c for c in _PANEL_REQUIRED_COLUMNS if c not in panel.columns]
    if missing:
        raise FormulaError(f"面板缺少必需列: {', '.join(missing)}")


def _per_instrument(func: Callable, args: list[pd.Series]) -> pd.Series:
    """逐标的执行时序算子/特征：切出该标的的时间序列 → 求值 → 拼回面板。"""
    frame = pd.concat(args, axis=1, keys=range(len(args)))
    parts: list[pd.Series] = []
    for _, sub in frame.groupby(level="instrument", sort=False):
        flat = sub.droplevel("instrument")
        res = func(*(flat[col] for col in flat.columns))
        if not isinstance(res, pd.Series):
            res = pd.Series(res, index=flat.index)
        res = pd.Series(res.to_numpy(dtype=float), index=sub.index)
        parts.append(res)
    return pd.concat(parts) if parts else pd.Series(dtype=float)


def _panel_feature(panel: pd.DataFrame, fn: Callable[[pd.DataFrame], pd.Series]) -> pd.Series:
    """逐标的计算基础特征（特征函数吃的是单标的 OHLCV 帧）。"""
    parts: list[pd.Series] = []
    for _, sub in panel.groupby(level="instrument", sort=False):
        flat = sub.droplevel("instrument")
        values = pd.Series(fn(flat), index=flat.index).astype(float)
        parts.append(pd.Series(values.to_numpy(dtype=float), index=sub.index))
    return pd.concat(parts) if parts else pd.Series(dtype=float)


def evaluate_formula_panel(panel: pd.DataFrame, tokens: list[str]) -> pd.Series:
    """
    在面板上执行 RPN 公式，返回 (datetime, instrument) 双层索引的因子序列。

    - **时序算子与基础特征**逐标的分组计算（与单标的路径同一份实现）。
    - **CS_\\* 截面算子**按 datetime 分组跨标的计算（见 `app/quant/cross_section.py`）。

    Parameters
    ----------
    panel  : (datetime, instrument) 双层索引面板，列含 OHLCV（见 `app/quant/panel.py`）
    tokens : RPN token 列表，如 ["MOM20", "CS_RANK"]

    Raises
    ------
    FormulaError : 面板形态非法 / token 非法 / 栈不平衡 / 求值异常
    """
    _validate_tokens(tokens)
    _validate_panel(panel)

    # 分组算子依赖组内时间有序；面板来源多样，这里统一排序后再算，
    # 最终按调用方传入的原索引对齐返回（不改变调用方看到的行顺序）。
    ordered = panel.sort_index()
    features = _feature_map()
    stack: list[pd.Series] = []

    for tok in tokens:
        if tok in features:
            stack.append(_panel_feature(ordered, features[tok]))
            continue
        op = _PANEL_OP_BY_NAME.get(tok)
        if op is None:
            raise FormulaError(f"未知 token: {tok}（既非特征也非算子）")
        if len(stack) < op.arity:
            raise FormulaError(f"算子 {tok} 需要 {op.arity} 个操作数，栈中只有 {len(stack)} 个")
        args = [stack.pop() for _ in range(op.arity)]
        args.reverse()
        stack.append(_eval_panel_op(op, args))

    if len(stack) != 1:
        raise FormulaError(f"公式不平衡：执行完毕后栈中剩余 {len(stack)} 个值（应为 1 个）")

    return stack[0].reindex(panel.index)


def _eval_panel_op(op: OpSpec, args: list[pd.Series]) -> pd.Series:
    """执行单个 panel 算子：截面算子整截面算，其余逐标的算。"""
    try:
        if op.name in _CS_OP_NAMES:
            res = cs_apply(args[0], op.func)
        else:
            res = _per_instrument(op.func, args)
    except Exception as e:
        raise FormulaError(f"算子 {op.name} 求值失败: {e}") from e
    return res.replace([np.inf, -np.inf], np.nan)


# ── 元数据（供前端构建器）─────────────────────────────────────

FEATURE_META = [
    {"name": "RET1",      "label": "1日收益",        "group": "动量"},
    {"name": "MOM5",      "label": "5日动量",        "group": "动量"},
    {"name": "MOM20",     "label": "20日动量",       "group": "动量"},
    {"name": "RSI14",     "label": "RSI(14)",       "group": "振荡"},
    {"name": "RSI21",     "label": "RSI(21)",       "group": "振荡"},
    {"name": "MACD_HIST", "label": "MACD柱",        "group": "振荡"},
    {"name": "ADX14",     "label": "ADX(14)",       "group": "振荡"},
    {"name": "MFI14",     "label": "MFI(14)",       "group": "振荡"},
    {"name": "BB_POS",    "label": "布林带位置",      "group": "均值回归"},
    {"name": "PX_SMA20",  "label": "价格/SMA20偏离", "group": "均值回归"},
    {"name": "ATR_RATIO", "label": "ATR比率",        "group": "波动率"},
    {"name": "HL_RANGE",  "label": "高低价幅",        "group": "波动率"},
    {"name": "LOG_VOL",   "label": "对数成交量",      "group": "成交量"},
    {"name": "VOL_CHG",   "label": "成交量变化",      "group": "成交量"},
    {"name": "OBV_MOM",   "label": "OBV动量",        "group": "成交量"},
    {"name": "ZERO",      "label": "常量0",          "group": "常量"},
    {"name": "ONE",       "label": "常量1",          "group": "常量"},
]

OP_META = [
    {
        "name": op.name,
        "label": op.label,
        "arity": op.arity,
        "group": op.group,
        # 截面算子只在 panel 模式可用；前端据此提示「该公式需多标的截面」
        "requires_panel": op.name in _CS_OP_NAMES,
    }
    for op in PANEL_OPS
]

# 预设公式示例（RPN），帮助用户上手
PRESET_FORMULAS = [
    {
        "name": "动量/波动率",
        "tokens": ["MOM20", "ATR_RATIO", "DIV"],
        "desc": "风险调整动量：20日动量除以波动率，高动量+低波动得分高",
    },
    {
        "name": "RSI门控动量",
        "tokens": ["RSI14", "MOM20", "ZERO", "GATE"],
        "desc": "RSI>0（强势）时取动量，否则取0，过滤弱势信号",
    },
    {
        "name": "衰减动量",
        "tokens": ["MOM5", "DECAY"],
        "desc": "对5日动量做线性衰减加权，平滑短期噪声",
    },
    {
        "name": "标准化反转",
        "tokens": ["RET1", "NEG", "ZSCORE"],
        "desc": "1日收益取负后标准化，捕捉短期均值回归",
    },
    {
        "name": "量价背离",
        "tokens": ["MOM20", "VOL_CHG", "MUL"],
        "desc": "动量与成交量变化相乘，量价共振信号更强",
    },
    {
        "name": "布林带排名",
        "tokens": ["BB_POS", "RANK"],
        "desc": "布林带位置的滚动分位排名，识别相对超买超卖",
    },
]

# 需要多标的面板的预设公式（截面型）。**刻意与 PRESET_FORMULAS 分开**：
# 后者服务于单标的公式分析页，混进 CS_* 会让用户一点就报错。
PANEL_PRESET_FORMULAS = [
    {
        "name": "截面动量排名",
        "tokens": ["MOM20", "CS_RANK"],
        "desc": "同一时刻跨标的的动量排名，选股型 alpha 的基本形态",
    },
    {
        "name": "截面反转",
        "tokens": ["RET1", "NEG", "CS_ZSCORE"],
        "desc": "1日收益取负后做截面标准化，多空对冲的经典短周期反转",
    },
]
